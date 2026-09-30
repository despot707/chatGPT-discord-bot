from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest
from src.discord_purchases import (
    DiscordPurchases,
    PurchaseDenied,
    Settlement,
    assert_purchase_current,
)
from src.prepaid import Denied, Ledger, Payment

NOW = 2_000_000_000
SKU = 111
APP = 222
GUILD = 333
ENTITLEMENT = 444
SUBSCRIPTION = 555
RECEIPT = "paid-invoice-001"


def dt(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc)


def settlement(**changes):
    values = dict(
        receipt_id=RECEIPT,
        entitlement_id=ENTITLEMENT,
        subscription_id=SUBSCRIPTION,
        guild_id=GUILD,
        sku_id=SKU,
        product="basic",
        starts=NOW - 100,
        ends=NOW + 3600,
        gross_micros=990000,
        net_micros=700000,
        currency="USD",
        evidence_sha256="a" * 64,
        evidence_reference="Discord export row 7",
        reviewed_by="operator",
    )
    values.update(changes)
    return Settlement(**values)


class FakeSKU:
    id = SKU
    application_id = APP
    type = discord.SKUType.subscription
    flags = SimpleNamespace(guild_subscription=True)

    def __init__(self, sub, hook=None):
        self.sub = sub
        self.hook = hook

    async def fetch_subscription(self, sub_id):
        assert sub_id == self.sub.id
        if self.hook:
            self.hook()
        return self.sub

    async def subscriptions(self, *, limit, user):
        assert limit is None and user.id == self.sub.user_id
        yield self.sub


class FakeClient:
    application_id = APP

    def __init__(
        self,
        *,
        guild=GUILD,
        user=None,
        deleted=False,
        status=None,
        fail=False,
        hook=None,
        renewal=False,
    ):
        starts = NOW + 3600 if renewal else NOW - 100
        ends = NOW + 7200 if renewal else NOW + 3600
        self.ent = SimpleNamespace(
            id=ENTITLEMENT,
            sku_id=SKU,
            application_id=APP,
            guild_id=guild,
            user_id=user,
            type=discord.EntitlementType.application_subscription,
            deleted=deleted,
            starts_at=dt(NOW - 100),
            ends_at=dt(NOW + 7200),
        )
        self.sub = SimpleNamespace(
            id=SUBSCRIPTION,
            user_id=777,
            entitlement_ids=[ENTITLEMENT],
            sku_ids=[SKU],
            status=status or discord.SubscriptionStatus.active,
            current_period_start=dt(starts),
            current_period_end=dt(ends),
        )
        self.sku = FakeSKU(self.sub, hook)
        self.fail = fail

    async def fetch_skus(self):
        return [self.sku]

    async def entitlements(self, *, limit, exclude_ended, exclude_deleted):
        assert limit is None and not exclude_ended and not exclude_deleted
        if self.fail:
            raise OSError("Discord unavailable")
        yield self.ent


def purchases(tmp_path):
    ledger = Ledger(str(tmp_path / "paid.sqlite3"), clock=lambda: NOW)
    return DiscordPurchases(ledger, {SKU: "basic"}, application_id=APP, clock=lambda: NOW)


def enforce(monkeypatch):
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    monkeypatch.setenv("DISCORD_PURCHASE_MODE", "enforce")


@pytest.mark.asyncio
async def test_reviewed_invoice_and_authenticated_null_user_guild_purchase(tmp_path, monkeypatch):
    enforce(monkeypatch)
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    assert await p.reconcile(FakeClient(user=None), credit=True) == {
        "observed": 1,
        "pending": 0,
        "credited": 1,
        "revoked": 0,
    }
    p.assert_current()
    p.ledger.assert_active(GUILD)
    assert (await p.reconcile(FakeClient(user=None), credit=True))["credited"] == 0
    with pytest.raises(Denied, match="Receipt|receipt"):
        p.import_reviewed_settlement(settlement(guild_id=999))


@pytest.mark.asyncio
async def test_cross_guild_mismatch_never_credits(tmp_path, monkeypatch):
    enforce(monkeypatch)
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    result = await p.reconcile(FakeClient(guild=999), credit=True)
    assert result["credited"] == 0
    with pytest.raises(Denied):
        p.ledger.assert_active(GUILD)


@pytest.mark.asyncio
async def test_renewal_requires_distinct_reviewed_invoice_and_revokes_prior(tmp_path, monkeypatch):
    enforce(monkeypatch)
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    await p.reconcile(FakeClient(), credit=True)
    later = FakeClient(renewal=True)
    result = await p.reconcile(later, credit=True)
    assert result["credited"] == 0 and result["revoked"] == 1
    p.import_reviewed_settlement(
        settlement(receipt_id="paid-invoice-002", starts=NOW + 3600, ends=NOW + 7200)
    )
    # New billing period is not current yet; it cannot be credited early.
    assert (await p.reconcile(later, credit=True))["credited"] == 0


@pytest.mark.asyncio
async def test_refund_revoke_and_api_outage_fail_closed(tmp_path, monkeypatch):
    enforce(monkeypatch)
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    await p.reconcile(FakeClient(), credit=True)
    with pytest.raises(OSError):
        await p.reconcile(FakeClient(fail=True), credit=True)
    with pytest.raises(PurchaseDenied) as denied:
        p.assert_current()
    assert "stale" in denied.value.operator_reason
    result = await p.reconcile(FakeClient(deleted=True), credit=True)
    assert result["revoked"] == 1
    with pytest.raises(Denied):
        p.ledger.assert_active(GUILD)


@pytest.mark.asyncio
async def test_event_during_fetch_cannot_mark_old_snapshot_current(tmp_path, monkeypatch):
    enforce(monkeypatch)
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    with pytest.raises(Denied, match="changed during"):
        await p.reconcile(FakeClient(hook=p.invalidate), credit=True)
    with pytest.raises(PurchaseDenied):
        p.assert_current()
    assert (await p.reconcile(FakeClient(), credit=True))["credited"] == 1
    p.invalidate()
    with pytest.raises(PurchaseDenied):
        p.assert_current()


@pytest.mark.asyncio
async def test_observe_cannot_credit_or_satisfy_enforce_freshness(tmp_path, monkeypatch):
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    assert (await p.reconcile(FakeClient(), credit=False))["credited"] == 0
    with pytest.raises(PurchaseDenied):
        assert_purchase_current(p.ledger, APP, {SKU: "basic"}, clock=lambda: NOW)
    with pytest.raises(Denied, match="enforcement"):
        await p.reconcile(FakeClient(), credit=True)


@pytest.mark.asyncio
async def test_active_legacy_grant_blocks_snapshot_and_spend(tmp_path, monkeypatch):
    enforce(monkeypatch)
    p = purchases(tmp_path)
    p.ledger.credit(
        Payment(
            "manual-legacy",
            GUILD,
            "basic",
            990000,
            700000,
            NOW - 100,
            NOW + 3600,
            "verified_payment",
            "USD",
        )
    )
    with pytest.raises(Denied, match="migration"):
        await p.reconcile(FakeClient(deleted=True), credit=True)
    with pytest.raises(PurchaseDenied):
        p.assert_current()


@pytest.mark.asyncio
async def test_entitlement_cap_prevents_partial_snapshot(tmp_path, monkeypatch):
    enforce(monkeypatch)
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    monkeypatch.setattr("src.discord_purchases.MAX_ENTITLEMENTS", 0)
    with pytest.raises(Denied, match="incomplete"):
        await p.reconcile(FakeClient(), credit=True)
    with pytest.raises(PurchaseDenied):
        p.assert_current()


@pytest.mark.asyncio
async def test_reviewed_chargeback_tombstone_stops_active_entitlement(tmp_path, monkeypatch):
    enforce(monkeypatch)
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    await p.reconcile(FakeClient(), credit=True)
    assert p.invalidate_settlement(RECEIPT, "chargeback", "b" * 64, "payout dispute 9", "operator")
    with pytest.raises(PurchaseDenied):
        p.assert_current()
    with pytest.raises(Denied):
        p.ledger.assert_active(GUILD)
    assert not p.invalidate_settlement(
        RECEIPT, "chargeback", "b" * 64, "payout dispute 9", "operator"
    )
    assert (await p.reconcile(FakeClient(), credit=True))["credited"] == 0
    with pytest.raises(Denied, match="invalidated"):
        p.import_reviewed_settlement(settlement())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "credited"),
    [(discord.SubscriptionStatus.inactive, 0), (99, 0), (discord.SubscriptionStatus.ending, 1)],
)
async def test_only_current_active_or_ending_subscriptions_can_fund_grants(
    tmp_path, monkeypatch, status, credited
):
    enforce(monkeypatch)
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    assert (await p.reconcile(FakeClient(status=status), credit=True))["credited"] == credited
