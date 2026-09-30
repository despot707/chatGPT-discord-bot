"""One SDK attempt after durable admission; no fallback, retries or hidden tools."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Callable

from src.ai_access import ai_disabled, parse_ai_access_mode
from src.budget import BudgetExceeded, BudgetLedger, PaidBudgetLedger
from src.prepaid import COSTS, Denied, Ledger, integer


class Gateway:
    def __init__(self, ledger: Ledger, client, *, ready: Callable[[], None], budget: BudgetLedger):
        self.ledger = ledger
        self.client = client
        self.ready = ready
        self.budget = budget

    async def complete(
        self,
        guild: int,
        user: int,
        messages: list,
        *,
        reasoning=False,
        search=False,
        allow_web=False,
        images=(),
    ) -> str:
        if ai_disabled(parse_ai_access_mode(os.environ), "paid completion"):
            raise Denied("I can't do that right now.")
        self.ready()
        if (
            type(reasoning) is not bool
            or type(search) is not bool
            or type(allow_web) is not bool
            or (reasoning and search)
        ):
            raise Denied("Choose either a web lookup or a reasoning request.")
        feature = "search" if search else "reasoning" if reasoning else "chat"
        model = "gpt-6-luna"
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
        request["reasoning"] = {"effort": "low" if reasoning else "none"}
        # A normal chat may search when the model needs live information. Hold
        # both possible outcomes before dispatch; verified usage selects one
        # allowance and releases the other. If search has no available quota,
        # continue as a chat that must disclose its lack of live web access.
        optional_web = allow_web and feature == "chat"
        rid = self.ledger.reserve(guild, user, feature)
        search_rid = None
        budget_holds = {}
        try:
            if optional_web:
                try:
                    search_rid = self.ledger.reserve(guild, user, "search")
                except Denied:
                    optional_web = False
            if optional_web:
                assert search_rid is not None
                try:
                    holds = (
                        self.budget.reserve_paid(self.ledger, [rid, search_rid])
                        if isinstance(self.budget, PaidBudgetLedger)
                        else self.budget.reserve_many(
                            {"luna": COSTS["chat"], "extras": COSTS["search"]}
                        )
                    )
                    budget_holds = {hold.bucket: hold for hold in holds}
                except BudgetExceeded:
                    assert search_rid is not None
                    self.ledger.cancel(search_rid)
                    search_rid = None
                    optional_web = False
            if not budget_holds:
                bucket = "luna" if feature == "chat" else "extras"
                budget_holds[bucket] = (
                    self.budget.reserve_paid(self.ledger, [rid])[0]
                    if isinstance(self.budget, PaidBudgetLedger)
                    else self.budget.reserve(bucket, COSTS[feature])
                )
            if search or optional_web:
                request["tools"] = [{"type": "web_search", "search_context_size": "low"}]
                request["tool_choice"] = "required" if search else "auto"
                if optional_web:
                    request_messages.insert(
                        0,
                        {
                            "role": "developer",
                            "content": (
                                "Use web_search when answering needs current or "
                                "externally verifiable information. Otherwise "
                                "answer without a web call. Never claim a lookup "
                                "unless the tool actually runs."
                            ),
                        },
                    )
            elif allow_web and feature == "chat":
                request_messages.insert(
                    0,
                    {
                        "role": "developer",
                        "content": (
                            "Live web access is unavailable for this request. "
                            "Do not claim you searched or verified current information."
                        ),
                    },
                )
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
            # Counting awaited network I/O. Re-check launch permission and the
            # authenticated snapshot immediately before durable dispatch.
            if ai_disabled(parse_ai_access_mode(os.environ), "paid dispatch"):
                raise Denied("I can't do that right now.")
            self.ready()
            if optional_web:
                assert search_rid is not None
                self.ledger.dispatch_many([rid, search_rid])
            else:
                self.ledger.dispatch(rid)
        except BaseException:
            self.ledger.cancel(rid)
            if search_rid is not None:
                self.ledger.cancel(search_rid)
            self.budget.cancel_before_dispatch([hold.id for hold in budget_holds.values()])
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
            web_calls = [i for i in items if getattr(i, "type", "") == "web_search_call"]
            tool_calls = len(web_calls)
            web_statuses = [getattr(i, "status", None) for i in web_calls]
            # max_tool_calls limits processed calls. The provider can still
            # return one later ignored attempt in 'searching' state after a
            # completed call. Count both possible fees against the $0.04 hold.
            valid_web_shape = not web_calls or (
                getattr(response, "status", None) == "completed"
                and web_statuses in (["completed"], ["completed", "searching"])
            )
            unknown = any(
                getattr(i, "type", "") not in {"web_search_call", "reasoning", "message", "refusal"}
                for i in items
            )
            if (
                output_tokens > cap
                or unknown
                or not valid_web_shape
                or (not (search or optional_web) and tool_calls)
            ):
                raise Denied("Provider response exceeded the approved contract.")
            # Responses web search is documented to have a 128k search-context
            # window, separate from the admitted 8k prompt. Allow headroom for
            # wrapping, but reject usage outside the prepaid $0.04 envelope.
            if input_tokens > (136000 if tool_calls else 8000):
                raise Denied("Provider input exceeded its admitted bound.")
            # Count all input at Luna's cache-write price ($0.125/M), which is
            # higher than standard input and independent of cache discounts.
            # One built-in web call adds $0.01; the 128k search context means
            # long-context pricing (>272k) is outside this approved contract.
            cost = (input_tokens + 7) // 8 + (output_tokens + 1) // 2
            if tool_calls:
                cost += tool_calls * 10000
            selected_feature = "search" if tool_calls and optional_web else feature
            if cost > COSTS[selected_feature]:
                raise Denied("Provider cost exceeded its reserved maximum.")
            # The API docs do not explicitly reconcile hosted web-content
            # tokens to response.usage. Charge the full search hold after a
            # verified call so the shared cash ledger never understates spend.
            if tool_calls:
                cost = COSTS["search"]
            if optional_web:
                assert search_rid is not None
                self.ledger.settle_choice(rid, search_rid, cost, used_search=bool(tool_calls))
                selected_bucket = "extras" if tool_calls else "luna"
                other_bucket = "luna" if tool_calls else "extras"
                self.budget.settle(budget_holds[selected_bucket].id, cost)
                self.budget.settle(budget_holds[other_bucket].id, 0)
            else:
                self.ledger.settle(rid, cost)
                self.budget.settle(next(iter(budget_holds.values())).id, cost)
        except Denied:
            self.ledger.lock("paid_response_contract")
            self.budget.lock("paid_response_contract")
            raise
        except (AttributeError, TypeError, ValueError):
            self.ledger.lock()
            self.budget.lock("paid_usage_contract")
            raise Denied("Usage could not be verified; paid features are paused.") from None
        if search and not tool_calls:
            raise Denied(
                "The web lookup did not run. This attempt may have consumed its allowance."
            )
        text = getattr(response, "output_text", "")
        if not text:
            raise Denied(
                "The provider returned no text. This attempt may have consumed its allowance."
            )
        from src.providers import _web_citation_text

        return _web_citation_text(response, text) if tool_calls else text

    async def image(self, guild: int, user: int, prompt: str) -> bytes:
        if ai_disabled(parse_ai_access_mode(os.environ), "paid image"):
            raise Denied("I can't do that right now.")
        self.ready()
        # Deliberately one priced contract, not whatever model/quality the user
        # can select in legacy provider menus. Requires launch availability check.
        if not isinstance(prompt, str) or not 1 <= len(prompt.encode()) <= 2000:
            raise Denied("Use an image prompt of 1 to 2,000 UTF-8 bytes.")
        rid = self.ledger.reserve(guild, user, "images")
        budget_hold = None
        try:
            budget_hold = (
                self.budget.reserve_paid(self.ledger, [rid])[0]
                if isinstance(self.budget, PaidBudgetLedger)
                else self.budget.reserve("extras", COSTS["images"])
            )
            self.ledger.dispatch(rid)
        except BaseException:
            self.ledger.cancel(rid)
            if budget_hold is not None:
                self.budget.cancel_before_dispatch([budget_hold.id])
            raise
        response = await self.client.images.generate(
            model="gpt-image-2.5-flare-2026-09-08",
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="medium",
            output_format="jpeg",
            timeout=60,
        )
        # Flare 2.5 charges $5/M text input and $30/M output tokens. Its
        # 1024-square medium generation is 439 output tokens in the official
        # calculator; the $0.08 reservation leaves headroom for prompt tokens.
        # Require complete usage; unknown means freeze, not assume a free call.
        try:
            usage = response.usage
            it = integer(usage.input_tokens)
            ot = integer(usage.output_tokens)
            cost = it * 5 + ot * 30
            if it > 2500 or cost > COSTS["images"]:
                raise Denied("Image cost contract exceeded.")
            self.ledger.settle(rid, cost)
            self.budget.settle(budget_hold.id, cost)
            data = response.data
            if len(data) != 1 or not data[0].b64_json:
                raise Denied("Expected one inline image.")
            if len(data[0].b64_json) > 12_000_000:
                raise Denied("Image exceeds bounded delivery size.")
            return base64.b64decode(data[0].b64_json, validate=True)
        except Denied:
            self.ledger.lock("paid_image_contract")
            self.budget.lock("paid_image_contract")
            raise
        except (AttributeError, TypeError, ValueError):
            self.ledger.lock()
            self.budget.lock("paid_image_contract")
            raise Denied("Image usage could not be verified; paid features are paused.") from None
