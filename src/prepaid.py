"""Prepaid server allowances. Money is integer USD microdollars, never floats.

This is the accounting boundary, NOT payment verification. Only a trusted receipt
adapter may call credit(); Discord entitlements/test purchases alone do not prove
payment amount, currency, settlement or renewal. No Discord command grants credit.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import time
import uuid
import weakref
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Condition, Lock, get_ident
from typing import Callable


class Denied(ValueError):
    """A safe denial to show privately; no provider credentials or account costs."""


# Conservative per-attempt provider reservations, not customer currency prices.
# Search uses a separately pinned, fixed-block web-search contract. Image is one
# 1024-square medium GPT Image 2 image + at most 2,000 UTF-8 prompt bytes.
COSTS = {"chat": 1500, "reasoning": 3000, "search": 40000, "images": 80000, "core": 50}
MIB = 1024**2
FREE_STORAGE_BYTES = MIB  # Combined profile and gaming records per server.
SQLITE_BUSY_TIMEOUT_MS = 250
SQLITE_BODY_BUSY_TIMEOUT_MS = 10000
SQLITE_BEGIN_RETRY_SECONDS = 30
SQLITE_ADMISSION_TIMEOUT_SECONDS = 30


class _Admission:
    """Give local callers one turn each before competing for SQLite's writer lock."""

    def __init__(self):
        self.condition = Condition()
        self.waiters: deque[object] = deque()
        self.owner: int | None = None

    @contextmanager
    def enter(self):
        ticket = object()
        deadline = time.monotonic() + SQLITE_ADMISSION_TIMEOUT_SECONDS
        with self.condition:
            if self.owner == get_ident():
                raise RuntimeError("Nested prepaid transactions on one database are unsupported")
            self.waiters.append(ticket)
            try:
                while self.waiters[0] is not ticket:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise sqlite3.OperationalError(
                            "Timed out waiting for local prepaid database admission"
                        )
                    self.condition.wait(timeout=remaining)
            except BaseException:
                self.waiters.remove(ticket)
                self.condition.notify_all()
                raise
            self.owner = get_ident()
        try:
            yield
        finally:
            with self.condition:
                self.owner = None
                self.waiters.popleft()
                self.condition.notify_all()


_admissions_guard = Lock()
_admissions: weakref.WeakValueDictionary[str, _Admission] = weakref.WeakValueDictionary()


@contextmanager
def _database_admission(path):
    # Resolve aliases within this process; SQLite remains the cross-process lock.
    key = os.path.normcase(str(Path(path).resolve()))
    with _admissions_guard:
        admission = _admissions.get(key)
        if admission is None:
            admission = _Admission()
            _admissions[key] = admission
    with admission.enter():
        yield


@dataclass(frozen=True)
class Product:
    name: str
    price_cents: int
    kind: str
    allowances: dict[str, int]
    storage_bytes: int = 0

    @property
    def max_api_cost(self) -> int:
        return sum(COSTS[k] * n for k, n in self.allowances.items() if k != "core")


PRODUCTS = {
    "basic": Product("Basic", 99, "subscription", {"chat": 100, "core": 1000}, 1 * MIB),
    "plus": Product(
        "Plus",
        499,
        "subscription",
        {"chat": 500, "reasoning": 50, "search": 10, "images": 3, "core": 5000},
        20 * MIB,
    ),
    "premium": Product(
        "Premium",
        999,
        "subscription",
        {"chat": 1000, "reasoning": 100, "search": 20, "images": 10, "core": 10000},
        100 * MIB,
    ),
    "chat_pack": Product("Extra chat", 99, "add-on", {"chat": 200, "core": 200}),
    "reasoning_pack": Product("Extra reasoning", 99, "add-on", {"reasoning": 100, "core": 100}),
    "search_pack": Product("Web search pack", 199, "add-on", {"search": 15, "core": 30}),
    "image_pack": Product("Image pack", 299, "add-on", {"images": 12, "core": 12}),
    "storage_pack": Product("Storage boost (100 MiB, 30 days)", 99, "add-on", {}, 100 * MIB),
}


def validate_products() -> None:
    for p in PRODUCTS.values():
        if p.price_cents < 99 or p.max_api_cost > p.price_cents * 3500:
            raise ValueError("Product violates price floor or provider-cost envelope")
        # 3x volume price allows metadata/backups/grace, separate from AI funds.
        infra = p.allowances.get("core", 0) * COSTS["core"] + p.storage_bytes * 450000 // 1024**3
        if infra > p.price_cents * 1000:
            raise ValueError("Product violates infrastructure allocation")


@dataclass(frozen=True)
class Payment:
    receipt_id: str
    guild_id: int
    product: str
    gross_micros: int
    net_micros: int
    starts: int
    ends: int
    source: str
    currency: str


def integer(value: object, minimum=0) -> int:
    if type(value) is not int or value < minimum or value > 2**63 - 1:
        raise Denied("Invalid allowance value.")
    return value


class Ledger:
    def __init__(self, path: str, *, clock: Callable[[], float] = time.time, storage_cap=1024**3):
        if not path or path == ":memory:":
            raise ValueError("Prepaid accounting requires durable storage")
        self.path = str(path)
        self.clock = clock
        self.storage_cap = integer(storage_cap, 1)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        validate_products()
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS prepaid_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS prepaid_grants(
              receipt TEXT PRIMARY KEY,guild INTEGER NOT NULL,product TEXT NOT NULL,
              starts INTEGER NOT NULL,ends INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,
              payment TEXT NOT NULL,limits TEXT NOT NULL,storage INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS prepaid_guild ON prepaid_grants(guild,starts,ends);
            CREATE TABLE IF NOT EXISTS prepaid_requests(
              id TEXT PRIMARY KEY,receipt TEXT NOT NULL,guild INTEGER NOT NULL,user_id INTEGER NOT NULL,
              feature TEXT NOT NULL,reserved INTEGER NOT NULL,actual INTEGER,
              state TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS prepaid_usage ON prepaid_requests(receipt,feature,state);
            CREATE TABLE IF NOT EXISTS prepaid_storage(
              guild INTEGER NOT NULL,object_key TEXT NOT NULL,bytes INTEGER NOT NULL,
              PRIMARY KEY(guild,object_key));
            """)

    @contextmanager
    def db(self):
        with _database_admission(self.path):
            with self._transaction() as db:
                yield db

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            deadline = time.monotonic() + SQLITE_BEGIN_RETRY_SECONDS
            delay = 0.005

            def execute_with_retry(statement):
                nonlocal delay
                while True:
                    try:
                        db.execute(statement)
                        return
                    except sqlite3.OperationalError as exc:
                        code = getattr(exc, "sqlite_errorcode", None)
                        if code is None or (code & 0xFF) not in (
                            sqlite3.SQLITE_BUSY,
                            sqlite3.SQLITE_LOCKED,
                        ):
                            raise
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise
                        time.sleep(min(random.uniform(delay / 2, delay), remaining))
                        delay = min(delay * 2, 0.1)

            # Retry lock-sensitive setup and transaction boundaries under one
            # bounded deadline. Only COMMIT is retried after the body has run.
            db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            execute_with_retry("PRAGMA synchronous=FULL")
            # 128 MiB hard ceiling on accounting files; full storage denies new work.
            execute_with_retry("PRAGMA max_page_count=32768")
            execute_with_retry("BEGIN IMMEDIATE")
            # Preserve the existing tolerance for locks in the transaction body,
            # including schema initialization via executescript().
            db.execute(f"PRAGMA busy_timeout={SQLITE_BODY_BUSY_TIMEOUT_MS}")
            yield db
            if db.in_transaction:
                db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
                execute_with_retry("COMMIT")
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def credit(self, p: Payment) -> bool:
        """Call ONLY after authenticated server-side payment verification.

        The caller must supply a unique PAID INVOICE/transaction ID per renewal,
        not a reusable subscription ID. Discount/test transactions cannot mint
        full-price limits. No free trial is implicitly funded by this method.
        """
        integer(p.guild_id, 1)
        integer(p.starts)
        integer(p.ends)
        integer(p.gross_micros, 1)
        integer(p.net_micros, 1)
        product = PRODUCTS.get(p.product)
        if (
            not product
            or p.source != "verified_payment"
            or p.currency != "USD"
            or not isinstance(p.receipt_id, str)
            or not 1 <= len(p.receipt_id) <= 180
            or not p.starts < p.ends
            or p.ends <= self.clock()
            or p.ends - p.starts > 32 * 86400
            or p.gross_micros != product.price_cents * 10000
            or not p.gross_micros * 65 // 100 <= p.net_micros <= p.gross_micros
        ):
            raise Denied("Payment does not fund this offering. Nothing was credited.")
        if product.kind == "add-on" and p.ends - p.starts > 30 * 86400 + 1:
            raise Denied("Add-ons last at most 30 days.")
        encoded = json.dumps(asdict(p), sort_keys=True, separators=(",", ":"))
        with self.db() as db:
            old = db.execute(
                "SELECT payment FROM prepaid_grants WHERE receipt=?", (p.receipt_id,)
            ).fetchone()
            if old:
                if old["payment"] != encoded:
                    raise Denied("Receipt already assigned.")
                return False
            db.execute(
                "INSERT INTO prepaid_grants(receipt,guild,product,starts,ends,payment,limits,storage) VALUES(?,?,?,?,?,?,?,?)",
                (
                    p.receipt_id,
                    p.guild_id,
                    p.product,
                    p.starts,
                    p.ends,
                    encoded,
                    json.dumps(product.allowances),
                    product.storage_bytes,
                ),
            )
        return True

    def revoke(self, receipt_id: str) -> None:
        # Do not restore already dispatched costs. A refund cannot unspend an API call.
        with self.db() as db:
            db.execute("UPDATE prepaid_grants SET revoked=1 WHERE receipt=?", (receipt_id,))

    def _active(self, db, guild):
        integer(guild, 1)
        now = int(self.clock())
        rows = db.execute(
            "SELECT * FROM prepaid_grants WHERE guild=? AND revoked=0 AND starts<=? AND ends>? ORDER BY ends,receipt",
            (guild, now, now),
        ).fetchall()
        if not any(PRODUCTS[r["product"]].kind == "subscription" for r in rows):
            raise Denied("This server needs an active paid plan. See /plans.")
        return rows

    def assert_active(self, guild):
        with self.db() as db:
            self._active(db, guild)

    def reserve(self, guild: int, user: int, feature: str) -> str:
        integer(user, 1)
        if feature not in COSTS:
            raise Denied("This feature has no approved usage limit.")
        with self.db() as db:
            if db.execute("SELECT 1 FROM prepaid_state WHERE key='locked'").fetchone():
                raise Denied("Paid features are paused while usage is reconciled.")
            rows = self._active(db, guild)
            for row in rows:
                pct = 10 if feature == "core" else 35
                comparator = "=" if feature == "core" else "!="
                held = db.execute(
                    f"SELECT COALESCE(SUM(reserved),0) FROM prepaid_requests WHERE receipt=? AND feature{comparator}'core' AND state!='cancelled'",
                    (row["receipt"],),
                ).fetchone()[0]
                paid = json.loads(row["payment"])["gross_micros"]
                if held + COSTS[feature] > paid * pct // 100:
                    continue
                quota = json.loads(row["limits"]).get(feature, 0)
                used = db.execute(
                    "SELECT COUNT(*) FROM prepaid_requests WHERE receipt=? AND feature=? AND state!='cancelled'",
                    (row["receipt"], feature),
                ).fetchone()[0]
                if used >= quota:
                    continue
                rid = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO prepaid_requests VALUES(?,?,?,?,?,?,NULL,?,?)",
                    (
                        rid,
                        row["receipt"],
                        guild,
                        user,
                        feature,
                        COSTS[feature],
                        "reserved",
                        int(self.clock()),
                    ),
                )
                return rid
        label = {
            "core": "Server operations",
            "images": "Images",
            "search": "Web search",
            "reasoning": "Advanced reasoning",
            "chat": "Chat",
        }[feature]
        raise Denied(
            f"{label} allowance is exhausted or not included. See /usage and /plans. No automatic charge was made."
        )

    def dispatch(self, rid: str):
        with self.db() as db:
            row = db.execute(
                "SELECT r.*,g.revoked,g.ends FROM prepaid_requests r JOIN prepaid_grants g ON g.receipt=r.receipt WHERE r.id=?",
                (rid,),
            ).fetchone()
            if (
                not row
                or row["state"] != "reserved"
                or row["revoked"]
                or row["ends"] <= self.clock()
            ):
                raise Denied("This reservation is no longer valid.")
            if db.execute("SELECT 1 FROM prepaid_state WHERE key='locked'").fetchone():
                raise Denied("Paid features are paused.")
            self._active(db, row["guild"])
            db.execute("UPDATE prepaid_requests SET state='dispatched' WHERE id=?", (rid,))

    def cancel(self, rid: str):
        with self.db() as db:
            row = db.execute("SELECT state FROM prepaid_requests WHERE id=?", (rid,)).fetchone()
            if not row or row["state"] != "reserved":
                raise Denied("Dispatched requests cannot be refunded automatically.")
            db.execute("UPDATE prepaid_requests SET state='cancelled' WHERE id=?", (rid,))

    def settle(self, rid: str, actual: int):
        integer(actual)
        breach = False
        with self.db() as db:
            row = db.execute("SELECT * FROM prepaid_requests WHERE id=?", (rid,)).fetchone()
            if not row:
                raise Denied("Unknown usage reservation.")
            if row["state"] == "settled" and row["actual"] == actual:
                return
            if row["state"] != "dispatched":
                raise Denied("Usage is already settled or was never dispatched.")
            breach = actual > row["reserved"]
            if breach:
                db.execute(
                    "INSERT OR REPLACE INTO prepaid_state VALUES('locked','provider_cost_contract_exceeded')"
                )
            db.execute(
                "UPDATE prepaid_requests SET actual=?,state='settled' WHERE id=?", (actual, rid)
            )
        if breach:
            raise Denied("Paid features paused: provider usage exceeded its approved bound.")

    def lock(self, reason="usage_unverifiable"):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO prepaid_state VALUES('locked',?)", (reason[:80],))

    def storage(self, guild: int, key: str, size: int):
        """Reserve BEFORE write; release only AFTER a committed shrink/delete.

        Separate databases intentionally over-reserve on crashes rather than
        silently under-account. Reconciliation must inspect actual stored records.
        """
        integer(guild, 1)
        integer(size)
        if not key or len(key) > 200:
            raise Denied("Invalid storage record.")
        with self.db() as db:
            old = db.execute(
                "SELECT bytes FROM prepaid_storage WHERE guild=? AND object_key=?", (guild, key)
            ).fetchone()
            previous = old[0] if old else 0
            if size > previous:
                try:
                    rows = self._active(db, guild)
                except Denied:
                    rows = []
                used = db.execute(
                    "SELECT COALESCE(SUM(bytes),0) FROM prepaid_storage WHERE guild=?", (guild,)
                ).fetchone()[0]
                free_used = db.execute(
                    "SELECT COALESCE(SUM(bytes),0) FROM prepaid_storage "
                    "WHERE guild=? AND object_key IN ('profiles','gaming')",
                    (guild,),
                ).fetchone()[0]
                all_used = db.execute(
                    "SELECT COALESCE(SUM(bytes),0) FROM prepaid_storage"
                ).fetchone()[0]
                free_record = key in ("profiles", "gaming")
                within_free = free_record and free_used - previous + size <= FREE_STORAGE_BYTES
                paid_limit = sum(r["storage"] for r in rows)
                if (
                    all_used - previous + size > self.storage_cap
                    or (
                        not free_record
                        and (not rows or used - free_used - previous + size > paid_limit)
                    )
                    or (
                        not within_free and used - previous + size > FREE_STORAGE_BYTES + paid_limit
                    )
                ):
                    raise Denied(
                        "Saved-data storage is full. Delete data or add storage; no automatic charge was made."
                    )
            if size:
                db.execute(
                    "INSERT INTO prepaid_storage VALUES(?,?,?) ON CONFLICT(guild,object_key) DO UPDATE SET bytes=excluded.bytes",
                    (guild, key, size),
                )
            else:
                db.execute(
                    "DELETE FROM prepaid_storage WHERE guild=? AND object_key=?", (guild, key)
                )

    def summary(self, guild: int) -> dict:
        with self.db() as db:
            try:
                rows = self._active(db, guild)
            except Denied:
                rows = []
            remaining = {k: 0 for k in COSTS}
            included = dict(remaining)
            for r in rows:
                for f, n in json.loads(r["limits"]).items():
                    used = db.execute(
                        "SELECT COUNT(*) FROM prepaid_requests WHERE receipt=? AND feature=? AND state!='cancelled'",
                        (r["receipt"], f),
                    ).fetchone()[0]
                    included[f] += n
                    remaining[f] += max(0, n - used)
            storage = db.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM prepaid_storage WHERE guild=?", (guild,)
            ).fetchone()[0]
            return {
                "remaining": remaining,
                "included": included,
                "storage_used": storage,
                "storage_limit": FREE_STORAGE_BYTES + sum(r["storage"] for r in rows),
                "plans": sorted({PRODUCTS[r["product"]].name for r in rows}),
                "next_expiry": min((r["ends"] for r in rows), default=None),
            }

    def infrastructure_funding(self) -> int:
        with self.db() as db:
            now = int(self.clock())
            rows = db.execute(
                "SELECT payment FROM prepaid_grants WHERE revoked=0 AND starts<=? AND ends>?",
                (now, now),
            ).fetchall()
            return sum(json.loads(r[0])["gross_micros"] // 10 for r in rows)
