"""Operator reversal command: reviewed evidence is required before access stops."""

import hashlib
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest
from src.discord_purchase_revoke import main
from src.discord_purchases import DiscordPurchases, Settlement
from src.prepaid import Denied, Ledger, Payment

APP = 222
SKU = 111
GUILD = 333
ENTITLEMENT = 444
SUBSCRIPTION = 555
RECEIPT = "paid-invoice-001"


def _stage(tmp_path):
    now = int(time.time())
    database = tmp_path / "paid.sqlite3"
    ledger = Ledger(str(database), clock=lambda: now)
    purchases = DiscordPurchases(ledger, {SKU: "basic"}, application_id=APP, clock=lambda: now)
    facts = Settlement(
        RECEIPT,
        ENTITLEMENT,
        SUBSCRIPTION,
        GUILD,
        SKU,
        "basic",
        now - 60,
        now + 3600,
        1990000,
        1300000,
        "USD",
        "a" * 64,
        "finance/invoice-1",
        "reviewer",
    )
    assert purchases.import_reviewed_settlement(facts)
    return ledger, purchases, facts, database


def _args(tmp_path, *, receipt_id=RECEIPT, reason="refund", confirm=True):
    evidence = tmp_path / "reversal.bin"
    if not evidence.exists():
        evidence.write_bytes(b"reviewed financial reversal")
    args = [
        "--receipt-id",
        receipt_id,
        "--reason",
        reason,
        "--evidence-file",
        str(evidence),
        "--evidence-reference",
        "finance/reversal-1",
        "--reviewed-by",
        "operator",
    ]
    if confirm:
        args.append("--confirm-revocation")
    return args, evidence


def _env(monkeypatch, database):
    monkeypatch.setenv("PREPAID_DATABASE_PATH", str(database))
    monkeypatch.setenv("DISCORD_APPLICATION_ID", str(APP))
    monkeypatch.setenv("DISCORD_SKU_MAP", f"{SKU}:basic")


class _Client:
    application_id = APP

    def __init__(self, facts):
        self.facts = facts
        self.sub = SimpleNamespace(
            id=SUBSCRIPTION,
            user_id=777,
            entitlement_ids=[ENTITLEMENT],
            sku_ids=[SKU],
            status=discord.SubscriptionStatus.active,
            current_period_start=datetime.fromtimestamp(facts.starts, timezone.utc),
            current_period_end=datetime.fromtimestamp(facts.ends, timezone.utc),
        )

    async def fetch_skus(self):
        sub = self.sub

        class SKUObject:
            id = SKU
            application_id = APP
            type = discord.SKUType.subscription
            flags = SimpleNamespace(guild_subscription=True)

            async def fetch_subscription(self, sub_id):
                assert sub_id == SUBSCRIPTION
                return sub

        return [SKUObject()]

    async def entitlements(self, *, limit, exclude_ended, exclude_deleted):
        assert limit is None and not exclude_ended and not exclude_deleted
        facts = self.facts
        yield SimpleNamespace(
            id=ENTITLEMENT,
            application_id=APP,
            sku_id=SKU,
            guild_id=GUILD,
            user_id=None,
            type=discord.EntitlementType.application_subscription,
            deleted=False,
            starts_at=datetime.fromtimestamp(facts.starts, timezone.utc),
            ends_at=datetime.fromtimestamp(facts.ends, timezone.utc),
        )


def test_revoke_active_grant_keeps_incurred_usage(tmp_path, monkeypatch, capsys):
    ledger, _, facts, database = _stage(tmp_path)
    _env(monkeypatch, database)
    ledger.credit(
        Payment(
            RECEIPT,
            GUILD,
            "basic",
            facts.gross_micros,
            facts.net_micros,
            facts.starts,
            facts.ends,
            "verified_payment",
            "USD",
        )
    )
    request = ledger.reserve(GUILD, 777, "core")
    ledger.dispatch(request)
    ledger.settle(request, 50)

    args, evidence = _args(tmp_path)
    assert main(args) == 0
    assert "access is denied" in capsys.readouterr().out
    with sqlite3.connect(database) as db:
        assert (
            db.execute("SELECT revoked FROM prepaid_grants WHERE receipt=?", (RECEIPT,)).fetchone()[
                0
            ]
            == 1
        )
        assert db.execute(
            "SELECT state,actual FROM prepaid_requests WHERE id=?", (request,)
        ).fetchone() == ("settled", 50)
        row = db.execute(
            "SELECT reason,evidence_sha256,evidence_reference,reviewed_by "
            "FROM discord_purchase_invalid WHERE receipt_id=?",
            (RECEIPT,),
        ).fetchone()
    assert row == (
        "refund",
        hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "finance/reversal-1",
        "operator",
    )
    with pytest.raises(Denied):
        ledger.assert_active(GUILD)


@pytest.mark.asyncio
async def test_tombstone_before_first_grant_blocks_later_reconcile(tmp_path, monkeypatch):
    ledger, purchases, facts, database = _stage(tmp_path)
    _env(monkeypatch, database)
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    monkeypatch.setenv("DISCORD_PURCHASE_MODE", "enforce")
    args, _ = _args(tmp_path)
    assert main(args) == 0
    result = await purchases.reconcile(_Client(facts), credit=True)
    assert result["credited"] == 0
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM prepaid_grants").fetchone()[0] == 0
    with pytest.raises(Denied):
        ledger.assert_active(GUILD)


def test_exact_replay_is_noop_and_changed_evidence_conflicts(tmp_path, monkeypatch, capsys):
    _, _, _, database = _stage(tmp_path)
    _env(monkeypatch, database)
    args, evidence = _args(tmp_path, reason="chargeback")
    assert main(args) == 0
    with sqlite3.connect(database) as db:
        initial = db.execute(
            "SELECT * FROM discord_purchase_invalid WHERE receipt_id=?", (RECEIPT,)
        ).fetchone()
    assert main(args) == 0
    assert "already recorded unchanged" in capsys.readouterr().out
    evidence.write_bytes(b"different reversal evidence")
    with pytest.raises(SystemExit) as conflict:
        main(args)
    assert conflict.value.code == 2
    with sqlite3.connect(database) as db:
        assert (
            db.execute(
                "SELECT * FROM discord_purchase_invalid WHERE receipt_id=?", (RECEIPT,)
            ).fetchone()
            == initial
        )
        assert db.execute("SELECT COUNT(*) FROM discord_purchase_invalid").fetchone()[0] == 1


def test_missing_confirmation_or_unstaged_receipt_never_creates_revocation(tmp_path, monkeypatch):
    _, _, _, database = _stage(tmp_path)
    _env(monkeypatch, database)
    args, _ = _args(tmp_path, confirm=False)
    with pytest.raises(SystemExit) as unconfirmed:
        main(args)
    assert unconfirmed.value.code == 2
    unknown_args, _ = _args(tmp_path, receipt_id="not-staged")
    with pytest.raises(SystemExit) as unknown:
        main(unknown_args)
    assert unknown.value.code == 2
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM discord_purchase_invalid").fetchone()[0] == 0
