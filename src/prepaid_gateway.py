"""One SDK attempt after durable admission; no fallback, retries or hidden tools."""

from __future__ import annotations

import base64
import json
from typing import Any, Callable

from src.prepaid import COSTS, Denied, Ledger, integer


class Gateway:
    def __init__(self, ledger: Ledger, client, *, ready: Callable[[], None]):
        self.ledger = ledger
        self.client = client
        self.ready = ready

    async def complete(
        self, guild: int, user: int, messages: list, *, reasoning=False, search=False, images=()
    ) -> str:
        self.ready()
        if type(reasoning) is not bool or type(search) is not bool or (reasoning and search):
            raise Denied("Choose either a web lookup or a reasoning request.")
        feature = "search" if search else "reasoning" if reasoning else "chat"
        model = "gpt-4.1-mini" if search else "gpt-6-luna"
        cap = 2000 if reasoning else 500
        if len(messages) > 45 or len(json.dumps(messages).encode()) > 150000 or len(images) > 1:
            raise Denied("This request exceeds the bounded conversation size.")
        if any(
            m.get("role") not in {"system", "developer", "user", "assistant"}
            or not isinstance(m.get("content"), str)
            for m in messages
        ):
            raise Denied("Only bounded conversation inputs are supported.")
        request_messages = [dict(m) for m in messages]
        if images:
            image = images[0]
            if len(image.data) > 2_000_000:
                raise Denied("Image input exceeds 2 MB.")
            index = next(
                (i for i in range(len(messages) - 1, -1, -1) if messages[i]["role"] == "user"), None
            )
            if index is None:
                raise Denied("An image needs a user prompt.")
            request_messages[index]["content"] = [
                {"type": "input_text", "text": messages[index]["content"]},
                {
                    "type": "input_image",
                    "detail": "low",
                    "image_url": f"data:{image.mime_type};base64,"
                    + base64.b64encode(image.data).decode(),
                },
            ]
        request: dict[str, Any] = {"model": model, "input": request_messages}
        if not search:
            request["reasoning"] = {"effort": "low" if reasoning else "none"}
        if search:
            request["tools"] = [{"type": "web_search", "search_context_size": "low"}]
            request["tool_choice"] = "required"
        # Reserve BEFORE even the unbilled token-counting network request.
        rid = self.ledger.reserve(guild, user, feature)
        try:
            counted = await self.client.responses.input_tokens.count(**request, timeout=10)
            count = integer(getattr(counted, "input_tokens", None))
            if count > 8000:
                raise Denied(
                    "This request exceeds 8,000 input tokens, including context. Shorten it or reset the conversation."
                )
            request.update(
                store=False,
                max_output_tokens=cap,
                service_tier="default",
                timeout=45,
                max_tool_calls=1,
                parallel_tool_calls=False,
            )
            self.ledger.dispatch(rid)
        except BaseException:
            self.ledger.cancel(rid)
            raise
        # Timeout/cancellation/connection failure can be billable. Leave the
        # dispatched maximum charged and never retry automatically.
        response = await self.client.responses.create(**request)
        try:
            actual_model = getattr(response, "model", model)
            if actual_model != model and not actual_model.startswith(model + "-"):
                raise Denied("Unexpected provider model.")
            usage = response.usage
            input_tokens = integer(usage.input_tokens)
            output_tokens = integer(usage.output_tokens)
            items = getattr(response, "output", None) or []
            tool_calls = sum(getattr(i, "type", "") == "web_search_call" for i in items)
            unknown = any(
                getattr(i, "type", "") not in {"web_search_call", "reasoning", "message", "refusal"}
                for i in items
            )
            if output_tokens > cap or unknown or tool_calls > 1 or (not search and tool_calls):
                raise Denied("Provider response exceeded the approved contract.")
            if not search and input_tokens > 8000:
                raise Denied("Provider input exceeded its admitted bound.")
            # Cached discounts never increase available quota. Search input uses
            # the documented 8k block; add it conservatively even if usage already
            # includes it. Final usage exceeding the hold freezes the service.
            cost = (
                (input_tokens * 2 + output_tokens * 8 + 4) // 5
                if search
                else (input_tokens + output_tokens * 4 + 7) // 8
            )
            if search:
                cost += tool_calls * (10000 + 3200)
            if cost > COSTS[feature]:
                raise Denied("Provider cost exceeded its reserved maximum.")
            self.ledger.settle(rid, cost)
        except (AttributeError, TypeError, ValueError):
            self.ledger.lock()
            raise Denied("Usage could not be verified; paid features are paused.") from None
        text = getattr(response, "output_text", "")
        if not text:
            raise Denied(
                "The provider returned no text. This attempt may have consumed its allowance."
            )
        from src.providers import _web_citation_text

        return _web_citation_text(response, text) if search else text

    async def image(self, guild: int, user: int, prompt: str) -> bytes:
        self.ready()
        # Deliberately one priced contract, not whatever model/quality the user
        # can select in legacy provider menus. Requires launch availability check.
        if not isinstance(prompt, str) or not 1 <= len(prompt.encode()) <= 2000:
            raise Denied("Use an image prompt of 1 to 2,000 UTF-8 bytes.")
        rid = self.ledger.reserve(guild, user, "images")
        self.ledger.dispatch(rid)
        response = await self.client.images.generate(
            model="gpt-image-2-2026-04-21",
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="medium",
            output_format="jpeg",
            timeout=60,
        )
        # Image 2 publishes about $0.053 for medium square output + $2.50/M
        # text input. Reserve $0.08 above a 2,000-byte prompt and fixed image.
        # Require complete usage; unknown means freeze, not assume a free call.
        try:
            usage = response.usage
            it = integer(usage.input_tokens)
            ot = integer(usage.output_tokens)
            cost = (it * 5 + 1) // 2 + ot * 15
            if it > 2500 or cost > COSTS["images"]:
                raise Denied("Image cost contract exceeded.")
            self.ledger.settle(rid, cost)
            data = response.data
            if len(data) != 1 or not data[0].b64_json:
                raise Denied("Expected one inline image.")
            if len(data[0].b64_json) > 12_000_000:
                raise Denied("Image exceeds bounded delivery size.")
            return base64.b64decode(data[0].b64_json, validate=True)
        except (AttributeError, TypeError, ValueError):
            self.ledger.lock()
            raise Denied("Image usage could not be verified; paid features are paused.") from None
