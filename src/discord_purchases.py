"""Authenticated Discord access reconciliation with separately reviewed settlement facts.

Discord entitlements are access evidence, not payment receipts. This adapter only
credits a prepaid grant after a complete authenticated snapshot and an operator
reviewed, period-specific settlement record agree on every identity and date.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import discord

from src.prepaid import PRODUCTS, Denied, Ledger, Payment, integer

MAX_ENTITLEMENTS = 10000
MAX_SUBSCRIPTIONS_PER_USER = 1000
SNAPSHOT_MAX_AGE_SECONDS = 300


class PurchaseDenied(Denied):
    """Keep operator diagnostics separate from member-visible failure text."""

    def __init__(self, operator_reason: str):
        self.operator_reason = operator_reason
        super().__init__("I can't do that right now.")


def purchase_mode(value: str) -> str:
    if value not in {"off", "observe", "enforce"}:
        raise Denied("Invalid Discord purchase mode.")
    return value


def sku_map(raw: str) -> dict[int, str]:
    """Parse explicit SKU to product mappings; no name or price guessing."""
    if not raw.strip():
        return {}
    result: dict[int, str] = {}
    products: set[str] = set()
    for pair in raw.split(","):
        parts = pair.strip().split(":")
        if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) <= 0:
            raise Denied("Invalid Discord SKU map.")
        sku_id, product = int(parts[0]), parts[1].strip()
        if (
            sku_id in result
            or product not in PRODUCTS
            or product in products
            or PRODUCTS[product].kind != "subscription"
        ):
            raise Denied("Invalid or duplicate Discord SKU mapping.")
        result[sku_id] = product
        products.add(product)
    return result


def _seconds(value: datetime | None) -> int:
    if value is None or value.tzinfo is None:
        raise Denied("Discord purchase has no UTC period.")
    return int(value.timestamp())


def _identity(application_id: int, mapping: dict[int, str], *, credit: bool) -> str:
    return hashlib.sha256(
        json.dumps(
            [application_id, sorted(mapping.items()), credit], separators=(",", ":")
        ).encode()
    ).hexdigest()


def assert_purchase_current(
    ledger: Ledger,
    application_id: int,
    mapping: dict[int, str],
    *,
    clock: Callable[[], float] = time.time,
) -> None:
    """Fail closed if the last complete snapshot is stale or used another SKU map."""
    try:
        with ledger.db() as db:
            rows = db.execute(
                "SELECT key,value FROM discord_purchase_state WHERE key IN "
                "('complete_snapshot_at','snapshot_identity')"
            ).fetchall()
        state = {row["key"]: row["value"] for row in rows}
        stamp = int(state["complete_snapshot_at"])
        if (
            state["snapshot_identity"] == _identity(application_id, mapping, credit=True)
            and 0 <= clock() - stamp <= SNAPSHOT_MAX_AGE_SECONDS
        ):
            _assert_grants_bound(ledger, mapping, int(clock()))
            return
    except (sqlite3.Error, KeyError, ValueError):
        pass
    raise PurchaseDenied("Discord purchase reconciliation is stale or mismatched.")


def _assert_grants_bound(ledger: Ledger, mapping: dict[int, str], now: int) -> None:
    """No active manually inserted grant may bypass Discord access matching."""
    with ledger.db() as db:
        rows = db.execute(
            """
            SELECT g.receipt,g.payment,s.facts,i.receipt_id AS invalid_id,
                   a.entitlement_id AS access_id,
                   a.subscription_id,a.guild_id,a.sku_id,a.product,a.starts,a.ends
            FROM prepaid_grants g
            LEFT JOIN discord_purchase_settlement s ON s.receipt_id=g.receipt
            LEFT JOIN discord_purchase_invalid i ON i.receipt_id=g.receipt
            LEFT JOIN discord_purchase_access a ON a.entitlement_id=s.entitlement_id
            WHERE g.revoked=0 AND g.starts<=? AND g.ends>?
        """,
            (now, now),
        ).fetchall()
    for row in rows:
        try:
            facts = Settlement(**json.loads(row["facts"]))
            access = Access(
                row["access_id"],
                row["subscription_id"],
                row["guild_id"],
                0,
                row["sku_id"],
                row["product"],
                row["starts"],
                row["ends"],
            )
            payment = json.loads(row["payment"])
            expected = Payment(
                facts.receipt_id,
                facts.guild_id,
                facts.product,
                facts.gross_micros,
                facts.net_micros,
                facts.starts,
                facts.ends,
                "verified_payment",
                facts.currency,
            )
            if (
                row["invalid_id"] is not None
                or facts.product != mapping.get(facts.sku_id)
                or not DiscordPurchases._matches(access, facts)
                or payment != expected.__dict__
            ):
                raise ValueError()
        except (TypeError, KeyError, ValueError):
            raise PurchaseDenied(
                "An active grant lacks matching reviewed Discord purchase evidence."
            ) from None


@dataclass(frozen=True)
class Access:
    entitlement_id: int
    subscription_id: int
    guild_id: int
    user_id: int
    sku_id: int
    product: str
    starts: int
    ends: int


@dataclass(frozen=True)
class Settlement:
    """Facts transcribed from independently inspected financial evidence."""

    receipt_id: str
    entitlement_id: int
    subscription_id: int
    guild_id: int
    sku_id: int
    product: str
    starts: int
    ends: int
    gross_micros: int
    net_micros: int
    currency: str
    evidence_sha256: str
    evidence_reference: str
    reviewed_by: str


class DiscordPurchases:
    def __init__(
        self,
        ledger: Ledger,
        mapping: dict[int, str],
        *,
        application_id: int,
        clock: Callable[[], float] = time.time,
    ):
        self.ledger = ledger
        self.mapping = mapping
        self.application_id = integer(application_id, 1)
        self.clock = clock
        self._generation = 0
        with ledger.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS discord_purchase_state(
              key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS discord_purchase_access(
              entitlement_id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL,
              guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
              sku_id INTEGER NOT NULL, product TEXT NOT NULL,
              starts INTEGER NOT NULL, ends INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS discord_purchase_settlement(
              receipt_id TEXT PRIMARY KEY, facts TEXT NOT NULL,
              entitlement_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
              starts INTEGER NOT NULL, ends INTEGER NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS discord_purchase_period
              ON discord_purchase_settlement(entitlement_id,starts,ends);
            CREATE TABLE IF NOT EXISTS discord_purchase_invalid(
              receipt_id TEXT PRIMARY KEY, reason TEXT NOT NULL,
              evidence_sha256 TEXT NOT NULL, evidence_reference TEXT NOT NULL,
              reviewed_by TEXT NOT NULL, invalidated_at INTEGER NOT NULL);
            """)

    def import_reviewed_settlement(self, record: Settlement) -> bool:
        """Operator boundary. Record evidence provenance without trusting Discord for money.

        The importer cannot authenticate the external invoice on its own. A reviewer
        must inspect the source and supply a unique paid invoice/transaction ID.
        """
        for value in (
            record.entitlement_id,
            record.subscription_id,
            record.guild_id,
            record.sku_id,
            record.starts,
            record.ends,
            record.gross_micros,
            record.net_micros,
        ):
            integer(value, 1)
        if (
            not 1 <= len(record.receipt_id) <= 180
            or record.product != self.mapping.get(record.sku_id)
            or record.currency != "USD"
            or record.gross_micros != PRODUCTS[record.product].price_cents * 10000
            or not record.gross_micros * 65 // 100 <= record.net_micros <= record.gross_micros
            or not record.starts < record.ends <= self.clock() + 32 * 86400
            or record.ends - record.starts > 32 * 86400
            or len(record.evidence_sha256) != 64
            or any(c not in "0123456789abcdef" for c in record.evidence_sha256)
            or not 1 <= len(record.evidence_reference.strip()) <= 300
            or not 1 <= len(record.reviewed_by.strip()) <= 120
        ):
            raise Denied("Settlement evidence does not fund this offering.")
        facts = json.dumps(record.__dict__, sort_keys=True, separators=(",", ":"))
        with self.ledger.db() as db:
            if db.execute(
                "SELECT 1 FROM discord_purchase_invalid WHERE receipt_id=?", (record.receipt_id,)
            ).fetchone():
                raise Denied("This paid receipt was invalidated and cannot be restored.")
            old = db.execute(
                "SELECT facts FROM discord_purchase_settlement WHERE receipt_id=?",
                (record.receipt_id,),
            ).fetchone()
            if old:
                if old["facts"] != facts:
                    raise Denied("Paid receipt already assigned to different facts.")
                return False
            try:
                db.execute(
                    "INSERT INTO discord_purchase_settlement VALUES(?,?,?,?,?,?)",
                    (
                        record.receipt_id,
                        facts,
                        record.entitlement_id,
                        record.guild_id,
                        record.starts,
                        record.ends,
                    ),
                )
            except Exception as exc:
                raise Denied("Billing period already has a reviewed receipt.") from exc
        return True

    def invalidate_settlement(
        self,
        receipt_id: str,
        reason: str,
        evidence_sha256: str,
        evidence_reference: str,
        reviewed_by: str,
    ) -> bool:
        """Persist reviewed refund/chargeback evidence; never restore a tombstoned receipt."""
        if (
            not isinstance(receipt_id, str)
            or not 1 <= len(receipt_id) <= 180
            or reason not in {"refund", "chargeback", "payment_reversal"}
            or len(evidence_sha256) != 64
            or any(c not in "0123456789abcdef" for c in evidence_sha256)
            or not 1 <= len(evidence_reference.strip()) <= 300
            or not 1 <= len(reviewed_by.strip()) <= 120
        ):
            raise Denied("Invalid reviewed payment reversal evidence.")
        fields = (reason, evidence_sha256, evidence_reference, reviewed_by)
        with self.ledger.db() as db:
            if not db.execute(
                "SELECT 1 FROM discord_purchase_settlement WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone():
                raise Denied("No reviewed Discord settlement has this receipt ID.")
            old = db.execute(
                "SELECT reason,evidence_sha256,evidence_reference,reviewed_by "
                "FROM discord_purchase_invalid WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if old:
                if tuple(old) != fields:
                    raise Denied("Payment reversal already has different reviewed evidence.")
                inserted = False
            else:
                db.execute(
                    "INSERT INTO discord_purchase_invalid VALUES(?,?,?,?,?,?)",
                    (receipt_id, *fields, int(self.clock())),
                )
                inserted = True
            db.execute(
                "INSERT OR REPLACE INTO discord_purchase_state VALUES(?,?)",
                ("complete_snapshot_at", "0"),
            )
            db.execute("UPDATE prepaid_grants SET revoked=1 WHERE receipt=?", (receipt_id,))
        return inserted

    def assert_current(self) -> None:
        assert_purchase_current(self.ledger, self.application_id, self.mapping, clock=self.clock)

    def invalidate(self) -> None:
        """Gateway events invalidate old access before the REST refresh starts."""
        self._generation += 1
        with self.ledger.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO discord_purchase_state VALUES(?,?)",
                ("complete_snapshot_at", "0"),
            )

    async def _fetch_access(self, client: discord.Client) -> tuple[list[Access], int]:
        skus = {sku.id: sku for sku in await client.fetch_skus()}
        candidate_by_entitlement: dict[int, list[Settlement]] = {}
        for facts in self._settlements():
            candidate_by_entitlement.setdefault(facts.entitlement_id, []).append(facts)
        for sku_id, product in self.mapping.items():
            sku = skus.get(sku_id)
            if (
                sku is None
                or sku.application_id != self.application_id
                or not sku.flags.guild_subscription
                or sku.type != discord.SKUType.subscription
                or PRODUCTS[product].kind != "subscription"
            ):
                raise Denied("Mapped Discord SKU is not a guild subscription.")
        seen: set[int] = set()
        access: list[Access] = []
        pending = 0
        count = 0
        now = int(self.clock())
        async for ent in client.entitlements(
            limit=None, exclude_ended=False, exclude_deleted=False
        ):
            count += 1
            if count > MAX_ENTITLEMENTS or ent.id in seen:
                raise Denied("Discord entitlement snapshot is incomplete or duplicated.")
            seen.add(ent.id)
            if ent.sku_id not in self.mapping:
                continue
            if ent.application_id != self.application_id:
                raise Denied("Discord entitlement belongs to another application.")
            if (
                ent.type != discord.EntitlementType.application_subscription
                or ent.deleted
                or ent.guild_id is None
                or ent.starts_at is None
                or ent.ends_at is None
            ):
                continue
            starts, ends = _seconds(ent.starts_at), _seconds(ent.ends_at)
            if not starts <= now < ends:
                continue
            sku = skus[ent.sku_id]
            matches = []
            candidates = [
                facts
                for facts in candidate_by_entitlement.get(ent.id, [])
                if facts.starts <= now < facts.ends
            ]
            if len(candidates) > 1:
                raise Denied("Multiple settlement candidates claim one billing period.")
            if candidates:
                matches.append(await sku.fetch_subscription(candidates[0].subscription_id))
            elif ent.user_id is not None:
                sub_count = 0
                async for sub in sku.subscriptions(limit=None, user=discord.Object(id=ent.user_id)):
                    sub_count += 1
                    if sub_count > MAX_SUBSCRIPTIONS_PER_USER:
                        raise Denied("Discord subscription snapshot is incomplete.")
                    if ent.id in sub.entitlement_ids and ent.sku_id in sub.sku_ids:
                        matches.append(sub)
            else:
                pending += 1
                continue
            if len(matches) != 1:
                raise Denied("Discord entitlement has no unique subscription.")
            sub = matches[0]
            period_start, period_end = (
                _seconds(sub.current_period_start),
                _seconds(sub.current_period_end),
            )
            if (
                sub.status
                not in (discord.SubscriptionStatus.active, discord.SubscriptionStatus.ending)
                or (ent.user_id is not None and sub.user_id != ent.user_id)
                or ent.id not in sub.entitlement_ids
                or ent.sku_id not in sub.sku_ids
                or not starts <= period_start <= now < period_end <= ends
                or period_end - period_start > 32 * 86400
            ):
                continue
            access.append(
                Access(
                    ent.id,
                    sub.id,
                    ent.guild_id,
                    sub.user_id,
                    ent.sku_id,
                    self.mapping[ent.sku_id],
                    period_start,
                    period_end,
                )
            )
        return access, pending

    async def reconcile(self, client: discord.Client, *, credit: bool) -> dict[str, int]:
        """Only a completed REST walk changes access or credits reviewed receipts."""
        self.invalidate()
        if client.application_id != self.application_id:
            raise Denied("Discord application identity does not match configuration.")
        if credit and (
            os.getenv("PREPAID_MODE", "off") != "enforce"
            or os.getenv("DISCORD_PURCHASE_MODE", "off") != "enforce"
        ):
            raise Denied("Discord credits require reviewed commercial enforcement mode.")
        generation = self._generation
        access, pending = await self._fetch_access(client)
        if self._generation != generation:
            raise Denied("Discord purchase changed during reconciliation; retry required.")
        seen = {row.entitlement_id: row for row in access}
        if self._generation != generation:
            raise Denied("Discord purchase changed during reconciliation; retry required.")
        with self.ledger.db() as db:
            db.execute("DELETE FROM discord_purchase_access")
            db.executemany(
                "INSERT INTO discord_purchase_access VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        r.entitlement_id,
                        r.subscription_id,
                        r.guild_id,
                        r.user_id,
                        r.sku_id,
                        r.product,
                        r.starts,
                        r.ends,
                    )
                    for r in access
                ],
            )
            grants = db.execute(
                "SELECT receipt,payment FROM prepaid_grants WHERE revoked=0"
            ).fetchall()
        # Revoke only adapter-owned grants. A missing current period also revokes a
        # previous period's still-valid grant after a renewal or early refund.
        revoked = 0
        for grant in grants if credit else []:
            receipt = grant["receipt"]
            with self.ledger.db() as db:
                row = db.execute(
                    "SELECT facts FROM discord_purchase_settlement WHERE receipt_id=?",
                    (receipt,),
                ).fetchone()
            if row:
                facts = Settlement(**json.loads(row["facts"]))
                with self.ledger.db() as db:
                    invalid = db.execute(
                        "SELECT 1 FROM discord_purchase_invalid WHERE receipt_id=?",
                        (receipt,),
                    ).fetchone()
                if invalid or not self._matches(seen.get(facts.entitlement_id), facts):
                    self.ledger.revoke(receipt)
                    revoked += 1
        credited = 0
        if credit:
            for facts in self._settlements():
                if self._matches(seen.get(facts.entitlement_id), facts):
                    payment = Payment(
                        facts.receipt_id,
                        facts.guild_id,
                        facts.product,
                        facts.gross_micros,
                        facts.net_micros,
                        facts.starts,
                        facts.ends,
                        "verified_payment",
                        facts.currency,
                    )
                    if self.ledger.credit(payment):
                        credited += 1
        if credit:
            with self.ledger.db() as db:
                active = db.execute(
                    "SELECT g.receipt FROM prepaid_grants g "
                    "LEFT JOIN discord_purchase_settlement s ON s.receipt_id=g.receipt "
                    "WHERE g.revoked=0 AND g.starts<=? AND g.ends>? "
                    "AND s.receipt_id IS NULL LIMIT 1",
                    (int(self.clock()), int(self.clock())),
                ).fetchone()
            if active:
                raise Denied("Existing active grant needs audited Discord purchase migration.")
            _assert_grants_bound(self.ledger, self.mapping, int(self.clock()))
        with self.ledger.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO discord_purchase_state VALUES(?,?)",
                ("complete_snapshot_at", str(int(self.clock()))),
            )
            db.execute(
                "INSERT OR REPLACE INTO discord_purchase_state VALUES(?,?)",
                ("snapshot_identity", _identity(self.application_id, self.mapping, credit=credit)),
            )
        return {
            "observed": len(access),
            "pending": pending,
            "credited": credited,
            "revoked": revoked,
        }

    def _settlements(self) -> list[Settlement]:
        with self.ledger.db() as db:
            rows = db.execute(
                "SELECT s.facts FROM discord_purchase_settlement s "
                "LEFT JOIN discord_purchase_invalid i "
                "ON i.receipt_id=s.receipt_id WHERE i.receipt_id IS NULL"
            ).fetchall()
        return [Settlement(**json.loads(row["facts"])) for row in rows]

    @staticmethod
    def _matches(access: Access | None, facts: Settlement) -> bool:
        return access is not None and (
            access.entitlement_id == facts.entitlement_id
            and access.subscription_id == facts.subscription_id
            and access.guild_id == facts.guild_id
            and access.sku_id == facts.sku_id
            and access.product == facts.product
            and access.starts == facts.starts
            and access.ends == facts.ends
        )


def evidence_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
