"""Async adapters for official hosted APIs and a local Ollama endpoint."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping, Optional, cast
from urllib.parse import quote, urlsplit

from anthropic import AsyncAnthropic
from google import genai
from google.genai import types
from openai import AsyncOpenAI

from src.ai_access import ai_disabled, parse_ai_access_mode
from src.budget import BudgetError, BudgetExceeded, BudgetLedger, BudgetPolicy
from src.config import _bool, _openai_reasoning_effort

logger = logging.getLogger(__name__)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _web_action_counts(output_items: Any) -> dict[str, int]:
    """Classify web actions without exposing provider payloads in diagnostics."""
    counts = {"search": 0, "open_page": 0, "find_in_page": 0, "unknown": 0, "missing": 0}
    for item in output_items or []:
        if _field(item, "type", "") != "web_search_call":
            continue
        action = _field(item, "action")
        action_type = _field(action, "type") if action is not None else None
        if action_type is None:
            counts["missing"] += 1
        elif action_type == "search":
            counts["search"] += 1
        elif action_type == "open_page":
            counts["open_page"] += 1
        elif action_type == "find_in_page":
            counts["find_in_page"] += 1
        else:
            counts["unknown"] += 1
    return counts


def _safe_response_id(response: Any) -> str:
    """Return only a tightly validated response ID for diagnostics."""
    value = _field(response, "id")
    if (
        isinstance(value, str)
        and value.startswith("resp_")
        and value.isascii()
        and 6 <= len(value) <= 80
        and all(character.isalnum() or character in "_-" for character in value)
    ):
        return value
    return "unavailable"


def _safe_response_status(response: Any) -> str:
    value = _field(response, "status")
    allowed = {"completed", "incomplete", "failed", "queued", "in_progress", "unknown"}
    return value if isinstance(value, str) and value in allowed else "unknown"


def _safe_usage_count(usage: Any, field_name: str) -> int | str:
    value = _field(usage, field_name)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return "unknown"


class ProviderType(Enum):
    """Provider identifiers. FREE is retained only to explain old config."""

    GEMINI = "gemini"
    OPENAI = "openai"
    CLAUDE = "claude"
    GROK = "grok"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    FREE = "free"


class ProviderError(RuntimeError):
    """Sanitized provider diagnostic; Discord renders a generic public message."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ImageInput:
    """Image bytes attached to the final user message."""

    data: bytes
    mime_type: str


def _web_citation_text(response: Any, fallback: str) -> str:
    """Turn Responses URL annotations into clickable, inline Discord citations."""
    rendered: list[str] = []
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", "") != "output_text":
                continue
            text = getattr(part, "text", "")
            replacements: dict[tuple[int, int], list[str]] = {}
            extra: list[str] = []
            for annotation in getattr(part, "annotations", None) or []:
                if getattr(annotation, "type", "") != "url_citation":
                    continue
                url = getattr(annotation, "url", "")
                try:
                    parsed = urlsplit(url)
                except ValueError:
                    continue
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    continue
                title = getattr(annotation, "title", "") or parsed.netloc
                title = " ".join(title.split())
                if len(title) > 120:
                    title = title[:117] + "..."
                for character in ("\\", "[", "]", "*", "_", "`"):
                    title = title.replace(character, "\\" + character)
                escaped_url = quote(url, safe=":/?#[]@!$&'*+,;=%-._~")
                link = f"[{title}](<{escaped_url}>)"
                if len(link) > 1900:
                    link = f"{title} (citation URL too long to display)"
                start = getattr(annotation, "start_index", None)
                end = getattr(annotation, "end_index", None)
                if (
                    isinstance(start, int)
                    and isinstance(end, int)
                    and 0 <= start <= end <= len(text)
                ):
                    replacements.setdefault((start, end), []).append(link)
                else:
                    extra.append(link)
            boundary = len(text)
            for (start, end), links in sorted(replacements.items(), reverse=True):
                if end > boundary:
                    extra.extend(links)
                    continue
                text = text[:start] + " ".join(dict.fromkeys(links)) + text[end:]
                boundary = start
            if extra:
                text += "\n\nSources: " + " · ".join(dict.fromkeys(extra))
            rendered.append(text)
    return "\n".join(rendered) if rendered else fallback


def _compatible_messages(
    messages: list[dict[str, Any]], images: tuple[ImageInput, ...]
) -> list[dict[str, Any]]:
    """Build OpenAI Chat Completions multimodal content without mutating input."""
    copied = [dict(message) for message in messages]
    if not images:
        return copied
    user_index = next(
        (i for i in range(len(copied) - 1, -1, -1) if copied[i].get("role") == "user"), None
    )
    if user_index is None:
        raise ProviderError("Images need a user message to attach to.")
    message = dict(copied[user_index])
    text = str(message.get("content", ""))
    message["content"] = ([{"type": "text", "text": text}] if text else []) + [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{image.mime_type};base64,{base64.b64encode(image.data).decode('ascii')}"
            },
        }
        for image in images
    ]
    copied[user_index] = message
    return copied


@dataclass(frozen=True)
class CompletionResult:
    text: str
    provider: ProviderType
    model: str
    attempted: tuple[ProviderType, ...]


@dataclass
class ModelInfo:
    name: str
    provider: ProviderType
    description: str = ""
    supports_vision: bool = False
    supports_image_generation: bool = False


def _safe_error(provider: str, error: BaseException) -> ProviderError:
    """Map SDK errors to concise messages without echoing their raw payloads."""
    if (
        isinstance(error, (TimeoutError, asyncio.TimeoutError))
        or "timeout" in type(error).__name__.lower()
    ):
        return ProviderError(
            f"{provider} did not respond before the request timed out. Please try again.",
            retryable=True,
        )
    raw_status = getattr(error, "status_code", None) or getattr(error, "status", None)
    raw_code = getattr(error, "code", None)
    status = raw_status or (raw_code if isinstance(raw_code, int) else None)
    if status is None and isinstance(raw_code, str) and raw_code.isdigit():
        status = int(raw_code)
    code = str(raw_code or "").lower()
    kind = type(error).__name__.lower()
    hint = f"{kind} {code} {status}".lower()
    if status == 401 or status == 403 or "auth" in hint or "api_key" in hint:
        return ProviderError(f"{provider} authentication failed. Check the configured API key.")
    if status == 404 or "notfound" in kind or "not_found" in hint:
        return ProviderError(
            f"{provider} could not find the requested model or endpoint. Check the model setting."
        )
    if status == 429 or "ratelimit" in kind or "rate_limit" in hint:
        return ProviderError(
            f"{provider} is rate limited. Please wait a moment and try again.", retryable=True
        )
    if "quota" in hint or "resource_exhausted" in hint:
        return ProviderError(
            f"{provider} quota is unavailable or exhausted. Check the provider account limits.",
            retryable=True,
        )
    if (
        status == 408
        or (isinstance(status, int) and status >= 500)
        or any(word in hint for word in ("overloaded", "unavailable", "connection", "temporarily"))
    ):
        return ProviderError(
            f"{provider} is temporarily unavailable. Please try again.", retryable=True
        )
    return ProviderError(
        f"{provider} request failed. Please try again or check the provider status and configuration."
    )


async def _bounded(awaitable: Any, timeout: Optional[float], provider: str) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout or 60.0)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if isinstance(error, ProviderError):
            raise
        logger.warning("%s request failed (%s)", provider, type(error).__name__)
        raise _safe_error(provider, error) from None


class BaseProvider(ABC):
    def __init__(self, api_key: Optional[str] = None, *, default_model: str = ""):
        self.api_key = api_key
        self.default_model = default_model

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Return a single assistant response for the supplied conversation."""

    @abstractmethod
    async def generate_image(
        self, prompt: str, model: Optional[str] = None, **kwargs: Any
    ) -> str | bytes:
        """Generate an image URL or image bytes when the backend supports it."""

    @abstractmethod
    def get_available_models(self) -> list[ModelInfo]:
        """Return representative model IDs for this provider."""

    @abstractmethod
    def supports_image_generation(self) -> bool:
        """Whether image generation is implemented for this provider."""

    def supports_vision(self) -> bool:
        return any(model.supports_vision for model in self.get_available_models())

    async def close(self) -> None:
        client = getattr(self, "client", None)
        if client is not None and hasattr(client, "close"):
            await client.close()


class GeminiProvider(BaseProvider):
    """Official Google Gen AI SDK, using one async generate_content call."""

    def __init__(self, api_key: str, *, default_model: str = "gemini-3.5-flash-lite"):
        super().__init__(api_key, default_model=default_model)
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=60_000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )

    async def chat_completion(
        self, messages: list[dict[str, Any]], model: Optional[str] = None, **kwargs: Any
    ) -> str:
        system_parts: list[str] = []
        contents: list[Any] = []
        last_user_index = next(
            (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
            None,
        )
        for message_index, message in enumerate(messages):
            role = message.get("role")
            content = message.get("content", "")
            if role == "system":
                system_parts.append(str(content))
            elif role in ("user", "assistant", "model"):
                parts = [types.Part(text=str(content))]
                if role == "user" and message_index == last_user_index:
                    parts.extend(
                        types.Part(
                            inline_data=types.Blob(mime_type=image.mime_type, data=image.data)
                        )
                        for image in kwargs.get("images", ())
                    )
                contents.append(
                    types.Content(
                        role="model" if role in ("assistant", "model") else "user",
                        parts=parts,
                    )
                )
        if not contents:
            raise ProviderError("Gemini needs at least one user or assistant message.")
        config_args: dict[str, Any] = {}
        if system_parts:
            config_args["system_instruction"] = "\n\n".join(system_parts)
        if kwargs.get("max_tokens") is not None:
            config_args["max_output_tokens"] = kwargs["max_tokens"]
        for key in ("temperature", "top_p", "top_k"):
            if kwargs.get(key) is not None:
                config_args[key] = kwargs[key]
        config = types.GenerateContentConfig(**config_args) if config_args else None
        timeout = kwargs.get("request_timeout")
        response = await _bounded(
            self.client.aio.models.generate_content(
                model=model or self.default_model, contents=contents, config=config
            ),
            timeout,
            "Gemini",
        )
        text_parts: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                if not getattr(part, "thought", False) and getattr(part, "text", None):
                    text_parts.append(part.text)
        text: Optional[str] = "".join(text_parts)
        if not text:
            try:
                text = getattr(response, "text", None)
            except Exception:
                text = None
        if text:
            return text
        prompt_feedback = getattr(response, "prompt_feedback", None)
        blocked_finish = any(
            "safety" in str(getattr(candidate, "finish_reason", "")).lower()
            or "block" in str(getattr(candidate, "finish_reason", "")).lower()
            for candidate in getattr(response, "candidates", None) or []
        )
        if prompt_feedback or blocked_finish:
            raise ProviderError(
                "Gemini blocked this request. Please revise the prompt and try again."
            )
        raise ProviderError("Gemini returned no text for this request.")

    async def generate_image(
        self, prompt: str, model: Optional[str] = None, **kwargs: Any
    ) -> str | bytes:
        raise NotImplementedError("Gemini image generation is not enabled in this bot.")

    def get_available_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                self.default_model,
                ProviderType.GEMINI,
                "Configured Gemini model",
                supports_vision=True,
            )
        ]

    def supports_image_generation(self) -> bool:
        return False

    async def close(self) -> None:
        await self.client.aio.aclose()


class OpenAIProvider(BaseProvider):
    def __init__(
        self,
        api_key: str,
        *,
        default_model: str = "gpt-6-luna",
        reasoning_effort: Optional[str] = None,
        base_url: Optional[str] = None,
        enable_image_generation: bool = False,
        budget: Optional[BudgetLedger] = None,
    ):
        super().__init__(api_key, default_model=default_model)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=60.0)
        self._image_generation_enabled = enable_image_generation
        self._budget = budget
        self._budget_contract_blocked = False
        self._reasoning_effort = _openai_reasoning_effort(
            {"OPENAI_REASONING_EFFORT": reasoning_effort or ""}
        )

    async def chat_completion(
        self, messages: list[dict[str, Any]], model: Optional[str] = None, **kwargs: Any
    ) -> str:
        if self._budget is not None:
            return await self._budgeted_chat_completion(messages, model=model, **kwargs)
        request_messages = [dict(message) for message in messages]
        images: tuple[ImageInput, ...] = tuple(kwargs.get("images", ()))
        if images:
            last_user = next(
                (
                    index
                    for index in range(len(request_messages) - 1, -1, -1)
                    if request_messages[index].get("role") == "user"
                ),
                None,
            )
            if last_user is None:
                raise ProviderError("Images need a user message to attach to.")
            message = dict(request_messages[last_user])
            text = str(message.get("content", ""))
            message["content"] = [{"type": "input_text", "text": text}]
            message["content"].extend(
                {
                    "type": "input_image",
                    "image_url": f"data:{image.mime_type};base64,{base64.b64encode(image.data).decode('ascii')}",
                }
                for image in images
            )
            request_messages[last_user] = message
        request: dict[str, Any] = {
            "model": model or self.default_model,
            "input": request_messages,
            "store": False,
        }
        if kwargs.get("max_tokens") is not None:
            request["max_output_tokens"] = kwargs["max_tokens"]
        if "reasoning_requested" in kwargs:
            if not isinstance(kwargs["reasoning_requested"], bool):
                raise ProviderError("reasoning_requested must be a boolean.")
            request["reasoning"] = {"effort": "low" if kwargs["reasoning_requested"] else "none"}
        elif self._reasoning_effort is not None:
            request["reasoning"] = {"effort": self._reasoning_effort}
        if kwargs.get("web_search", False):
            request["tools"] = [{"type": "web_search", "search_context_size": "low"}]
            request["max_tool_calls"] = 1
        for key in ("temperature", "top_p"):
            if kwargs.get(key) is not None:
                request[key] = kwargs[key]
        if kwargs.get("request_timeout") is not None:
            request["timeout"] = kwargs["request_timeout"]
        response = await _bounded(
            self.client.responses.create(**request), kwargs.get("request_timeout"), "OpenAI"
        )
        output = getattr(response, "output_text", None)
        if output:
            return _web_citation_text(response, output)
        output_items = getattr(response, "output", None) or []
        refused = any(
            getattr(item, "type", "") == "refusal"
            or any(
                getattr(part, "type", "") == "refusal"
                for part in (getattr(item, "content", None) or [])
            )
            for item in output_items
        )
        if refused:
            raise ProviderError(
                "OpenAI declined this request. Please revise the prompt and try again."
            )
        raise ProviderError("OpenAI returned no text for this request.")

    @staticmethod
    def _budget_count(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProviderError(
                "OpenAI returned invalid token-count data; request blocked by budget controls."
            )
        return value

    @staticmethod
    def _budget_micros(tokens: int, rate_micros_per_token: Decimal) -> int:
        return int(
            (Decimal(tokens) * rate_micros_per_token).to_integral_value(rounding=ROUND_CEILING)
        )

    def _lock_budget(self, reason: str) -> None:
        self._budget_contract_blocked = True
        logger.warning("Strict budget ledger locked (reason=%s)", reason)
        try:
            budget = self._budget
            if budget is not None:
                budget.lock(reason)
        except Exception:
            logger.error("Strict budget ledger lock failed")

    async def _budgeted_chat_completion(
        self, messages: list[dict[str, Any]], *, model: Optional[str] = None, **kwargs: Any
    ) -> str:
        """Strict Luna-only Responses call guarded by preflight counts and ledger holds."""
        budget = self._budget
        if budget is None:
            raise ProviderError("Strict budget mode is not configured; request blocked.")
        chosen_model = model or self.default_model
        if chosen_model != "gpt-6-luna" or self.default_model != "gpt-6-luna":
            raise ProviderError("Strict budget mode only permits the gpt-6-luna model.")
        if self._budget_contract_blocked:
            raise ProviderError("The budget ledger is locked; paid requests are blocked.")
        reasoning_requested = kwargs.get("reasoning_requested", False)
        if not isinstance(reasoning_requested, bool):
            raise ProviderError("reasoning_requested must be a boolean.")
        reasoning = {"effort": "low" if reasoning_requested else "none"}
        timeout = kwargs.get("request_timeout")
        request_messages = [dict(message) for message in messages]
        images: tuple[ImageInput, ...] = tuple(kwargs.get("images", ()))
        if images:
            last_user = next(
                (
                    index
                    for index in range(len(request_messages) - 1, -1, -1)
                    if request_messages[index].get("role") == "user"
                ),
                None,
            )
            if last_user is None:
                raise ProviderError("Images need a user message to attach to.")
            message = dict(request_messages[last_user])
            text = str(message.get("content", ""))
            message["content"] = [{"type": "input_text", "text": text}]
            message["content"].extend(
                {
                    "type": "input_image",
                    "image_url": f"data:{image.mime_type};base64,{base64.b64encode(image.data).decode('ascii')}",
                }
                for image in images
            )
            request_messages[last_user] = message

        wants_web = bool(kwargs.get("web_search", False))
        require_web = bool(kwargs.get("require_web_search", False))
        if require_web:
            wants_web = True
        # Extra thinking is an explicit, per-request choice. Reserve the entire
        # reasoning + visible output cap in Luna's existing daily/monthly pool.
        output_cap = 2000 if reasoning_requested else 500
        requested_output = (
            kwargs.get("reasoning_max_tokens") if reasoning_requested else kwargs.get("max_tokens")
        )
        if requested_output is None:
            max_output = output_cap
        elif (
            isinstance(requested_output, bool)
            or not isinstance(requested_output, int)
            or requested_output <= 0
        ):
            raise ProviderError("max_tokens must be a positive integer in strict budget mode.")
        else:
            max_output = min(output_cap, requested_output)
        try:
            state = cast(dict[str, Any], budget.snapshot())
        except Exception:
            raise ProviderError(
                "The budget ledger is unavailable; paid requests are blocked."
            ) from None
        if state.get("blocked_reason") == "opening_month_spend_unknown" or not state.get(
            "baseline_known"
        ):
            raise ProviderError(
                "Opening month spend is unknown; configure BUDGET_OPENING_MONTH_SPEND_USD before paid requests."
            )
        if state.get("blocked_reason"):
            raise ProviderError("The budget ledger is locked; paid requests are blocked.")
        luna_state = state.get("luna", {})
        precheck_rate = Decimal("0.75") if wants_web else Decimal("0.50")
        minimum_output_hold = self._budget_micros(max_output, precheck_rate)
        if int(luna_state.get("daily_remaining_micros", 0)) < minimum_output_hold:
            raise ProviderError(
                "The Luna daily budget is exhausted; check /budget for the reset time."
            )
        if int(luna_state.get("monthly_remaining_micros", 0)) < minimum_output_hold:
            raise ProviderError("The Luna monthly allocation is exhausted; check /budget.")
        extras_state = state.get("extras", {})
        extras_remaining = min(
            int(extras_state.get("monthly_remaining_micros", 0)),
            int(extras_state.get("daily_remaining_micros", 0)),
        )
        web_unavailable = False
        if wants_web and extras_remaining < 74_000:
            if require_web:
                extras_monthly = int(extras_state.get("monthly_remaining_micros", 0))
                if int(extras_state.get("daily_remaining_micros", 0)) < 74_000:
                    raise ProviderError(
                        "The Extras daily budget is exhausted; check /budget for the reset time."
                    )
                if extras_monthly < 74_000:
                    raise ProviderError(
                        "The Extras monthly allocation is exhausted; check /budget."
                    )
                raise ProviderError(
                    "The web-search reservation exceeds the remaining Extras budget."
                )
            wants_web = False
            web_unavailable = True
        if web_unavailable:
            request_messages = [
                {
                    "role": "developer",
                    "content": (
                        "Live web search is unavailable for this response. Do not claim you searched "
                        "or verified current information. Explain uncertainty about current facts "
                        "and do not invent sources."
                    ),
                },
                *request_messages,
            ]
        base_request: dict[str, Any] = {
            "model": "gpt-6-luna",
            "input": request_messages,
            "reasoning": reasoning,
        }
        web_tool = [{"type": "web_search", "search_context_size": "low"}]
        if wants_web:
            base_request["tools"] = web_tool
            base_request["max_tool_calls"] = 1
            if require_web:
                base_request["tool_choice"] = "required"
        try:
            count_response = await _bounded(
                self.client.responses.input_tokens.count(
                    **{
                        key: value
                        for key, value in base_request.items()
                        if key in {"model", "input", "reasoning", "tools", "tool_choice"}
                    }
                ),
                timeout,
                "OpenAI token counting",
            )
            counted_input = self._budget_count(getattr(count_response, "input_tokens", None))
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ProviderError(
                "OpenAI could not verify the input token count; request blocked by budget controls."
            ) from None
        if counted_input > 64_000:
            raise ProviderError("This request exceeds the strict 64,000 input-token budget limit.")

        luna_input_rate = Decimal("0.25") if wants_web else Decimal("0.125")
        luna_output_rate = Decimal("0.75") if wants_web else Decimal("0.50")
        luna_hold = self._budget_micros(counted_input, luna_input_rate) + self._budget_micros(
            max_output, luna_output_rate
        )
        web_hold = self._budget_micros(2 * 128_000, Decimal("0.25")) + 10_000
        used_web = wants_web
        reservations: list[Any]
        try:
            holds = {"luna": luna_hold}
            if wants_web:
                holds["extras"] = web_hold
            reservations = budget.reserve_many(holds)
        except BudgetExceeded as budget_error:
            if not wants_web or require_web:
                raise ProviderError(str(budget_error)) from None
            # Keep search optional for ordinary chat. Recount after changing the input and tools.
            request_messages = [
                {
                    "role": "developer",
                    "content": (
                        "Live web search is unavailable for this response. Do not claim you searched "
                        "or verified current information. Explain uncertainty about current facts "
                        "and do not invent sources."
                    ),
                },
                *request_messages,
            ]
            base_request = {
                "model": "gpt-6-luna",
                "input": request_messages,
                "reasoning": reasoning,
            }
            try:
                count_response = await _bounded(
                    self.client.responses.input_tokens.count(**base_request),
                    timeout,
                    "OpenAI token counting",
                )
                counted_input = self._budget_count(getattr(count_response, "input_tokens", None))
            except asyncio.CancelledError:
                raise
            except Exception:
                raise ProviderError(
                    "OpenAI could not verify the input token count; request blocked by budget controls."
                ) from None
            if counted_input > 64_000:
                raise ProviderError(
                    "This request exceeds the strict 64,000 input-token budget limit."
                )
            luna_hold = self._budget_micros(counted_input, Decimal("0.125")) + self._budget_micros(
                max_output, Decimal("0.50")
            )
            try:
                reservations = budget.reserve_many({"luna": luna_hold})
            except BudgetExceeded as budget_error:
                raise ProviderError(str(budget_error)) from None
            except BudgetError:
                raise ProviderError(
                    "The budget ledger is unavailable; paid requests are blocked."
                ) from None
            used_web = False
        except BudgetError:
            raise ProviderError(
                "The budget ledger is unavailable; paid requests are blocked."
            ) from None
        except Exception:
            raise ProviderError(
                "The budget ledger could not be verified; paid requests are blocked."
            ) from None

        request: dict[str, Any] = {
            "model": "gpt-6-luna",
            "input": request_messages,
            "store": False,
            "max_output_tokens": max_output,
            "reasoning": reasoning,
            "service_tier": "default",
            "max_tool_calls": 1,
            "parallel_tool_calls": False,
        }
        if used_web:
            request["tools"] = web_tool
            if require_web:
                request["tool_choice"] = "required"
        if timeout is not None:
            request["timeout"] = timeout
        response = await _bounded(self.client.responses.create(**request), timeout, "OpenAI")
        usage = getattr(response, "usage", None)
        output_items = getattr(response, "output", None) or []
        action_counts = _web_action_counts(output_items)
        logger.info(
            "OpenAI strict web usage observed "
            "(response_id=%s status=%s actions=%s input_tokens=%s output_tokens=%s)",
            _safe_response_id(response),
            _safe_response_status(response),
            action_counts,
            _safe_usage_count(usage, "input_tokens"),
            _safe_usage_count(usage, "output_tokens"),
        )
        if getattr(response, "model", "gpt-6-luna") != "gpt-6-luna":
            self._lock_budget("provider_contract_unknown_model")
            raise ProviderError(
                "OpenAI returned an unexpected model; budget usage could not be verified."
            )
        service_tier = getattr(response, "service_tier", None)
        if service_tier not in (None, "default", "standard"):
            self._lock_budget("provider_contract_unknown_service_tier")
            raise ProviderError(
                "OpenAI returned an unexpected service tier; budget usage could not be verified."
            )
        try:
            actual_input = self._budget_count(getattr(usage, "input_tokens", None))
            actual_output = self._budget_count(getattr(usage, "output_tokens", None))
            if actual_output > max_output:
                self._lock_budget("provider_contract_output_cap_exceeded")
                raise ProviderError("OpenAI usage exceeded the strict output-token cap.")
            web_activity = sum(action_counts.values())
            chargeable_searches = (
                action_counts["search"] + action_counts["unknown"] + action_counts["missing"]
            )
            if web_activity and not used_web:
                self._lock_budget("provider_contract_unrequested_web_activity")
                raise ProviderError("OpenAI used web search when it was not offered.")
            if chargeable_searches > 1:
                logger.error(
                    "OpenAI strict web-search cap violation "
                    "(response_id=%s status=%s actions=%s input_tokens=%d output_tokens=%d)",
                    _safe_response_id(response),
                    _safe_response_status(response),
                    action_counts,
                    actual_input,
                    actual_output,
                )
                self._lock_budget("provider_contract_search_call_cap_exceeded")
                raise ProviderError("OpenAI usage exceeded the strict web-search call cap.")
            if actual_input > counted_input and not used_web:
                self._lock_budget("provider_contract_unreserved_input_tokens")
                raise ProviderError("OpenAI usage exceeded the verified input-token count.")
            luna_input = min(actual_input, counted_input)
            long_context = used_web and actual_input > 272_000
            actual_input_rate = Decimal("0.25") if long_context else Decimal("0.125")
            actual_output_rate = Decimal("0.75") if long_context else Decimal("0.50")
            actual_luna = self._budget_micros(luna_input, actual_input_rate) + self._budget_micros(
                actual_output, actual_output_rate
            )
            actual_extras = self._budget_micros(
                max(0, actual_input - counted_input), actual_input_rate
            )
            if chargeable_searches:
                actual_extras += chargeable_searches * 10_000
            for reservation in reservations:
                actual = actual_luna if reservation.bucket == "luna" else actual_extras
                budget.settle(reservation.id, actual)
        except ProviderError:
            raise
        except BudgetError as budget_error:
            raise ProviderError(str(budget_error)) from None
        except Exception:
            # Keep all reservations when usage is missing or malformed.
            raise ProviderError(
                "OpenAI usage could not be verified; the budget reservation was retained."
            ) from None
        if require_web and not action_counts["search"]:
            raise ProviderError("OpenAI did not perform the required web search.")
        output = getattr(response, "output_text", None)
        if not output:
            raise ProviderError("OpenAI returned no text for this request.")
        # The capability is available for ordinary chat, but its presence does
        # not mean a search was requested or used. The no-search developer
        # instruction above keeps unsupported current claims out of fallbacks.
        return _web_citation_text(response, output) if web_activity else output

    async def generate_image(
        self, prompt: str, model: Optional[str] = None, **kwargs: Any
    ) -> str | bytes:
        if self._budget is not None:
            raise ProviderError("Image generation is disabled in strict budget mode.")
        if not self._image_generation_enabled:
            raise ProviderError(
                "Image generation is disabled. Set ENABLE_IMAGE_GENERATION=true to enable it."
            )
        response = await _bounded(
            self.client.images.generate(
                model=model or "gpt-image-1",
                prompt=prompt,
                size=kwargs.get("size", "1024x1024"),
                quality=kwargs.get("quality", "medium"),
                n=1,
            ),
            kwargs.get("request_timeout"),
            "OpenAI",
        )
        data = getattr(response, "data", None) or []
        if not data:
            raise ProviderError("OpenAI returned no image for this request.")
        image = data[0]
        if getattr(image, "b64_json", None):
            return base64.b64decode(image.b64_json)
        if getattr(image, "url", None):
            return image.url
        raise ProviderError("OpenAI returned no usable image data.")

    def get_available_models(self) -> list[ModelInfo]:
        if self._budget is not None:
            return [
                ModelInfo(
                    "gpt-6-luna", ProviderType.OPENAI, "Budgeted chat model", supports_vision=True
                )
            ]
        return [
            ModelInfo(
                self.default_model,
                ProviderType.OPENAI,
                "Configured OpenAI model",
                supports_vision=True,
            ),
            ModelInfo(
                "gpt-image-1",
                ProviderType.OPENAI,
                "OpenAI image generation",
                supports_image_generation=True,
            ),
        ]

    def supports_image_generation(self) -> bool:
        return self._budget is None and self._image_generation_enabled


class ClaudeProvider(BaseProvider):
    def __init__(self, api_key: str, *, default_model: str = "claude-haiku-4-5-20251001"):
        super().__init__(api_key, default_model=default_model)
        self.client = AsyncAnthropic(api_key=api_key, max_retries=0, timeout=60.0)

    async def chat_completion(
        self, messages: list[dict[str, Any]], model: Optional[str] = None, **kwargs: Any
    ) -> str:
        systems = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
        conversation = [
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in messages
            if m.get("role") in {"user", "assistant"}
        ]
        if not conversation:
            raise ProviderError("Claude needs at least one user or assistant message.")
        request: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": conversation,
            "max_tokens": kwargs.get("max_tokens") or 4096,
        }
        images: tuple[ImageInput, ...] = tuple(kwargs.get("images", ()))
        if images:
            last_user = next(
                (
                    index
                    for index in range(len(conversation) - 1, -1, -1)
                    if conversation[index].get("role") == "user"
                ),
                None,
            )
            if last_user is None:
                raise ProviderError("Images need a user message to attach to.")
            message = dict(conversation[last_user])
            text = str(message.get("content", ""))
            message["content"] = ([{"type": "text", "text": text}] if text else []) + [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.mime_type,
                        "data": base64.b64encode(image.data).decode("ascii"),
                    },
                }
                for image in images
            ]
            conversation[last_user] = message
            request["messages"] = conversation
        if systems:
            request["system"] = "\n\n".join(systems)
        for key in ("temperature", "top_p", "top_k"):
            if kwargs.get(key) is not None:
                request[key] = kwargs[key]
        if kwargs.get("request_timeout") is not None:
            request["timeout"] = kwargs["request_timeout"]
        response = await _bounded(
            self.client.messages.create(**request), kwargs.get("request_timeout"), "Claude"
        )
        text = "".join(
            block.text
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", "") == "text"
        )
        if not text:
            raise ProviderError("Claude returned no text for this request.")
        return text

    async def generate_image(
        self, prompt: str, model: Optional[str] = None, **kwargs: Any
    ) -> str | bytes:
        raise NotImplementedError("Claude does not support image generation.")

    def get_available_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                self.default_model,
                ProviderType.CLAUDE,
                "Configured Claude model",
                supports_vision=True,
            )
        ]

    def supports_image_generation(self) -> bool:
        return False


class GrokProvider(OpenAIProvider):
    def __init__(self, api_key: str, *, default_model: str = "grok-4.7"):
        super().__init__(api_key, default_model=default_model, base_url="https://api.x.ai/v1")

    async def chat_completion(
        self, messages: list[dict[str, Any]], model: Optional[str] = None, **kwargs: Any
    ) -> str:
        request: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": _compatible_messages(messages, tuple(kwargs.get("images", ()))),
        }
        if kwargs.get("max_tokens") is not None:
            request["max_tokens"] = kwargs["max_tokens"]
        for key in ("temperature", "top_p"):
            if kwargs.get(key) is not None:
                request[key] = kwargs[key]
        if kwargs.get("request_timeout") is not None:
            request["timeout"] = kwargs["request_timeout"]
        response = await _bounded(
            self.client.chat.completions.create(**request), kwargs.get("request_timeout"), "Grok"
        )
        text = response.choices[0].message.content if getattr(response, "choices", None) else None
        if not text:
            raise ProviderError("Grok returned no text for this request.")
        return text

    def get_available_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                self.default_model, ProviderType.GROK, "Configured xAI model", supports_vision=True
            )
        ]

    async def generate_image(
        self, prompt: str, model: Optional[str] = None, **kwargs: Any
    ) -> str | bytes:
        raise NotImplementedError("Grok image generation is not enabled in this bot.")

    def supports_image_generation(self) -> bool:
        return False


class OllamaProvider(OpenAIProvider):
    """Local Ollama OpenAI-compatible server; never downloads models."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://localhost:11434/v1",
        supports_vision: bool = False,
    ):
        if not model.strip():
            raise ProviderError(
                "Set OLLAMA_MODEL to a model already available in your local Ollama installation."
            )
        super().__init__("ollama", default_model=model, base_url=base_url.rstrip("/"))
        self._vision_enabled = supports_vision

    async def chat_completion(
        self, messages: list[dict[str, Any]], model: Optional[str] = None, **kwargs: Any
    ) -> str:
        request: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": _compatible_messages(messages, tuple(kwargs.get("images", ()))),
        }
        if kwargs.get("max_tokens") is not None:
            request["max_tokens"] = kwargs["max_tokens"]
        for key in ("temperature", "top_p"):
            if kwargs.get(key) is not None:
                request[key] = kwargs[key]
        if kwargs.get("request_timeout") is not None:
            request["timeout"] = kwargs["request_timeout"]
        response = await _bounded(
            self.client.chat.completions.create(**request), kwargs.get("request_timeout"), "Ollama"
        )
        text = response.choices[0].message.content if getattr(response, "choices", None) else None
        if not text:
            raise ProviderError("Ollama returned no text for this request.")
        return text

    async def generate_image(
        self, prompt: str, model: Optional[str] = None, **kwargs: Any
    ) -> str | bytes:
        raise NotImplementedError("Ollama image generation is not enabled in this bot.")

    def supports_image_generation(self) -> bool:
        return False

    def get_available_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                self.default_model,
                ProviderType.OLLAMA,
                "Configured local Ollama model",
                supports_vision=self._vision_enabled,
            )
        ]


class OpenAICompatibleProvider(BaseProvider):
    """Chat Completions adapter for providers with the OpenAI-compatible API."""

    provider_label = "Compatible API"
    provider_type = ProviderType.GROQ
    base_url = ""
    vision = False

    def __init__(self, api_key: str, *, default_model: str):
        super().__init__(api_key, default_model=default_model)
        self.client = AsyncOpenAI(
            api_key=api_key, base_url=self.base_url, max_retries=0, timeout=60.0
        )

    async def chat_completion(
        self, messages: list[dict[str, Any]], model: Optional[str] = None, **kwargs: Any
    ) -> str:
        request: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": _compatible_messages(messages, tuple(kwargs.get("images", ()))),
        }
        for key in ("max_tokens", "temperature", "top_p"):
            if kwargs.get(key) is not None:
                request[key] = kwargs[key]
        if kwargs.get("request_timeout") is not None:
            request["timeout"] = kwargs["request_timeout"]
        response = await _bounded(
            self.client.chat.completions.create(**request),
            kwargs.get("request_timeout"),
            self.provider_label,
        )
        choices = getattr(response, "choices", None) or []
        text = choices[0].message.content if choices else None
        if not text:
            raise ProviderError(f"{self.provider_label} returned no text for this request.")
        return text

    async def generate_image(
        self, prompt: str, model: Optional[str] = None, **kwargs: Any
    ) -> str | bytes:
        raise NotImplementedError(f"{self.provider_label} image generation is not enabled.")

    def get_available_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                self.default_model,
                self.provider_type,
                f"Configured {self.provider_label} model",
                supports_vision=self.vision,
            )
        ]

    def supports_image_generation(self) -> bool:
        return False


class GroqProvider(OpenAICompatibleProvider):
    provider_label = "Groq"
    provider_type = ProviderType.GROQ
    base_url = "https://api.groq.com/openai/v1"
    vision = False


class OpenRouterProvider(OpenAICompatibleProvider):
    provider_label = "OpenRouter"
    provider_type = ProviderType.OPENROUTER
    base_url = "https://openrouter.ai/api/v1"
    # The free-model router can choose among changing models, so vision is not guaranteed.
    vision = False


class ProviderManager:
    """Select configured providers with Gemini as the safe default."""

    _KEYS = {
        ProviderType.GEMINI: ("GEMINI_API_KEY", "GEMINI_KEY"),
        ProviderType.OPENAI: ("OPENAI_API_KEY", "OPENAI_KEY"),
        ProviderType.CLAUDE: ("ANTHROPIC_API_KEY", "CLAUDE_KEY"),
        ProviderType.GROK: ("XAI_API_KEY", "GROK_KEY"),
        ProviderType.GROQ: ("GROQ_API_KEY", ""),
        ProviderType.OPENROUTER: ("OPENROUTER_API_KEY", ""),
    }
    _MODELS = {
        ProviderType.GEMINI: ("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        ProviderType.OPENAI: ("OPENAI_MODEL", "gpt-6-luna"),
        ProviderType.CLAUDE: ("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        ProviderType.GROK: ("GROK_MODEL", "grok-4.7"),
        ProviderType.GROQ: ("GROQ_MODEL", "openai/gpt-oss-20b"),
        ProviderType.OPENROUTER: ("OPENROUTER_MODEL", "openrouter/free"),
    }
    _PAID = {ProviderType.OPENAI, ProviderType.CLAUDE, ProviderType.GROK}

    def __init__(self, environ: Optional[Mapping[str, str]] = None):
        self.environ = dict(os.environ if environ is None else environ)
        self.ai_access_mode = parse_ai_access_mode(self.environ)
        self.budget: Optional[BudgetLedger] = None
        try:
            self.strict_budget = _bool(self.environ, "HARD_BUDGET_ENABLED")
        except ValueError:
            raise ProviderError("HARD_BUDGET_ENABLED must be true or false.") from None
        if self.strict_budget and self.ai_access_mode != "disabled":
            configured_provider = (self.environ.get("DEFAULT_PROVIDER") or "").strip().lower()
            configured_model = (self.environ.get("OPENAI_MODEL") or "").strip()
            if configured_provider and configured_provider != "openai":
                raise ProviderError("Strict budget mode requires DEFAULT_PROVIDER=openai.")
            if configured_model and configured_model != "gpt-6-luna":
                raise ProviderError("Strict budget mode requires OPENAI_MODEL=gpt-6-luna.")
        self.openai_reasoning_effort = _openai_reasoning_effort(self.environ)
        self.providers: dict[ProviderType, BaseProvider] = {}
        self.current_provider = (
            ProviderType.OPENAI
            if self.strict_budget
            else self._parse_provider(self.environ.get("DEFAULT_PROVIDER", "gemini"))
        )
        self._cooldown_until: dict[ProviderType, float] = {}
        if self.strict_budget and self.ai_access_mode != "disabled":
            self.budget = self._create_budget()
        self._initialize_providers()

    @staticmethod
    def _budget_usd_micros(
        raw: Optional[str], name: str, default: Optional[str] = None
    ) -> Optional[int]:
        value = raw if raw not in (None, "") else default
        if value is None:
            return None
        try:
            amount = Decimal(value)
        except (InvalidOperation, TypeError, ValueError):
            raise ProviderError(f"{name} must be a finite non-negative dollar amount.") from None
        if not amount.is_finite() or amount < 0:
            raise ProviderError(f"{name} must be a finite non-negative dollar amount.")
        return int((amount * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))

    @classmethod
    def budget_settings(cls, environ: Mapping[str, str]) -> tuple[BudgetPolicy, str, Optional[int]]:
        """Parse and validate strict budget settings without opening the ledger."""
        monthly = cls._budget_usd_micros(
            environ.get("BUDGET_MONTHLY_USD"), "BUDGET_MONTHLY_USD", "10"
        )
        luna = cls._budget_usd_micros(environ.get("BUDGET_LUNA_USD"), "BUDGET_LUNA_USD", "7")
        opening = cls._budget_usd_micros(
            environ.get("BUDGET_OPENING_MONTH_SPEND_USD"), "BUDGET_OPENING_MONTH_SPEND_USD"
        )
        if monthly is None or monthly <= 0 or monthly > 10_000_000:
            raise ProviderError("BUDGET_MONTHLY_USD must be greater than zero and at most $10.00.")
        if luna is None or luna > monthly:
            raise ProviderError(
                "BUDGET_LUNA_USD must be non-negative and no greater than the monthly limit."
            )
        timezone = (environ.get("BUDGET_TIMEZONE") or "America/Los_Angeles").strip()
        path = (environ.get("BUDGET_DATABASE_PATH") or "data/budget.sqlite3").strip()
        if not timezone or not path:
            raise ProviderError("Budget timezone and database path must not be empty.")
        try:
            policy = BudgetPolicy(monthly, luna, timezone)
        except Exception:
            raise ProviderError("BUDGET_TIMEZONE or budget limits are invalid.") from None
        return policy, path, opening

    def _create_budget(self) -> BudgetLedger:
        policy, path, opening = self.budget_settings(self.environ)
        try:
            return BudgetLedger(policy, path, opening_month_spend_micros=opening)
        except BudgetError as error:
            raise ProviderError(str(error)) from None
        except Exception:
            raise ProviderError(
                "The strict budget could not be initialized; paid provider use is blocked."
            ) from None

    @staticmethod
    def _parse_provider(value: str | ProviderType) -> ProviderType:
        if isinstance(value, ProviderType):
            return value
        try:
            return ProviderType(value.strip().lower())
        except (ValueError, AttributeError):
            raise ProviderError(
                "Unknown provider. Choose gemini, groq, openrouter, openai, claude, grok, or ollama."
            ) from None

    @staticmethod
    def _enabled(value: Optional[str]) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _initialize_providers(self) -> None:
        if self.ai_access_mode == "disabled":
            logger.warning("AI_ACCESS_MODE=disabled: all AI providers are inactive")
            return
        if self.strict_budget:
            key = (
                self.environ.get("OPENAI_API_KEY") or self.environ.get("OPENAI_KEY") or ""
            ).strip()
            if key:
                self.providers[ProviderType.OPENAI] = OpenAIProvider(
                    key,
                    default_model="gpt-6-luna",
                    reasoning_effort="none",
                    base_url="https://api.openai.com/v1",
                    budget=self.budget,
                )
            return
        paid_allowed = self._enabled(self.environ.get("ALLOW_PAID_PROVIDERS"))
        for provider_type, (env_name, legacy_name) in self._KEYS.items():
            if provider_type in self._PAID and not paid_allowed:
                continue
            key = (self.environ.get(env_name) or self.environ.get(legacy_name) or "").strip()
            if not key:
                continue
            model_env, default_model = self._MODELS[provider_type]
            model = (self.environ.get(model_env) or "").strip() or default_model
            provider_class = {
                ProviderType.GEMINI: GeminiProvider,
                ProviderType.OPENAI: OpenAIProvider,
                ProviderType.CLAUDE: ClaudeProvider,
                ProviderType.GROK: GrokProvider,
                ProviderType.GROQ: GroqProvider,
                ProviderType.OPENROUTER: OpenRouterProvider,
            }[provider_type]
            options: dict[str, Any] = {"default_model": model}
            if provider_type == ProviderType.OPENAI:
                options["reasoning_effort"] = self.openai_reasoning_effort
                options["enable_image_generation"] = self._enabled(
                    self.environ.get("ENABLE_IMAGE_GENERATION")
                )
            self.providers[provider_type] = provider_class(key, **options)
        ollama_model = self.environ.get("OLLAMA_MODEL", "")
        if (self.current_provider == ProviderType.OLLAMA or ollama_model) and ollama_model.strip():
            self.providers[ProviderType.OLLAMA] = OllamaProvider(
                ollama_model,
                base_url=self.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                supports_vision=self._enabled(self.environ.get("OLLAMA_SUPPORTS_VISION")),
            )

    def _unavailable(self, provider_type: ProviderType) -> ProviderError:
        if self.strict_budget and provider_type != ProviderType.OPENAI:
            return ProviderError("Strict budget mode only permits OpenAI gpt-6-luna.")
        if self.strict_budget and provider_type == ProviderType.OPENAI and not self.providers:
            return ProviderError(
                "OpenAI is not configured. Set OPENAI_API_KEY to enable budgeted chat."
            )
        if provider_type == ProviderType.FREE:
            return ProviderError(
                "The old unauthenticated FREE/g4f scraper was removed. Use Gemini with GEMINI_API_KEY (Google AI Studio has a free tier), or configure OLLAMA_MODEL for a local model."
            )
        if provider_type in self._PAID and not self._enabled(
            self.environ.get("ALLOW_PAID_PROVIDERS")
        ):
            return ProviderError(
                f"{provider_type.value} is disabled by default because its API may incur charges. Set ALLOW_PAID_PROVIDERS=true to enable it."
            )
        if provider_type == ProviderType.OLLAMA:
            return ProviderError(
                "Ollama is not configured. Install and start Ollama, then set OLLAMA_MODEL to a model already present locally."
            )
        env_name = self._KEYS.get(provider_type, ("", ""))[0]
        return ProviderError(
            f"{provider_type.value} is not configured. Set {env_name} before selecting it."
        )

    @staticmethod
    def _is_free_openrouter_model(model: str) -> bool:
        normalized = model.strip().lower()
        return normalized == "openrouter/free" or normalized.endswith(":free")

    def _check_model_cost(self, provider_type: ProviderType, model: str) -> None:
        if (
            provider_type == ProviderType.OPENROUTER
            and not self._enabled(self.environ.get("ALLOW_PAID_PROVIDERS"))
            and not self._is_free_openrouter_model(model)
        ):
            raise ProviderError(
                "OpenRouter paid models are disabled. Choose openrouter/free or a model ending in :free, or set ALLOW_PAID_PROVIDERS=true."
            )

    def _fallback_types(self) -> list[ProviderType]:
        raw = self.environ.get("FALLBACK_PROVIDERS", "gemini,groq,openrouter,ollama")
        result: list[ProviderType] = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                candidate = self._parse_provider(item)
            except ProviderError:
                logger.warning("Ignoring unknown fallback provider name")
                continue
            if candidate not in result and candidate in self.providers:
                result.append(candidate)
        return result

    def _max_attempts(self) -> int:
        raw = self.environ.get("MAX_PROVIDER_ATTEMPTS", "3")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ProviderError("MAX_PROVIDER_ATTEMPTS must be an integer from 1 to 3.") from None
        if not 1 <= value <= 3:
            raise ProviderError("MAX_PROVIDER_ATTEMPTS must be an integer from 1 to 3.")
        return value

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        provider_type: ProviderType | None = None,
        model: str | None = None,
        images: tuple[ImageInput, ...] = (),
        web_search: bool = False,
        **kwargs: Any,
    ) -> CompletionResult:
        """Complete a chat request with bounded, eligible provider failover."""
        if ai_disabled(self.ai_access_mode, "chat completion"):
            raise ProviderError("AI access is disabled by the administrator.")
        primary = (
            self.current_provider if provider_type is None else self._parse_provider(provider_type)
        )
        primary_provider = self.get_provider(primary)
        primary_model = model or primary_provider.default_model
        self._check_model_cost(primary, primary_model)
        overall_timeout = kwargs.pop("request_timeout", 60.0)
        try:
            overall_timeout = float(overall_timeout)
        except (TypeError, ValueError):
            raise ProviderError("request_timeout must be a positive number of seconds.") from None
        if overall_timeout <= 0:
            raise ProviderError("request_timeout must be a positive number of seconds.")
        try:
            configured_attempt_cap = float(
                self.environ.get("PROVIDER_ATTEMPT_TIMEOUT_SECONDS", "20")
            )
        except ValueError:
            raise ProviderError(
                "PROVIDER_ATTEMPT_TIMEOUT_SECONDS must be a positive number."
            ) from None
        if configured_attempt_cap <= 0:
            raise ProviderError("PROVIDER_ATTEMPT_TIMEOUT_SECONDS must be a positive number.")
        attempt_cap = min(20.0, configured_attempt_cap)

        candidates = [primary]
        if self._enabled(self.environ.get("ENABLE_PROVIDER_FALLBACK", "true")):
            candidates.extend(p for p in self._fallback_types() if p != primary)
        max_attempts = self._max_attempts()
        deadline = time.monotonic() + overall_timeout
        attempted: list[ProviderType] = []
        failures: list[ProviderError] = []
        skipped_capability = 0
        for candidate in candidates:
            if len(attempted) >= max_attempts:
                break
            provider = self.providers[candidate]
            chosen_model = primary_model if candidate == primary else provider.default_model
            if candidate == ProviderType.OPENROUTER:
                try:
                    self._check_model_cost(candidate, chosen_model)
                except ProviderError as cost_error:
                    failures.append(cost_error)
                    continue
            if images and not provider.supports_vision():
                skipped_capability += 1
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failures.append(
                    ProviderError("The provider request deadline expired.", retryable=True)
                )
                break
            if self._cooldown_until.get(candidate, 0.0) > time.monotonic():
                continue
            attempted.append(candidate)
            candidate_cap = 45.0 if web_search and candidate == ProviderType.OPENAI else attempt_cap
            attempt_timeout = min(candidate_cap, remaining)
            request = dict(kwargs)
            request["images"] = images
            request["request_timeout"] = attempt_timeout
            request_messages = messages
            if web_search:
                if candidate == ProviderType.OPENAI:
                    request["web_search"] = True
                else:
                    request_messages = [
                        {
                            "role": "system",
                            "content": (
                                "Live web search is unavailable for this response. Do not claim "
                                "you searched or verified current information. Explain uncertainty "
                                "about current facts and do not invent sources."
                            ),
                        },
                        *messages,
                    ]
            try:
                text = await asyncio.wait_for(
                    provider.chat_completion(
                        request_messages,
                        model=chosen_model,
                        **request,
                    ),
                    timeout=attempt_timeout,
                )
                self._cooldown_until.pop(candidate, None)
                if web_search and candidate != ProviderType.OPENAI:
                    text = "Live web search is unavailable for this response.\n\n" + text
                return CompletionResult(text, candidate, chosen_model, tuple(attempted))
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                failure = ProviderError(
                    f"{candidate.value} did not respond before the request timed out. Please try again.",
                    retryable=True,
                )
            except ProviderError as provider_error:
                failure = provider_error
            except Exception as unexpected_error:
                logger.warning(
                    "%s request failed (%s)", candidate.value, type(unexpected_error).__name__
                )
                failure = _safe_error(candidate.value, unexpected_error)
            failures.append(failure)
            if failure.retryable:
                self._cooldown_until[candidate] = time.monotonic() + 30.0
                continue
            raise failure
        if skipped_capability and not attempted:
            raise ProviderError(
                "None of the configured providers can analyze images. Enable a vision-capable provider or use a text-only request."
            )
        if failures:
            raise failures[-1]
        raise ProviderError(
            "No configured provider is currently available for this request. Check the provider settings and cooldowns."
        )

    def get_provider(self, provider_type: Optional[ProviderType | str] = None) -> BaseProvider:
        if ai_disabled(self.ai_access_mode, "provider access"):
            raise ProviderError("AI access is disabled by the administrator.")
        selected = (
            self.current_provider if provider_type is None else self._parse_provider(provider_type)
        )
        provider = self.providers.get(selected)
        if provider is None:
            raise self._unavailable(selected)
        return provider

    def get_available_providers(self) -> list[ProviderType]:
        return list(self.providers)

    def get_provider_models(self, provider_type: ProviderType) -> list[ModelInfo]:
        if self.strict_budget and provider_type != ProviderType.OPENAI:
            return []
        provider = self.providers.get(provider_type)
        return provider.get_available_models() if provider else []

    def get_all_models(self) -> dict[ProviderType, list[ModelInfo]]:
        return {kind: provider.get_available_models() for kind, provider in self.providers.items()}

    def set_current_provider(self, provider_type: ProviderType | str) -> None:
        selected = self._parse_provider(provider_type)
        self.get_provider(selected)
        self.current_provider = selected

    async def close(self) -> None:
        await asyncio.gather(
            *(provider.close() for provider in self.providers.values()), return_exceptions=True
        )
