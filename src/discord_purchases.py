"""Authenticated Discord access reconciliation and separate financial evidence.

Entitlement mode grants bounded service from verified Discord access and never
records it as payment. Settlement mode retains reviewed invoice reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

import discord

from src.prepaid import PRODUCTS, AccessGrant, Denied, Ledger, Payment, integer

MAX_ENTITLEMENTS = 10000
MAX_SUBSCRIPTIONS_PER_USER = 1000
SNAPSHOT_MAX_AGE_SECONDS = 300
# Discord's Subscription wire contract is ACTIVE=0, INACTIVE=1, ENDING=2.
# discord.py 2.7.1 labels values 1 and 2 in reverse; compare wire values.
SETTLEMENT_SUBSCRIPTION_STATUSES = {0, 2}


class PurchaseDenied(Denied):
    """Keep operator diagnostics separate from member-visible failure text."""

    def __init__(self, operator_reason: str):
        self.operator_reason = operator_reason
        super().__init__("I can't do that right now.")


def purchase_mode(value: str) -> str:
    if value not in {"off", "observe", "enforce"}:
        raise Denied("Invalid Discord purchase mode.")
    return value


def funding_mode(value: str | None = None) -> str:
    """Settlement is the conservative default; entitlement is explicit opt-in."""
    mode = os.getenv("DISCORD_FUNDING_MODE", "settlement") if value is None else value
    if mode not in {"settlement", "entitlement"}:
        raise Denied("Invalid Discord funding mode.")
    return mode


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


def _identity(application_id: int, mapping: dict[int, str], *, credit: bool, mode: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [application_id, sorted(mapping.items()), credit, mode], separators=(",", ":")
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
        mode = funding_mode()
        if (
            state["snapshot_identity"] == _identity(application_id, mapping, credit=True, mode=mode)
            and 0 <= clock() - stamp <= SNAPSHOT_MAX_AGE_SECONDS
        ):
            _assert_grants_bound(ledger, mapping, int(clock()), mode=mode)
            return
    except (sqlite3.Error, KeyError, ValueError):
        pass
    raise PurchaseDenied("Discord purchase reconciliation is stale or mismatched.")


def _assert_grants_bound(ledger: Ledger, mapping: dict[int, str], now: int, *, mode: str) -> None:
    """No active manually inserted grant may bypass Discord access matching."""
    with ledger.db() as db:
        rows = db.execute(
            """
            SELECT g.receipt,g.payment,g.limits,g.storage,
                   g.guild AS grant_guild,g.product AS grant_product,
                   g.starts AS grant_starts,g.ends AS grant_ends,s.facts,
                   p.payload AS period_payload,
                   i.receipt_id AS invalid_id,
                   a.entitlement_id AS access_id,
                   a.subscription_id,a.guild_id,a.user_id,a.sku_id,a.product,a.starts,a.ends
            FROM prepaid_grants g
            LEFT JOIN discord_purchase_settlement s ON s.receipt_id=g.receipt
            LEFT JOIN discord_access_period p ON p.receipt_id=g.receipt
            LEFT JOIN discord_purchase_invalid i ON i.receipt_id=g.receipt
            LEFT JOIN discord_purchase_access a
              ON a.entitlement_id=COALESCE(s.entitlement_id,p.entitlement_id)
            WHERE g.revoked=0 AND g.starts<=? AND g.ends>?
        """,
            (now, now),
        ).fetchall()
    for row in rows:
        try:
            payment = json.loads(row["payment"])
            if mode == "entitlement":
                grant = AccessGrant(**payment["access"])
                if (
                    payment.get("source") != "discord_entitlement"
                    or row["invalid_id"] is not None
                    or row["receipt"] != grant.receipt_id
                    or row["payment"] != ledger.access_payload(grant)
                    or row["period_payload"] != row["payment"]
                    or grant.product != mapping.get(grant.sku_id)
                    or row["grant_guild"] != grant.guild_id
                    or row["grant_product"] != grant.product
                    or row["grant_starts"] != grant.starts
                    or row["grant_ends"] != grant.ends
                    or json.loads(row["limits"]) != PRODUCTS[grant.product].allowances
                    or row["storage"] != PRODUCTS[grant.product].storage_bytes
                    or row["access_id"] != grant.entitlement_id
                    or row["subscription_id"] != grant.subscription_id
                    or row["guild_id"] != grant.guild_id
                    or row["sku_id"] != grant.sku_id
                    or row["product"] != grant.product
                    or row["starts"] != grant.starts
                    or row["ends"] != grant.ends
                ):
                    raise ValueError()
                continue
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
        except (TypeError, KeyError, ValueError, Denied):
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
            CREATE TABLE IF NOT EXISTS discord_access_period(
              entitlement_id INTEGER NOT NULL, starts INTEGER NOT NULL,
              ends INTEGER NOT NULL, receipt_id TEXT NOT NULL UNIQUE,
              payload TEXT NOT NULL,
              PRIMARY KEY(entitlement_id,starts,ends));
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
            if not (
                db.execute(
                    "SELECT 1 FROM discord_purchase_settlement WHERE receipt_id=?",
                    (receipt_id,),
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM discord_access_period WHERE receipt_id=?",
                    (receipt_id,),
                ).fetchone()
            ):
                raise Denied("No Discord settlement or access period has this receipt ID.")
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

    async def _fetch_access(self, client: discord.Client, *, mode: str) -> tuple[list[Access], int]:
        skus = {sku.id: sku for sku in await client.fetch_skus()}
        candidate_by_entitlement: dict[int, list[Settlement]] = {}
        if mode == "settlement":
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
                ent.type
                not in (
                    discord.EntitlementType.purchase,
                    discord.EntitlementType.application_subscription,
                )
                or ent.deleted
                or ent.guild_id is None
            ):
                continue
            starts = _seconds(ent.starts_at) if ent.starts_at is not None else None
            ends = _seconds(ent.ends_at) if ent.ends_at is not None else None
            if (starts is not None and starts > now) or (ends is not None and now >= ends):
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
            elif mode == "entitlement":
                # discord.py 2.7 drops subscription_id from Entitlement although
                # Discord includes it in its raw entitlement API example. A guild
                # grant can have no user_id, so the user-filtered list cannot help.
                raw = await client.http.get_entitlement(self.application_id, ent.id)
                raw_id = str(raw.get("subscription_id") or "")
                if (
                    str(raw.get("id")) != str(ent.id)
                    or str(raw.get("application_id")) != str(self.application_id)
                    or str(raw.get("sku_id")) != str(ent.sku_id)
                    or str(raw.get("guild_id")) != str(ent.guild_id)
                    or raw.get("deleted") is not False
                    or raw.get("type") != ent.type.value
                ):
                    raise Denied("Discord entitlement detail conflicts with its list result.")
                if not raw_id.isdigit() or int(raw_id) <= 0:
                    pending += 1
                    continue
                matches.append(await sku.fetch_subscription(int(raw_id)))
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
                (
                    mode == "settlement"
                    and getattr(sub.status, "value", sub.status)
                    not in SETTLEMENT_SUBSCRIPTION_STATUSES
                )
                or (ent.user_id is not None and sub.user_id != ent.user_id)
                or ent.id not in sub.entitlement_ids
                or ent.sku_id not in sub.sku_ids
                or not period_start <= now < period_end
                or (starts is not None and period_start < starts)
                or (ends is not None and period_end > ends)
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
        periods: dict[tuple[int, int], int] = {}
        for row in access:
            period = (row.subscription_id, row.starts)
            previous = periods.setdefault(period, row.entitlement_id)
            if previous != row.entitlement_id:
                raise Denied("Discord subscription period has conflicting entitlements.")
        return access, pending

    def _access_history(
        self, db: sqlite3.Connection
    ) -> dict[tuple[str, int, int], set[AccessGrant]]:
        """Validate retained accounting once before resolving any current cycle."""
        history: dict[tuple[str, int, int], set[AccessGrant]] = {}
        by_receipt = {}
        try:
            for period in db.execute("SELECT * FROM discord_access_period").fetchall():
                grant = AccessGrant(**json.loads(period["payload"])["access"])
                if (
                    period["entitlement_id"] != grant.entitlement_id
                    or period["starts"] != grant.starts
                    or period["ends"] != grant.ends
                    or period["receipt_id"] != grant.receipt_id
                    or period["payload"] != self.ledger.access_payload(grant)
                ):
                    raise ValueError()
                by_receipt[grant.receipt_id] = grant
                for kind, identity in (
                    ("entitlement", grant.entitlement_id),
                    ("subscription", grant.subscription_id),
                ):
                    history.setdefault((kind, identity, grant.starts), set()).add(grant)
            for issued in db.execute("SELECT * FROM prepaid_grants").fetchall():
                payment = json.loads(issued["payment"])
                if payment.get("source") != "discord_entitlement":
                    continue
                grant = AccessGrant(**payment["access"])
                if (
                    by_receipt.get(issued["receipt"]) != grant
                    or issued["payment"] != self.ledger.access_payload(grant)
                    or issued["receipt"] != grant.receipt_id
                    or issued["guild"] != grant.guild_id
                    or issued["product"] != grant.product
                    or issued["starts"] != grant.starts
                    or issued["ends"] != grant.ends
                ):
                    # An orphaned old receipt may contain usage or a reversal;
                    # never replace it with a fresh stable-key allowance.
                    raise ValueError()
        except (TypeError, KeyError, ValueError, AttributeError):
            raise Denied(
                "Discord access history changed identity or terms; "
                "audited accounting migration required."
            ) from None
        return history

    def _grant_for_access(
        self, history: dict[tuple[str, int, int], set[AccessGrant]], row: Access
    ) -> AccessGrant:
        """Resolve one issued cycle, retaining any pre-fix receipt/request keys."""
        grant = AccessGrant(
            f"discord-access:{self.application_id}:{row.entitlement_id}:"
            f"{row.subscription_id}:{row.starts}",
            row.guild_id,
            row.product,
            row.starts,
            row.ends,
            self.application_id,
            row.entitlement_id,
            row.subscription_id,
            row.sku_id,
        )
        # End is expiration metadata, not a new billing cycle. Match both IDs
        # so a changed entitlement cannot reissue an existing subscription cycle.
        candidates = history.get(
            ("entitlement", row.entitlement_id, row.starts), set()
        ) | history.get(("subscription", row.subscription_id, row.starts), set())
        if len(candidates) > 1:
            # Never pick the unused receipt or silently discard historical usage.
            raise Denied("Duplicate Discord access cycles need audited accounting migration.")
        if candidates:
            previous = next(iter(candidates))
            grant = replace(grant, receipt_id=previous.receipt_id)
            if replace(previous, ends=grant.ends) != grant:
                raise Denied("Discord access period changed identity or terms.")
        return grant

    def _reconcile_access(self, access: list[Access], pending: int) -> dict[str, int]:
        """Commit the complete access snapshot and period grants as one transaction."""
        now = int(self.clock())
        revoked = credited = 0
        with self.ledger.db() as db:
            history = self._access_history(db)
            grants = [self._grant_for_access(history, row) for row in access]
            desired = {grant.receipt_id for grant in grants}
            rows = db.execute(
                "SELECT receipt,payment,starts,ends FROM prepaid_grants WHERE revoked=0"
            ).fetchall()
            for row in rows:
                basis = json.loads(row["payment"])
                if basis.get("source") != "discord_entitlement":
                    if row["starts"] <= now < row["ends"]:
                        raise Denied(
                            "Existing active grant needs audited Discord access migration."
                        )
                    continue
                if row["receipt"] not in desired:
                    db.execute(
                        "UPDATE prepaid_grants SET revoked=1 WHERE receipt=?",
                        (row["receipt"],),
                    )
                    revoked += 1
            db.execute("DELETE FROM discord_purchase_access")
            db.executemany(
                "INSERT INTO discord_purchase_access VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        row.entitlement_id,
                        row.subscription_id,
                        row.guild_id,
                        row.user_id,
                        row.sku_id,
                        row.product,
                        row.starts,
                        row.ends,
                    )
                    for row in access
                ],
            )
            for grant in grants:
                if db.execute(
                    "SELECT 1 FROM discord_purchase_invalid WHERE receipt_id=?",
                    (grant.receipt_id,),
                ).fetchone():
                    continue
                payload = self.ledger.access_payload(grant)
                old = db.execute(
                    "SELECT receipt_id FROM discord_access_period WHERE receipt_id=?",
                    (grant.receipt_id,),
                ).fetchone()
                if old:
                    db.execute(
                        "UPDATE discord_access_period SET ends=?,payload=? WHERE receipt_id=?",
                        (grant.ends, payload, grant.receipt_id),
                    )
                else:
                    db.execute(
                        "INSERT INTO discord_access_period VALUES(?,?,?,?,?)",
                        (
                            grant.entitlement_id,
                            grant.starts,
                            grant.ends,
                            grant.receipt_id,
                            payload,
                        ),
                    )
                if self.ledger._credit_access(db, grant):
                    credited += 1
            db.execute(
                "INSERT OR REPLACE INTO discord_purchase_state VALUES(?,?)",
                ("complete_snapshot_at", str(now)),
            )
            db.execute(
                "INSERT OR REPLACE INTO discord_purchase_state VALUES(?,?)",
                (
                    "snapshot_identity",
                    _identity(self.application_id, self.mapping, credit=True, mode="entitlement"),
                ),
            )
        return {
            "observed": len(access),
            "pending": pending,
            "credited": credited,
            "revoked": revoked,
        }

    async def reconcile(self, client: discord.Client, *, credit: bool) -> dict[str, int]:
        """Only a completed REST walk changes access or credits reviewed receipts."""
        self.invalidate()
        mode = funding_mode()
        if client.application_id != self.application_id:
            raise Denied("Discord application identity does not match configuration.")
        if credit and (
            os.getenv("PREPAID_MODE", "off") != "enforce"
            or os.getenv("DISCORD_PURCHASE_MODE", "off") != "enforce"
        ):
            raise Denied("Discord credits require reviewed commercial enforcement mode.")
        generation = self._generation
        access, pending = await self._fetch_access(client, mode=mode)
        if self._generation != generation:
            raise Denied("Discord purchase changed during reconciliation; retry required.")
        if credit and mode == "entitlement":
            return self._reconcile_access(access, pending)
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
            _assert_grants_bound(self.ledger, self.mapping, int(self.clock()), mode=mode)
        with self.ledger.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO discord_purchase_state VALUES(?,?)",
                ("complete_snapshot_at", str(int(self.clock()))),
            )
            db.execute(
                "INSERT OR REPLACE INTO discord_purchase_state VALUES(?,?)",
                (
                    "snapshot_identity",
                    _identity(self.application_id, self.mapping, credit=credit, mode=mode),
                ),
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
