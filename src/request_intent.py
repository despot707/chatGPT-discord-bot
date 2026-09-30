"""Conservative intent detection for optional per-request capabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestIntent:
    reasoning_requested: bool = False
    draw_prompt: str | None = None


_POLITE_PREFIX = re.compile(
    r"^(?:(?:please|kindly|can you|could you|would you|will you|"
    r"can you please|could you please|would you please)\s+)+",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"^(?:please\s+)?(?:do not|don't|dont|never|no need to)\b", re.I)
_REASONING = re.compile(
    r"^(?:reason\s+(?:through|about|out|carefully|step by step)\b|"
    r"think (?:carefully|deeply|step by step)\b)",
    re.IGNORECASE,
)
_DRAW = re.compile(
    r"^(?:draw\b|generate\s+(?:an?\s+)?(?:image|picture|illustration|artwork)\b|"
    r"create\s+(?:an?\s+)?(?:image|picture|illustration|artwork)\b)",
    re.IGNORECASE,
)


def _is_quoted_or_code(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    if stripped.startswith(("```", "~~~", ">", "`")):
        return True
    # A quoted command is content to discuss, not an instruction to the bot.
    if stripped[0] in {'"', "'", "“", "‘"}:
        return True
    return False


def parse_request_intent(text: str) -> RequestIntent:
    """Recognize only explicit leading requests in the current user's text."""
    if _is_quoted_or_code(text):
        return RequestIntent()

    candidate = _POLITE_PREFIX.sub("", text.strip(), count=1)
    if _NEGATION.match(candidate):
        return RequestIntent()

    draw = _DRAW.match(candidate)
    if draw:
        prompt = candidate[draw.end() :].lstrip(" \t:,-")
        prompt = re.sub(r"^(?:of|me)\s+", "", prompt, flags=re.IGNORECASE)
        non_image_draw = re.match(
            r"^(?:(?:a|an|the)\s+)?(?:conclusions?|attention|comparisons?|"
            r"distinctions?|parallels?|inferences?|lessons?|breath)\b|^from\b|^on\b",
            prompt,
            re.IGNORECASE,
        )
        if prompt and not non_image_draw:
            return RequestIntent(draw_prompt=prompt)

    reasoning_requested = bool(_REASONING.match(candidate))
    return RequestIntent(reasoning_requested=reasoning_requested)
