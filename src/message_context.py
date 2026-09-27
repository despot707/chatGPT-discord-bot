"""Bounded, display-oriented serialization for Discord message context."""

from __future__ import annotations

from datetime import datetime, timezone

MAX_MESSAGE_CONTEXT_CHARS = 4000


def _text(value, limit: int = 1000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _emoji(value) -> str:
    if value is None:
        return ""
    return _text(value, 80)


def _poll_text(poll) -> list[str]:
    question = _text(getattr(poll, "question", None), 300)
    if not question:
        return []
    lines = [f"Poll question: {question}"]
    finalized = getattr(poll, "is_finalized", None) or getattr(poll, "is_finalised", None)
    try:
        is_finalized = bool(finalized()) if callable(finalized) else bool(finalized)
    except (TypeError, ValueError):
        is_finalized = False
    expiry = getattr(poll, "expires_at", None)
    if is_finalized:
        status = "closed"
    elif isinstance(expiry, datetime) and expiry.tzinfo is not None:
        status = "closed; final results pending" if expiry <= datetime.now(timezone.utc) else "open"
    else:
        status = "unknown"
    lines.append(f"Poll status: {status}")
    multiple = bool(getattr(poll, "multiple", False))
    lines.append(f"Multiple selections: {'yes' if multiple else 'no'}")
    # discord.py initializes missing answer results to zero. Only finalized results
    # make every omitted answer entry a reliable zero under Discord's poll semantics.
    has_final_results = is_finalized
    for answer in list(getattr(poll, "answers", ()) or ())[:10]:
        label = _text(
            getattr(answer, "text", None)
            or getattr(answer, "media", None)
            and getattr(answer.media, "text", None),
            200,
        )
        emoji = _emoji(
            getattr(answer, "emoji", None) or getattr(getattr(answer, "media", None), "emoji", None)
        )
        if emoji:
            label = f"{emoji} {label}".strip()
        count = getattr(answer, "vote_count", None)
        if has_final_results and isinstance(count, int) and count >= 0:
            count_text = str(count)
        elif isinstance(count, int) and count > 0:
            count_text = f"{count} reported (provisional)"
        else:
            count_text = "unknown"
        unit = "selections" if multiple else "votes"
        lines.append(f"- {label or 'Unlabeled option'}: {count_text} {unit}")
    if has_final_results:
        total = getattr(poll, "total_votes", None)
        if isinstance(total, int) and total >= 0:
            unit = "selections" if multiple else "votes"
            lines.append(f"Total {unit}: {total}")
    return lines


def _embed_text(embeds) -> list[str]:
    lines: list[str] = []
    for embed in list(embeds or ())[:5]:
        title = _text(getattr(embed, "title", None), 300)
        description = _text(getattr(embed, "description", None), 1000)
        if title:
            lines.append(f"Embed title: {title}")
        if description:
            lines.append(f"Embed description: {description}")
        for field in list(getattr(embed, "fields", ()) or ())[:10]:
            name = _text(getattr(field, "name", None), 160)
            value = _text(getattr(field, "value", None), 500)
            if name or value:
                lines.append(f"Embed field {name or 'value'}: {value}")
    return lines


def _component_text(components) -> list[str]:
    """Capture visible labels and choices, never IDs or interaction controls."""
    lines: list[str] = []

    def visit(items, depth: int = 0) -> None:
        if depth > 4 or len(lines) >= 30:
            return
        for item in list(items or ()):
            if len(lines) >= 30:
                return
            label = _text(getattr(item, "label", None), 160)
            text = _text(getattr(item, "text", None) or getattr(item, "content", None), 300)
            if label:
                lines.append(f"Component label: {label}")
            if text:
                lines.append(f"Component text: {text}")
            for option in list(getattr(item, "options", ()) or ())[:25]:
                option_label = _text(getattr(option, "label", None), 120)
                option_desc = _text(getattr(option, "description", None), 200)
                if option_label:
                    suffix = f" — {option_desc}" if option_desc else ""
                    lines.append(f"Select option: {option_label}{suffix}")
            visit(getattr(item, "children", ()), depth + 1)
            accessory = getattr(item, "accessory", None)
            if accessory is not None:
                visit((accessory,), depth + 1)

    visit(components)
    return lines


def serialize_message(message, *, max_chars: int = MAX_MESSAGE_CONTEXT_CHARS) -> str:
    """Return visible, bounded message content; empty means no readable text."""
    parts: list[str] = []
    content = _text(getattr(message, "content", None), 2000)
    if content:
        parts.append(content)
    parts.extend(_poll_text(getattr(message, "poll", None)))
    parts.extend(_embed_text(getattr(message, "embeds", ())))
    parts.extend(_component_text(getattr(message, "components", ())))
    return "\n".join(parts)[: max(0, max_chars)]
