"""Render budget status without invoking a model or changing allowances."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any


def _usd(micros: int) -> str:
    return f"${Decimal(micros) / 1_000_000:.4f}"


def format_budget(snapshot: dict[str, Any]) -> str:
    lines = [
        "**Shared API budget — all servers and users**",
        f"Monthly ceiling: {_usd(snapshot['monthly_limit_micros'])}",
        f"Accounted spending: {_usd(snapshot['monthly_spent_micros'])}; "
        f"reserved: {_usd(snapshot['reserved_micros'])}",
        f"Monthly remaining: {_usd(snapshot['monthly_remaining_micros'])}",
    ]
    for key, label in (("luna", "Luna"), ("extras", "Extras")):
        pool = snapshot[key]
        lines.append(
            f"{label}: {_usd(pool['daily_remaining_micros'])} left today / "
            f"{_usd(pool['daily_limit_micros'])} daily allowance; "
            f"{_usd(pool['monthly_limit_micros'])} monthly allocation"
        )
    for key, label in (("day_resets_at", "Daily reset"), ("month_resets_at", "Monthly reset")):
        timestamp = int(datetime.fromisoformat(snapshot[key]).timestamp())
        lines.append(f"{label}: <t:{timestamp}:F>")
    if snapshot.get("blocked_reason"):
        reason = {
            "opening_month_spend_unknown": "The owner must enter the existing spend for this month.",
            "ledger_locked": "The budget needs owner review after an unexpected usage result.",
        }.get(str(snapshot["blocked_reason"]), str(snapshot["blocked_reason"]))
        lines.append(f"Paid requests blocked: {reason}")
    lines.append("Unused daily allowance expires. Uncertain charges stay reserved.")
    return "\n".join(lines)
