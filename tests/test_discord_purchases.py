from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest
from src.discord_purchases import (
    DiscordPurchases,
    PurchaseDenied,
    Settlement,
    assert_purchase_current,
    funding_mode,
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
        gross_micros=1990000,
        net_micros=1300000,
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


class LiveGuildClient(FakeClient):
    """The raw API exposes subscription_id, which discord.py 2.7 omits."""

    def __init__(self, *, raw_sub_id=SUBSCRIPTION, **kwargs):
        super().__init__(user=None, **kwargs)
        self.ent.ends_at = None
        self.raw_sub_id = raw_sub_id
        self.http = SimpleNamespace(get_entitlement=self._raw_entitlement)

    async def _raw_entitlement(self, app_id, ent_id):
        assert app_id == APP and ent_id == ENTITLEMENT
        return {
            "id": str(self.ent.id),
            "application_id": str(self.ent.application_id),
            "sku_id": str(self.ent.sku_id),
            "guild_id": str(self.ent.guild_id),
            "type": self.ent.type.value,
            "deleted": self.ent.deleted,
            "subscription_id": self.raw_sub_id,
        }


def purchases(tmp_path):
    ledger = Ledger(str(tmp_path / "paid.sqlite3"), clock=lambda: NOW)
    return DiscordPurchases(ledger, {SKU: "basic"}, application_id=APP, clock=lambda: NOW)


def enforce(monkeypatch):
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    monkeypatch.setenv("DISCORD_PURCHASE_MODE", "enforce")


def entitlement_mode(monkeypatch):
    enforce(monkeypatch)
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "entitlement")


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
            1990000,
            1300000,
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


@pytest.mark.asyncio
async def test_native_active_guild_purchase_grants_without_invoice_or_revenue(
    tmp_path, monkeypatch
):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    result = await p.reconcile(LiveGuildClient(), credit=True)
    assert result == {"observed": 1, "pending": 0, "credited": 1, "revoked": 0}
    p.assert_current()
    p.ledger.assert_active(GUILD)
    with p.ledger.db() as db:
        rows = db.execute("SELECT receipt,payment FROM prepaid_grants").fetchall()
    assert len(rows) == 1
    assert rows[0]["receipt"].startswith("discord-access:")
    assert '"source":"discord_entitlement"' in rows[0]["payment"]
    assert "gross_micros" not in rows[0]["payment"]
    assert "net_micros" not in rows[0]["payment"]
    assert p.ledger.infrastructure_funding() == 0
    for _ in range(400):
        p.ledger.reserve(GUILD, 777, "chat")
    with pytest.raises(Denied):
        p.ledger.reserve(GUILD, 777, "chat")
    assert (await p.reconcile(LiveGuildClient(), credit=True))["credited"] == 0


@pytest.mark.asyncio
async def test_native_renewal_credits_once_and_revokes_prior_period(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    assert (await p.reconcile(LiveGuildClient(), credit=True))["credited"] == 1
    renewal = LiveGuildClient()
    renewal.sub.current_period_start = dt(NOW - 10)
    renewal.sub.current_period_end = dt(NOW + 30 * 86400)
    result = await p.reconcile(renewal, credit=True)
    assert result["credited"] == 1 and result["revoked"] == 1
    assert (await p.reconcile(renewal, credit=True))["credited"] == 0
    with p.ledger.db() as db:
        rows = db.execute("SELECT revoked FROM prepaid_grants ORDER BY starts").fetchall()
    assert [row["revoked"] for row in rows] == [1, 0]


@pytest.mark.asyncio
async def test_native_guild_without_raw_subscription_id_is_pending(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    result = await p.reconcile(LiveGuildClient(raw_sub_id=None), credit=True)
    assert result == {"observed": 0, "pending": 1, "credited": 0, "revoked": 0}
    with pytest.raises(Denied):
        p.ledger.assert_active(GUILD)


@pytest.mark.asyncio
async def test_native_app_subscription_with_null_access_start_uses_period(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    client = LiveGuildClient()
    client.ent.starts_at = None
    assert (await p.reconcile(client, credit=True))["credited"] == 1
    p.assert_current()


@pytest.mark.asyncio
async def test_two_entitlements_for_one_subscription_period_cannot_double_allowance(
    tmp_path, monkeypatch
):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)

    class DuplicateClient(FakeClient):
        def __init__(self):
            super().__init__(user=777)
            self.sub.entitlement_ids = [ENTITLEMENT, ENTITLEMENT + 1]

        async def entitlements(self, *, limit, exclude_ended, exclude_deleted):
            assert limit is None and not exclude_ended and not exclude_deleted
            yield self.ent
            duplicate = SimpleNamespace(**vars(self.ent))
            duplicate.id = ENTITLEMENT + 1
            yield duplicate

    with pytest.raises(Denied, match="conflicting entitlements"):
        await p.reconcile(DuplicateClient(), credit=True)
    with p.ledger.db() as db:
        assert db.execute("SELECT COUNT(*) FROM prepaid_grants").fetchone()[0] == 0
    with pytest.raises(PurchaseDenied):
        p.assert_current()


@pytest.mark.asyncio
async def test_native_conflicting_raw_entitlement_fails_closed(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    client = LiveGuildClient()

    async def wrong_guild(*_args):
        detail = await client._raw_entitlement(APP, ENTITLEMENT)
        detail["guild_id"] = "999"
        return detail

    client.http.get_entitlement = wrong_guild
    with pytest.raises(Denied, match="conflicts"):
        await p.reconcile(client, credit=True)
    with pytest.raises(PurchaseDenied):
        p.assert_current()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        discord.SubscriptionStatus.active,
        discord.SubscriptionStatus.ending,
        discord.SubscriptionStatus.inactive,
    ],
)
async def test_native_access_comes_from_entitlement_not_status(tmp_path, monkeypatch, status):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    assert (await p.reconcile(LiveGuildClient(status=status), credit=True))["credited"] == 1
    p.ledger.assert_active(GUILD)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        discord.EntitlementType.test_mode_purchase,
        discord.EntitlementType.developer_gift,
        discord.EntitlementType.free_purchase,
    ],
)
async def test_native_test_gift_free_entitlements_do_not_grant(tmp_path, monkeypatch, kind):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    client = LiveGuildClient()
    client.ent.type = kind
    client.ent.starts_at = None
    result = await p.reconcile(client, credit=True)
    assert result["credited"] == 0
    with pytest.raises(Denied):
        p.ledger.assert_active(GUILD)


@pytest.mark.asyncio
async def test_native_delete_revokes_and_replay_cannot_restore(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    await p.reconcile(LiveGuildClient(), credit=True)
    result = await p.reconcile(LiveGuildClient(deleted=True), credit=True)
    assert result["revoked"] == 1
    with pytest.raises(Denied):
        p.ledger.assert_active(GUILD)
    with pytest.raises(Denied, match="Revoked"):
        await p.reconcile(LiveGuildClient(), credit=True)


@pytest.mark.asyncio
async def test_native_reviewed_refund_tombstone_blocks_still_active_access(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    await p.reconcile(LiveGuildClient(), credit=True)
    with p.ledger.db() as db:
        receipt = db.execute("SELECT receipt FROM prepaid_grants").fetchone()[0]
    assert p.invalidate_settlement(receipt, "refund", "b" * 64, "refund-1", "reviewer")
    result = await p.reconcile(LiveGuildClient(), credit=True)
    assert result["credited"] == 0
    with pytest.raises(Denied):
        p.ledger.assert_active(GUILD)


@pytest.mark.asyncio
async def test_switching_funding_mode_invalidates_snapshot(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    await p.reconcile(LiveGuildClient(), credit=True)
    p.assert_current()
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "settlement")
    with pytest.raises(PurchaseDenied):
        p.assert_current()


@pytest.mark.asyncio
async def test_native_failed_reconcile_rolls_back_revocation_and_new_grant(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    await p.reconcile(LiveGuildClient(), credit=True)
    renewal = LiveGuildClient()
    renewal.sub.current_period_start = dt(NOW - 10)
    renewal.sub.current_period_end = dt(NOW + 30 * 86400)

    def fail_credit(*_args):
        raise Denied("Injected failure")

    monkeypatch.setattr(p.ledger, "_credit_access", fail_credit)
    with pytest.raises(Denied, match="Injected"):
        await p.reconcile(renewal, credit=True)
    with p.ledger.db() as db:
        rows = db.execute("SELECT revoked FROM prepaid_grants").fetchall()
        periods = db.execute("SELECT COUNT(*) FROM discord_access_period").fetchone()[0]
    assert [row["revoked"] for row in rows] == [0]
    assert periods == 1
    with pytest.raises(PurchaseDenied):
        p.assert_current()


def test_funding_mode_is_explicit_and_validated(monkeypatch):
    monkeypatch.delenv("DISCORD_FUNDING_MODE", raising=False)
    assert funding_mode() == "settlement"
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "entitlement")
    assert funding_mode() == "entitlement"
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "guess")
    with pytest.raises(Denied, match="funding mode"):
        funding_mode()
