"""Conservative intent detection for optional per-request capabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestIntent:
    reasoning_requested: bool = False
    draw_prompt: str | None = None
    search_requested: bool = False
    reasoning_disabled: bool = False


_POLITE_PREFIX = re.compile(
    r"^(?:(?:please|kindly|can you|could you|would you|will you|"
    r"can you please|could you please|would you please)\s+)+",
    re.IGNORECASE,
)
_NEGATIVE_PREFIX = r"(?:do not|don['’]t|dont|never|no need to)"
_NEGATION = re.compile(rf"^{_NEGATIVE_PREFIX}\b", re.I)
_REASONING_PHRASE = (
    r"(?:reason\s+(?:through|about|out|carefully|step by step)|"
    r"think\s+(?:carefully|deeply|harder|hard|step by step)|"
    r"think\s+(?:this|it|that)\s+through\s+carefully|"
    r"use\s+(?:more|extra)\s+effort)\b"
)
_REASONING = re.compile(rf"^{_REASONING_PHRASE}", re.I)
_NO_REASONING = re.compile(
    rf"^(?:no\s+(?:extra|more)\s+effort\b|{_NEGATIVE_PREFIX}\s+{_REASONING_PHRASE})",
    re.I,
)
_DRAW = re.compile(
    r"^(?:(?P<draw>draw)\b|(?:generate|create|make)\s+(?:me\s+)?"
    r"(?:an?\s+)?(?:image|picture|illustration|artwork)\b|"
    r"i\s+want\s+(?:an?\s+)?(?:image|picture|illustration|artwork)\b"
    r"(?=\s+(?:of|showing|depicting|with)\b|\s*:))",
    re.I,
)
_NON_IMAGE_DRAW = re.compile(
    r"^(?:(?:a|an|the)\s+)?(?:conclusions?|attention|comparisons?|"
    r"distinctions?|parallels?|inferences?|lessons?|breath|cards?|lots|curtains)\b|"
    r"^(?:from|on|up)\b",
    re.I,
)
_IMAGE_PROMPT = re.compile(r"^(?:generation[\s-]+)?prompts?\b", re.I)
_SEARCH = re.compile(
    r"^(?:search\s+(?:(?:the\s+)?(?:web|internet)|online)|look\s+up|"
    r"find\s+(?:the\s+)?latest\s+(?:information|news)\s+(?:about|on)|"
    r"check\s+(?:the\s+)?current(?=\s+(?:weather|prices?|news|rates?|scores?|"
    r"standings|schedules?|availability|status|rules|regulations|versions?|releases?|"
    r"information|(?:train|flight|bus)\s+(?:schedules?|times|status))\b))\b",
    re.I,
)
_DISCUSSION_TAIL = re.compile(r"^\s+(?:is|are|was|were|means?)\b", re.I)


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
    """Read explicit leading requests locally; never scan quoted/context text.

    Reasoning and search can both be requested explicitly. The caller must reject
    that combination or choose a supported path before making a provider call.
    ``reasoning_disabled`` overrides a saved preference for this request only.
    """
    if _is_quoted_or_code(text):
        return RequestIntent()

    candidate = _POLITE_PREFIX.sub("", text.strip(), count=1)
    if _is_quoted_or_code(candidate):
        return RequestIntent()
    no_reasoning = _NO_REASONING.match(candidate)
    if no_reasoning:
        tail = candidate[no_reasoning.end() :].lstrip(" \t:,-;")
        return RequestIntent(
            reasoning_disabled=True,
            search_requested=_search_requested(tail),
        )
    if _NEGATION.match(candidate):
        return RequestIntent()

    draw = _DRAW.match(candidate)
    if draw:
        prompt = candidate[draw.end() :].lstrip(" \t:,-")
        prompt = re.sub(r"^(?:of|me)\s+", "", prompt, flags=re.IGNORECASE)
        non_image_draw = draw.group("draw") and _NON_IMAGE_DRAW.match(prompt)
        if prompt and not non_image_draw and not _IMAGE_PROMPT.match(prompt):
            return RequestIntent(draw_prompt=prompt)

    reasoning = _REASONING.match(candidate)
    if reasoning:
        if _DISCUSSION_TAIL.match(candidate[reasoning.end() :]):
            return RequestIntent()
        # Only a directly joined second imperative is a combined request. The
        # subject of "think carefully about how to search" is ordinary content.
        tail = candidate[reasoning.end() :].lstrip(" \t,;:")
        joined = re.match(r"^and\s+(?:then\s+)?(.+)$", tail, re.I | re.S)
        return RequestIntent(
            reasoning_requested=True,
            search_requested=bool(joined and _search_requested(joined.group(1))),
        )
    return RequestIntent(search_requested=_search_requested(candidate))


def _search_requested(text: str) -> bool:
    search = _SEARCH.match(text)
    if search is None:
        return False
    query = text[search.end() :].lstrip(" \t:,-")
    query = re.sub(r"^for\b\s*", "", query, flags=re.I)
    return bool(re.search(r"\w", query))
