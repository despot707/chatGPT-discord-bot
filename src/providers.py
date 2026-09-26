"""Async adapters for official hosted APIs and a local Ollama endpoint."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from anthropic import AsyncAnthropic
from google import genai
from google.genai import types
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


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
    """Safe, user-facing provider failure with no upstream details or secrets."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ImageInput:
    """Image bytes attached to the final user message."""

    data: bytes
    mime_type: str


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
        default_model: str = "gpt-5-mini",
        base_url: Optional[str] = None,
        enable_image_generation: bool = False,
    ):
        super().__init__(api_key, default_model=default_model)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=60.0)
        self._image_generation_enabled = enable_image_generation

    async def chat_completion(
        self, messages: list[dict[str, Any]], model: Optional[str] = None, **kwargs: Any
    ) -> str:
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
            return output
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

    async def generate_image(
        self, prompt: str, model: Optional[str] = None, **kwargs: Any
    ) -> str | bytes:
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
        return self._image_generation_enabled


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
        ProviderType.OPENAI: ("OPENAI_MODEL", "gpt-5-mini"),
        ProviderType.CLAUDE: ("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        ProviderType.GROK: ("GROK_MODEL", "grok-4.7"),
        ProviderType.GROQ: ("GROQ_MODEL", "openai/gpt-oss-20b"),
        ProviderType.OPENROUTER: ("OPENROUTER_MODEL", "openrouter/free"),
    }
    _PAID = {ProviderType.OPENAI, ProviderType.CLAUDE, ProviderType.GROK}

    def __init__(self, environ: Optional[Mapping[str, str]] = None):
        self.environ = dict(os.environ if environ is None else environ)
        self.providers: dict[ProviderType, BaseProvider] = {}
        self.current_provider = self._parse_provider(self.environ.get("DEFAULT_PROVIDER", "gemini"))
        self._cooldown_until: dict[ProviderType, float] = {}
        self._initialize_providers()

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
        **kwargs: Any,
    ) -> CompletionResult:
        """Complete a chat request with bounded, eligible provider failover."""
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
            attempt_timeout = min(attempt_cap, remaining)
            request = dict(kwargs)
            request["images"] = images
            request["request_timeout"] = attempt_timeout
            try:
                text = await asyncio.wait_for(
                    provider.chat_completion(
                        messages,
                        model=chosen_model,
                        **request,
                    ),
                    timeout=attempt_timeout,
                )
                self._cooldown_until.pop(candidate, None)
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
