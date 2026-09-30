"""Fail-closed commercial adapters; inactive unless PREPAID_MODE=enforce.

Launch evidence is operator-supplied, not an API verification of native caps.
Preview never converts Discord test entitlements to money or changes production.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Callable

from src.ai_access import ai_disabled, parse_ai_access_mode
from src.prepaid import FREE_STORAGE_BYTES, Denied, Ledger, integer
from src.prepaid_gateway import Gateway

SCOPE: ContextVar[tuple[int, int, int] | None] = ContextVar("paid_scope", default=None)
STORAGE_LOCK = threading.RLock()
_INSTANCE = None


def enforcing() -> bool:
    mode = os.getenv("PREPAID_MODE", "off")
    if mode not in {"off", "preview", "enforce"}:
        raise Denied("Invalid prepaid mode; paid features cannot start.")
    return mode == "enforce"


class Runtime:
    def __init__(
        self, ledger: Ledger, approval_path: str, *, clock: Callable[[], float] = time.time
    ):
        self.ledger = ledger
        self.approval_path = approval_path
        self.clock = clock
        self.gateway: Gateway | None = None
        self._recent: dict[tuple[int, int], float] = {}
        self._housekeeping: dict[int, float] = {}
        self._housekeeping_global: tuple[float, int] = (0, 0)
        self._housekeeping_events: dict[tuple[int, int], float] = {}

    def acquire_process(self):
        """Reject a second production writer; never use a network SQLite volume."""
        if getattr(self, "_lease", None) is not None:
            return
        if sys.platform == "win32":
            raise Denied("Commercial SQLite deployment requires the single-writer Linux container.")
        import fcntl

        lease = open(self.ledger.path + ".lock", "a")
        try:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lease.close()
            raise Denied("Another paid bot process owns this database.") from None
        self._lease = lease

    def evidence(self) -> dict:
        try:
            path = Path(self.approval_path)
            if path.stat().st_size > 8192:
                raise ValueError()
            data = json.loads(path.read_text())
            age = self.clock() - integer(data.get("reviewed_at"))
            if not 0 <= age <= 7 * 86400:
                raise ValueError()
            return data
        except (OSError, ValueError, TypeError):
            raise Denied(
                "Paid launch is locked pending a current cost and compliance review."
            ) from None

    def ready(self):
        data = self.evidence()
        from src.discord_purchases import PurchaseDenied, assert_purchase_current, sku_map

        if os.getenv("DISCORD_PURCHASE_MODE", "off") != "enforce":
            raise PurchaseDenied(
                "Paid access requires authenticated Discord purchase reconciliation."
            )
        assert_purchase_current(
            self.ledger,
            integer(int(os.getenv("DISCORD_APPLICATION_ID", "0")), 1),
            sku_map(os.getenv("DISCORD_SKU_MAP", "")),
            clock=self.clock,
        )
        for key in (
            "approved",
            "receipts_verified",
            "provider_contracts_verified",
            "storage_lifecycle_verified",
            "workload_isolated",
            "reconciliation_current",
        ):
            if data.get(key) is not True:
                raise Denied(
                    "Paid launch remains locked pending verified billing and cost controls."
                )
        compute = integer(data.get("native_compute_cap_micros"), 1)
        agent = integer(data.get("native_agent_cap_micros"))
        integer(data.get("native_openai_cap_micros"), 1)
        hosting = integer(data.get("hosting_liability_micros"), 1)
        funding = integer(data.get("operator_funding_micros"))
        # Native metering can lag; retain 10% headroom over configured caps.
        needed = max(hosting, (compute + agent) * 110 // 100)
        if self.ledger.infrastructure_funding() + funding < needed:
            raise Denied(
                "Service capacity is paused: prepaid infrastructure funding is insufficient."
            )

    def core(self, guild: int, user: int):
        """Bound local, code-based operations without a paid entitlement."""
        integer(guild, 1)
        integer(user, 1)
        now = time.monotonic()
        key = (guild, user)
        if now < self._recent.get(key, 0):
            raise Denied("Please wait a moment before trying again.")
        if len(self._recent) >= 2000:
            self._recent = {k: v for k, v in self._recent.items() if v > now}
            if len(self._recent) >= 2000:
                raise Denied("The service is busy. Please try again shortly.")
        self._recent[key] = now + 1

    def model_gateway(self) -> Gateway:
        if ai_disabled(parse_ai_access_mode(os.environ), "paid gateway initialization"):
            raise Denied("I can't do that right now.")
        self.ready()
        if self.gateway is None:
            from openai import AsyncOpenAI

            key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY")
            if not key:
                raise Denied("The paid AI service is not configured.")
            self.gateway = Gateway(
                self.ledger,
                AsyncOpenAI(
                    api_key=key, base_url="https://api.openai.com/v1", max_retries=0, timeout=45
                ),
                ready=self.ready,
            )
        return self.gateway


def runtime() -> Runtime:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = Runtime(
            Ledger(os.getenv("PREPAID_DATABASE_PATH", "data/prepaid.sqlite3")),
            os.getenv("PREPAID_APPROVAL_PATH", "data/prepaid-approval.json"),
        )
    return _INSTANCE


class PaidManager:
    """Preserve provider menu metadata, but never delegate an unmetered API call."""

    def __init__(self, legacy, rt: Runtime):
        self.legacy = legacy
        self.rt = rt

    def __getattr__(self, key):
        return getattr(self.legacy, key)

    async def complete(
        self, messages, *, provider_type=None, model=None, images=(), web_search=False, **kwargs
    ):
        scope = SCOPE.get()
        if scope is None:
            raise Denied("No prepaid server identity is attached to this request.")
        if model not in (None, "auto", "gpt-6-luna") or getattr(
            provider_type, "value", provider_type
        ) not in (None, "openai"):
            raise Denied("This model is not included in a metered plan.")
        # Native search is an explicit add-on action, never automatically consumed
        # because a legacy flag globally enables search.
        search = bool(kwargs.get("require_web_search", False))
        result = await self.rt.model_gateway().complete(
            scope[0],
            scope[2],
            messages,
            reasoning=bool(kwargs.get("reasoning_requested", False)),
            search=search,
            images=images,
        )
        from src.providers import CompletionResult, ProviderType

        return CompletionResult(
            result,
            ProviderType.OPENAI,
            "gpt-4.1-mini" if search else "gpt-6-luna",
            (ProviderType.OPENAI,),
        )

    async def close(self):
        await self.legacy.close()
        if self.rt.gateway is not None:
            await self.rt.gateway.client.close()


class PaidWeb:
    """No separately billed search engines or unaccounted file-download features."""

    def __init__(self, legacy):
        self.legacy = legacy

    async def search(self, *args, **kwargs):
        raise Denied(
            "Use an explicit metered web search; this external search provider is disabled."
        )

    async def browse(self, *args, **kwargs):
        scope = SCOPE.get()
        if scope is None:
            raise Denied("This web request has no prepaid server identity.")
        runtime().ready()
        return await self.legacy.browse(*args, **kwargs)

    async def image(self, *args, **kwargs):
        raise Denied("External image downloads are not an approved metered feature.")

    async def close(self):
        await self.legacy.close()


def storage_transaction(method):
    """Single-process serialization across quota reservation and SQLite commit.

    Paid deployment must have one writer/process. Native replicas must remain one;
    multi-process accounting/storage migration is a separate launch requirement.
    """

    @wraps(method)
    def wrapped(*args, **kwargs):
        if not enforcing():
            return method(*args, **kwargs)
        with STORAGE_LOCK:
            try:
                return method(*args, **kwargs)
            except BaseException:
                connection = getattr(args[0], "_connection", None)
                if connection is not None:
                    connection.rollback()
                raise

    return wrapped


def reserve_table(db, guild: int, kind: str):
    """Reserve prospective serialized payload+row metadata before DB commit.

    A failed commit deliberately leaves a conservative reservation. A later
    successful operation reconciles it. Local profile/game writes have a
    bounded free allowance; chat and excess storage need paid launch readiness.
    """
    if not enforcing():
        return None
    rt = runtime()
    queries = {
        "profiles": "SELECT COALESCE(SUM(length(CAST(payload AS BLOB))+256),0) FROM member_settings WHERE guild_id=?",
        "chat": "SELECT COALESCE(SUM(length(CAST(content AS BLOB))+256),0) FROM chat_turns WHERE guild_id=?",
        "gaming": "SELECT COALESCE(SUM(n),0) FROM (SELECT length(CAST(display_name AS BLOB))+length(CAST(role AS BLOB))+256 n FROM party_players WHERE guild_id=? UNION ALL SELECT length(CAST(display_name AS BLOB))+256 n FROM steam_links WHERE guild_id=?)",
    }
    if kind not in queries:
        raise Denied("Unmetered storage category.")
    size = db.execute(queries[kind], (guild, guild) if kind == "gaming" else (guild,)).fetchone()[0]
    with rt.ledger.db() as ledger_db:
        row = ledger_db.execute(
            "SELECT bytes FROM prepaid_storage WHERE guild=? AND object_key=?", (guild, kind)
        ).fetchone()
        old = row[0] if row else 0
    if size > old:
        if kind == "chat":
            rt.ready()
        else:
            with rt.ledger.db() as ledger_db:
                other = ledger_db.execute(
                    "SELECT COALESCE(SUM(bytes),0) FROM prepaid_storage "
                    "WHERE guild=? AND object_key IN ('profiles','gaming') AND object_key!=?",
                    (guild, kind),
                ).fetchone()[0]
            if other + size > FREE_STORAGE_BYTES:
                rt.ready()
        rt.ledger.storage(guild, kind, size)
    return (guild, kind, size)


def finish_table(ticket):
    # Called only after a successful commit, while storage_transaction owns lock.
    if ticket is not None:
        runtime().ledger.storage(*ticket)


def limit_database(db):
    if enforcing():
        db.execute("PRAGMA max_page_count=65536")  # 256 MiB at 4KiB/page per store
        db.execute("PRAGMA journal_size_limit=1048576")


def free_interaction_allowed(user_id: int, event_id: int | None = None) -> bool:
    """Bound housekeeping/autocomplete CPU without charging for privacy access."""
    if not enforcing():
        return True
    rt = runtime()
    now = time.monotonic()
    if event_id is not None:
        event_key = (user_id, event_id)
        if rt._housekeeping_events.get(event_key, 0) > now:
            return True
        rt._housekeeping_events = {k: v for k, v in rt._housekeeping_events.items() if v > now}
        if len(rt._housekeeping_events) >= 2000:
            return False
    bucket = getattr(rt, "_housekeeping", None)
    if bucket is None:
        bucket = rt._housekeeping = {}
    start, total = getattr(rt, "_housekeeping_global", (now, 0))
    if now - start >= 1:
        start, total = now, 0
    if total >= 30:
        return False
    previous = bucket.get(user_id, 0)
    if now < previous:
        return False
    if len(bucket) > 2000:
        bucket = {k: v for k, v in bucket.items() if v > now}
        rt._housekeeping = bucket
        if len(bucket) > 2000:
            return False
    bucket[user_id] = now + 0.25
    rt._housekeeping_global = (start, total + 1)
    if event_id is not None:
        rt._housekeeping_events[(user_id, event_id)] = now + 3
    return True
