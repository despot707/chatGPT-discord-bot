"""Exercise real discord.py REST parsing with offline Discord wire payloads."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import discord
import pytest
from src.discord_purchases import DiscordPurchases, Settlement
from src.prepaid import Denied, Ledger

NOW = 2_000_000_000
APP = 222
SKU = 111
GUILD = 333
ENTITLEMENT = 444
SUBSCRIPTION = 555


def dt(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    monkeypatch.setenv("DISCORD_PURCHASE_MODE", "enforce")
    client = discord.Client(intents=discord.Intents.none(), application_id=APP)
    entitlement = {
        "id": str(ENTITLEMENT),
        "application_id": str(APP),
        "sku_id": str(SKU),
        "guild_id": str(GUILD),
        "user_id": None,
        "type": 1,
        "deleted": False,
        "starts_at": dt(NOW - 100),
        "ends_at": None,
        "subscription_id": str(SUBSCRIPTION),
    }
    subscription = {
        "id": str(SUBSCRIPTION),
        "user_id": "777",
        "sku_ids": [str(SKU)],
        "entitlement_ids": [str(ENTITLEMENT)],
        "renewal_sku_ids": None,
        "current_period_start": dt(NOW - 100),
        "current_period_end": dt(NOW + 3600),
        "status": 0,
        "canceled_at": None,
    }
    sku = {
        "id": str(SKU),
        "application_id": str(APP),
        "type": 5,
        "flags": 128,
        "name": "Basic",
        "slug": "basic",
    }
    for method, result in (
        ("get_skus", [sku]),
        ("get_entitlements", [entitlement]),
        ("get_entitlement", entitlement),
        ("get_sku_subscription", subscription),
    ):
        monkeypatch.setattr(client.http, method, AsyncMock(return_value=result))
    return client, entitlement, subscription


def purchases(tmp_path):
    ledger = Ledger(str(tmp_path / "wire.sqlite3"), clock=lambda: NOW)
    return DiscordPurchases(ledger, {SKU: "basic"}, application_id=APP, clock=lambda: NOW)


def settlement():
    return Settlement(
        "paid-invoice-wire",
        ENTITLEMENT,
        SUBSCRIPTION,
        GUILD,
        SKU,
        "basic",
        NOW - 100,
        NOW + 3600,
        1_990_000,
        1_300_000,
        "USD",
        "a" * 64,
        "finance/invoice-wire",
        "reviewer",
    )


@pytest.mark.parametrize("ent_type", [1, 8])
@pytest.mark.parametrize("finite_end", [False, True])
@pytest.mark.parametrize("mode", ["settlement", "entitlement"])
async def test_wire_subscription_purchase_grants_one_period(
    tmp_path, wire, monkeypatch, ent_type, finite_end, mode
):
    client, entitlement, _ = wire
    monkeypatch.setenv("DISCORD_FUNDING_MODE", mode)
    entitlement["type"] = ent_type
    if finite_end:
        entitlement["ends_at"] = dt(NOW + 3600)
    p = purchases(tmp_path)
    if mode == "settlement":
        p.import_reviewed_settlement(settlement())
    assert (await p.reconcile(client, credit=True))["credited"] == 1
    p.assert_current()
    assert (await p.reconcile(client, credit=True))["credited"] == 0
    with p.ledger.db() as db:
        row = db.execute("SELECT payment,ends FROM prepaid_grants").fetchone()
    assert row["ends"] == NOW + 3600
    if mode == "entitlement":
        assert json.loads(row["payment"])["source"] == "discord_entitlement"
        assert p.ledger.infrastructure_funding() == 0
    else:
        assert json.loads(row["payment"])["source"] == "verified_payment"


@pytest.mark.parametrize("ent_type", [1, 8])
async def test_wire_null_start_uses_authenticated_subscription_period(
    tmp_path, wire, monkeypatch, ent_type
):
    client, entitlement, _ = wire
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "entitlement")
    entitlement.update(type=ent_type, starts_at=None)
    p = purchases(tmp_path)
    assert (await p.reconcile(client, credit=True))["credited"] == 1


@pytest.mark.parametrize("wire_status,credited", [(0, 1), (1, 0), (2, 1)])
async def test_wire_settlement_status_values(tmp_path, wire, wire_status, credited):
    client, _, subscription = wire
    subscription["status"] = wire_status
    p = purchases(tmp_path)
    p.import_reviewed_settlement(settlement())
    assert (await p.reconcile(client, credit=True))["credited"] == credited


@pytest.mark.parametrize("ent_type", [2, 3, 4, 5, 6, 7, 99])
async def test_wire_nonpurchase_types_do_not_grant(tmp_path, wire, monkeypatch, ent_type):
    client, entitlement, _ = wire
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "entitlement")
    entitlement["type"] = ent_type
    p = purchases(tmp_path)
    assert (await p.reconcile(client, credit=True))["credited"] == 0
    with p.ledger.db() as db:
        assert db.execute("SELECT COUNT(*) FROM prepaid_grants").fetchone()[0] == 0


@pytest.mark.parametrize(
    "sku_change",
    [{"type": 2}, {"flags": 256}, {"application_id": "999"}],
)
async def test_type_one_requires_authentic_mapped_guild_subscription(
    tmp_path, wire, monkeypatch, sku_change
):
    client, _, _ = wire
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "entitlement")
    client.http.get_skus.return_value[0].update(sku_change)
    p = purchases(tmp_path)
    with pytest.raises(Denied, match="guild subscription"):
        await p.reconcile(client, credit=True)
    with p.ledger.db() as db:
        assert db.execute("SELECT COUNT(*) FROM prepaid_grants").fetchone()[0] == 0


async def test_type_one_raw_detail_must_agree_with_list(tmp_path, wire, monkeypatch):
    client, _, _ = wire
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "entitlement")
    client.http.get_entitlement.return_value = {
        **client.http.get_entitlement.return_value,
        "type": 8,
    }
    with pytest.raises(Denied, match="conflicts"):
        await purchases(tmp_path).reconcile(client, credit=True)
