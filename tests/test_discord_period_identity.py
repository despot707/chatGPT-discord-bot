"""Offline regressions: a corrected expiry never issues a second allowance."""

import json
from dataclasses import replace

import pytest
from src.discord_purchases import DiscordPurchases, PurchaseDenied
from src.prepaid import AccessGrant, Denied

from tests.test_discord_purchases import (
    APP,
    ENTITLEMENT,
    GUILD,
    NOW,
    SKU,
    SUBSCRIPTION,
    LiveGuildClient,
    dt,
    entitlement_mode,
    purchases,
    settlement,
)


def rows(p, table):
    with p.ledger.db() as db:
        return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def legacy_period(p, *, ends=NOW + 3600):
    """Seed the exact pre-fix persisted format, without relying on ID generation."""
    grant = AccessGrant(
        f"discord-access:{APP}:{ENTITLEMENT}:{SUBSCRIPTION}:{NOW - 100}:{ends}",
        GUILD,
        "basic",
        NOW - 100,
        ends,
        APP,
        ENTITLEMENT,
        SUBSCRIPTION,
        SKU,
    )
    p.ledger.credit_access(grant)
    with p.ledger.db() as db:
        db.execute(
            "INSERT INTO discord_access_period VALUES(?,?,?,?,?)",
            (
                ENTITLEMENT,
                grant.starts,
                grant.ends,
                grant.receipt_id,
                p.ledger.access_payload(grant),
            ),
        )
    return grant


@pytest.mark.parametrize("delta", [0, 1, -1])
async def test_replay_and_end_corrections_preserve_issued_allowance(tmp_path, monkeypatch, delta):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    client = LiveGuildClient()
    await p.reconcile(client, credit=True)
    request = p.ledger.reserve(GUILD, 777, "chat")
    before = rows(p, "prepaid_grants")[0]
    before_requests = rows(p, "prepaid_requests")
    client.sub.current_period_end = dt(NOW + 3600 + delta)

    result = await p.reconcile(client, credit=True)

    assert result == {"observed": 1, "pending": 0, "credited": 0, "revoked": 0}
    p.assert_current()
    assert p.ledger.summary(GUILD)["remaining"]["chat"] == 399
    after = rows(p, "prepaid_grants")[0]
    assert len(rows(p, "prepaid_grants")) == len(rows(p, "discord_access_period")) == 1
    assert after == {
        **before,
        "ends": NOW + 3600 + delta,
        "payment": p.ledger.access_payload(
            replace(AccessGrant(**json.loads(before["payment"])["access"]), ends=NOW + 3600 + delta)
        ),
    }
    assert rows(p, "prepaid_requests") == before_requests
    assert rows(p, "discord_access_period")[0]["ends"] == after["ends"]
    assert rows(p, "discord_access_period")[0]["payload"] == after["payment"]
    p.ledger.dispatch(request)  # Its existing receipt key still resolves.


async def test_new_period_credits_once_after_end_correction(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    client = LiveGuildClient()
    await p.reconcile(client, credit=True)
    p.ledger.reserve(GUILD, 777, "chat")
    client.sub.current_period_end = dt(NOW + 3601)
    assert (await p.reconcile(client, credit=True))["credited"] == 0
    p.clock = p.ledger.clock = lambda: NOW + 3601
    client.sub.current_period_start = dt(NOW + 3601)
    client.sub.current_period_end = dt(NOW + 7201)
    assert (await p.reconcile(client, credit=True))["credited"] == 1
    assert p.ledger.summary(GUILD)["remaining"]["chat"] == 400
    p.ledger.reserve(GUILD, 777, "chat")
    assert (await p.reconcile(client, credit=True))["credited"] == 0
    assert p.ledger.summary(GUILD)["remaining"]["chat"] == 399
    assert len(rows(p, "prepaid_grants")) == 2


async def test_legacy_receipt_preserves_usage_limits_storage_and_cash_evidence(
    tmp_path, monkeypatch
):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    grant = legacy_period(p)
    used = p.ledger.reserve(GUILD, 777, "chat")
    p.ledger.dispatch(used)
    p.ledger.settle(used, 123)
    cancelled = p.ledger.reserve(GUILD, 777, "chat")
    p.ledger.cancel(cancelled)
    p.ledger.reserve(GUILD, 777, "core")
    p.ledger.storage(GUILD, "profiles", 100)
    p.import_reviewed_settlement(settlement())
    preserved = {
        table: rows(p, table)
        for table in ("prepaid_requests", "prepaid_storage", "discord_purchase_settlement")
    }
    old = rows(p, "prepaid_grants")[0]
    # Reopen the adapter, as a real rollout would, before accepting the correction.
    p = DiscordPurchases(p.ledger, {SKU: "basic"}, application_id=APP, clock=lambda: NOW)
    client = LiveGuildClient()
    client.sub.current_period_end = dt(NOW + 3601)

    assert (await p.reconcile(client, credit=True))["credited"] == 0
    assert (await p.reconcile(client, credit=True))["credited"] == 0
    p.assert_current()
    assert p.ledger.summary(GUILD)["remaining"]["chat"] == 399
    assert len(rows(p, "prepaid_grants")) == 1
    after = rows(p, "prepaid_grants")[0]
    assert after["receipt"] == grant.receipt_id
    assert after["limits"] == old["limits"] and after["storage"] == old["storage"]
    assert after["ends"] == NOW + 3601
    for table, before in preserved.items():
        assert rows(p, table) == before
    assert p.ledger.infrastructure_funding() == 0


@pytest.mark.parametrize("reviewed_refund", [False, True])
async def test_changed_end_cannot_restore_revoked_legacy_grant(
    tmp_path, monkeypatch, reviewed_refund
):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    grant = legacy_period(p)
    p.ledger.reserve(GUILD, 777, "chat")
    if reviewed_refund:
        p.invalidate_settlement(grant.receipt_id, "refund", "b" * 64, "refund-1", "reviewer")
    else:
        p.ledger.revoke(grant.receipt_id)
    evidence = rows(p, "discord_purchase_invalid")
    requests = rows(p, "prepaid_requests")
    client = LiveGuildClient()
    client.sub.current_period_end = dt(NOW + 3601)
    if reviewed_refund:
        assert (await p.reconcile(client, credit=True))["credited"] == 0
    else:
        with pytest.raises(Denied, match="Revoked"):
            await p.reconcile(client, credit=True)
    assert rows(p, "discord_purchase_invalid") == evidence
    assert rows(p, "prepaid_requests") == requests
    assert len(rows(p, "prepaid_grants")) == 1
    assert rows(p, "prepaid_grants")[0]["revoked"] == 1
    with pytest.raises(Denied):
        p.ledger.reserve(GUILD, 777, "chat")


@pytest.mark.parametrize(
    "field", ["guild", "subscription", "entitlement", "product", "application"]
)
async def test_cycle_identity_mismatch_is_denied_without_changes(tmp_path, monkeypatch, field):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    legacy_period(p)
    p.ledger.reserve(GUILD, 777, "chat")
    preserved = {
        table: rows(p, table)
        for table in (
            "prepaid_grants",
            "prepaid_requests",
            "discord_access_period",
            "discord_purchase_access",
        )
    }
    client = LiveGuildClient()
    client.sub.current_period_end = dt(NOW + 3601)
    if field == "guild":
        client.ent.guild_id += 1
    elif field == "subscription":
        client.sub.id += 1
        client.raw_sub_id += 1
    elif field == "entitlement":
        # Use the user-filtered API, whose fixture permits a changed entitlement ID.
        client.ent.user_id = 777
        client.ent.id += 1
        client.sub.entitlement_ids = [client.ent.id]
    elif field == "product":
        p.mapping[SKU] = "plus"
    elif field == "sku":
        p.mapping = {SKU + 1: "basic"}
        client.sku.id += 1
        client.ent.sku_id += 1
        client.sub.sku_ids = [SKU + 1]
    else:
        p.application_id += 1
        client.application_id += 1
        client.sku.application_id += 1
        client.ent.application_id += 1
        client.ent.user_id = 777
    with pytest.raises(Denied, match="identity|terms"):
        await p.reconcile(client, credit=True)
    for table, before in preserved.items():
        assert rows(p, table) == before
    with pytest.raises(PurchaseDenied):
        p.assert_current()


async def test_historical_duplicate_cycles_require_audited_migration(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    first = legacy_period(p)
    p.ledger.reserve(GUILD, 777, "chat")
    p.ledger.revoke(first.receipt_id)
    legacy_period(p, ends=NOW + 3601)
    p.ledger.reserve(GUILD, 777, "chat")
    preserved = {
        table: rows(p, table)
        for table in (
            "prepaid_grants",
            "prepaid_requests",
            "discord_access_period",
            "discord_purchase_access",
        )
    }
    client = LiveGuildClient()
    client.sub.current_period_end = dt(NOW + 3602)
    with pytest.raises(Denied, match="audited.*migration"):
        await p.reconcile(client, credit=True)
    for table, before in preserved.items():
        assert rows(p, table) == before
    with pytest.raises(PurchaseDenied):
        p.assert_current()


@pytest.mark.parametrize(
    "receipt",
    [
        str(NOW + 3600),
        None,
        "1" * 5000,
        f"discord-access:{APP + 1}:{ENTITLEMENT}:{SUBSCRIPTION}:{NOW - 100}:{NOW + 3600}",
    ],
    ids=["bare-timestamp", "non-string", "oversized", "wrong-application"],
)
def test_receipt_validation_rejects_unbound_or_malformed_keys(tmp_path, receipt):
    p = purchases(tmp_path)
    grant = AccessGrant(
        receipt, GUILD, "basic", NOW - 100, NOW + 3600, APP, ENTITLEMENT, SUBSCRIPTION, SKU
    )
    with pytest.raises(Denied):
        p.ledger.access_payload(grant)


async def test_same_snapshot_cannot_issue_cycle_twice_with_different_guild(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tests.test_discord_purchases import FakeClient

    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)

    class DuplicateClient(FakeClient):
        def __init__(self):
            super().__init__(user=777)
            self.sub.entitlement_ids = [ENTITLEMENT, ENTITLEMENT + 1]

        async def entitlements(self, **_kwargs):
            yield self.ent
            duplicate = SimpleNamespace(**vars(self.ent))
            duplicate.id += 1
            duplicate.guild_id += 1
            yield duplicate

    with pytest.raises(Denied, match="conflicting entitlements"):
        await p.reconcile(DuplicateClient(), credit=True)
    assert rows(p, "prepaid_grants") == []


async def test_corrupt_existing_period_cannot_be_replaced_with_fresh_quota(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    grant = legacy_period(p)
    p.ledger.reserve(GUILD, 777, "chat")
    corrupt = replace(grant, entitlement_id=ENTITLEMENT + 1, subscription_id=SUBSCRIPTION + 1)
    with p.ledger.db() as db:
        db.execute(
            "UPDATE discord_access_period SET payload=?",
            (json.dumps({"source": "discord_entitlement", "access": corrupt.__dict__}),),
        )
    preserved = {
        table: rows(p, table)
        for table in ("prepaid_grants", "prepaid_requests", "discord_access_period")
    }
    client = LiveGuildClient()
    client.sub.current_period_end = dt(NOW + 3601)
    with pytest.raises(Denied, match="identity|terms"):
        await p.reconcile(client, credit=True)
    for table, before in preserved.items():
        assert rows(p, table) == before


async def test_expiry_update_and_snapshot_roll_back_together(tmp_path, monkeypatch):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    legacy_period(p)
    await p.reconcile(LiveGuildClient(), credit=True)
    p.ledger.reserve(GUILD, 777, "chat")
    preserved = {
        table: rows(p, table)
        for table in (
            "prepaid_grants",
            "prepaid_requests",
            "discord_access_period",
            "discord_purchase_access",
        )
    }
    client = LiveGuildClient()
    client.sub.current_period_end = dt(NOW + 3601)

    def fail_credit(*_args):
        raise Denied("Injected failure")

    monkeypatch.setattr(p.ledger, "_credit_access", fail_credit)
    with pytest.raises(Denied, match="Injected"):
        await p.reconcile(client, credit=True)
    for table, before in preserved.items():
        assert rows(p, table) == before
    with pytest.raises(PurchaseDenied):
        p.assert_current()


@pytest.mark.parametrize("damage", ["period-start", "missing-period", "missing-refunded-period"])
async def test_incomplete_historical_identity_requires_audit(tmp_path, monkeypatch, damage):
    entitlement_mode(monkeypatch)
    p = purchases(tmp_path)
    grant = legacy_period(p)
    p.ledger.reserve(GUILD, 777, "chat")
    if damage == "missing-refunded-period":
        p.invalidate_settlement(grant.receipt_id, "refund", "b" * 64, "refund-1", "reviewer")
    with p.ledger.db() as db:
        if damage == "period-start":
            db.execute("UPDATE discord_access_period SET starts=starts+1")
        else:
            db.execute("DELETE FROM discord_access_period")
    preserved = {
        table: rows(p, table)
        for table in ("prepaid_grants", "prepaid_requests", "discord_access_period")
    }
    with pytest.raises(Denied, match="identity|audit"):
        await p.reconcile(LiveGuildClient(), credit=True)
    for table, before in preserved.items():
        assert rows(p, table) == before
    with pytest.raises(PurchaseDenied):
        p.assert_current()
