"""Offline subscription lifecycle checks across grants, usage, and restarts."""

from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest
from src.discord_purchases import DiscordPurchases
from src.prepaid import PRODUCTS, Denied, Ledger

NOW = 2_000_000_000
APP = 222
GUILD = 333
USER = 777
SUBSCRIPTION = 555
SKUS = {111: "basic", 112: "plus", 113: "premium"}


def dt(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc)


class SKU:
    application_id = APP
    type = discord.SKUType.subscription
    flags = SimpleNamespace(guild_subscription=True)

    def __init__(self, sku_id, client):
        self.id = sku_id
        self.client = client

    async def fetch_subscription(self, subscription_id):
        assert subscription_id == self.client.subscription.id
        return self.client.subscription


class Client:
    application_id = APP

    def __init__(self, product="basic", entitlement_id=444):
        self.period_start = NOW - 100
        self.period_end = NOW + 3600
        self.entitlement_id = entitlement_id
        self.product = product
        self.entitlements_list = [self._entitlement(entitlement_id, product)]
        self.subscription = self._subscription(product, entitlement_id)
        self.http = SimpleNamespace(get_entitlement=self._raw_entitlement)

    def _entitlement(self, entitlement_id, product, *, ends_at=None):
        sku_id = next(sku for sku, name in SKUS.items() if name == product)
        return SimpleNamespace(
            id=entitlement_id,
            application_id=APP,
            sku_id=sku_id,
            guild_id=GUILD,
            user_id=None,
            type=discord.EntitlementType.purchase,
            deleted=False,
            starts_at=dt(self.period_start),
            ends_at=dt(ends_at) if ends_at is not None else None,
        )

    def _subscription(self, product, entitlement_id):
        sku_id = next(sku for sku, name in SKUS.items() if name == product)
        return SimpleNamespace(
            id=SUBSCRIPTION,
            user_id=USER,
            entitlement_ids=[entitlement_id],
            sku_ids=[sku_id],
            renewal_sku_ids=None,
            status=discord.SubscriptionStatus.active,
            current_period_start=dt(self.period_start),
            current_period_end=dt(self.period_end),
        )

    async def fetch_skus(self):
        return [SKU(sku_id, self) for sku_id in SKUS]

    async def entitlements(self, *, limit, exclude_ended, exclude_deleted):
        assert limit is None and not exclude_ended and not exclude_deleted
        for entitlement in self.entitlements_list:
            yield entitlement

    async def _raw_entitlement(self, app_id, entitlement_id):
        assert app_id == APP
        entitlement = next(row for row in self.entitlements_list if row.id == entitlement_id)
        return {
            "id": str(entitlement.id),
            "application_id": str(APP),
            "sku_id": str(entitlement.sku_id),
            "guild_id": str(entitlement.guild_id),
            "type": entitlement.type.value,
            "deleted": entitlement.deleted,
            "subscription_id": str(SUBSCRIPTION),
        }

    def transition(self, product, entitlement_id, at, end):
        previous = self.entitlements_list[-1]
        previous.ends_at = dt(at)
        self.period_start = at
        self.period_end = end
        self.product = product
        self.entitlement_id = entitlement_id
        self.entitlements_list.append(self._entitlement(entitlement_id, product))
        self.subscription = self._subscription(product, entitlement_id)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    monkeypatch.setenv("DISCORD_PURCHASE_MODE", "enforce")
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "entitlement")
    now = [NOW]
    ledger = Ledger(str(tmp_path / "lifecycle.sqlite3"), clock=lambda: now[0])
    purchases = DiscordPurchases(ledger, SKUS, application_id=APP, clock=lambda: now[0])
    return now, ledger, purchases


def records(ledger, table):
    with ledger.db() as db:
        return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]


@pytest.mark.asyncio
async def test_three_tier_upgrade_chain_issues_each_new_period_once(setup):
    now, ledger, purchases = setup
    client = Client()
    assert (await purchases.reconcile(client, credit=True))["credited"] == 1
    first = ledger.reserve(GUILD, USER, "chat")
    ledger.dispatch(first)
    ledger.settle(first, 1200)
    old_requests = records(ledger, "prepaid_requests")

    for offset, product, entitlement_id in ((10, "plus", 445), (20, "premium", 446)):
        now[0] = NOW + offset
        client.transition(product, entitlement_id, now[0], now[0] + 3600)
        result = await purchases.reconcile(client, credit=True)
        assert result["credited"] == 1 and result["revoked"] == 1
        assert (await purchases.reconcile(client, credit=True))["credited"] == 0
        assert ledger.summary(GUILD)["remaining"]["chat"] == PRODUCTS[product].allowances["chat"]
        assert records(ledger, "prepaid_requests") == old_requests

    grants = records(ledger, "prepaid_grants")
    assert len(grants) == 3
    assert [row["revoked"] for row in grants] == [1, 1, 0]
    assert [row["product"] for row in grants] == ["basic", "plus", "premium"]
    assert sum(PRODUCTS[row["product"]].max_api_cost for row in grants) == 5_540_000
    premium_request = ledger.reserve(GUILD, USER, "chat")
    purchases.invalidate()  # Repeated subscription/entitlement gateway events.
    purchases.invalidate()
    restarted = DiscordPurchases(ledger, SKUS, application_id=APP, clock=lambda: now[0])
    assert (await restarted.reconcile(client, credit=True))["credited"] == 0
    assert len(records(ledger, "prepaid_grants")) == 3
    assert ledger.summary(GUILD)["remaining"]["chat"] == 999
    assert premium_request in {row["id"] for row in records(ledger, "prepaid_requests")}


@pytest.mark.asyncio
async def test_scheduled_downgrade_keeps_current_tier_until_next_period(setup):
    now, ledger, purchases = setup
    client = Client("premium", 446)
    assert (await purchases.reconcile(client, credit=True))["credited"] == 1
    client.subscription.renewal_sku_ids = [111]
    assert (await purchases.reconcile(client, credit=True))["credited"] == 0
    assert ledger.summary(GUILD)["plans"] == ["Premium"]
    now[0] = NOW + 3600
    client.transition("basic", 447, now[0], now[0] + 3600)
    result = await purchases.reconcile(client, credit=True)
    assert result["credited"] == 1 and result["revoked"] == 1
    assert ledger.summary(GUILD)["plans"] == ["Basic"]
    assert (await purchases.reconcile(client, credit=True))["credited"] == 0


@pytest.mark.asyncio
async def test_same_tier_replacement_cannot_refill_unchanged_period(setup):
    now, ledger, purchases = setup
    client = Client()
    assert (await purchases.reconcile(client, credit=True))["credited"] == 1
    used = ledger.reserve(GUILD, USER, "chat")
    ledger.dispatch(used)
    ledger.settle(used, 1000)
    requests_before = records(ledger, "prepaid_requests")
    grants_before = records(ledger, "prepaid_grants")

    # A replacement entitlement ID alone is not proof of a new billing cycle.
    now[0] = NOW + 1
    client.entitlements_list[0].ends_at = dt(now[0])
    client.entitlements_list.append(client._entitlement(448, "basic"))
    client.subscription.entitlement_ids = [448]
    with pytest.raises(Denied):
        await purchases.reconcile(client, credit=True)
    assert records(ledger, "prepaid_grants") == grants_before
    assert records(ledger, "prepaid_requests") == requests_before


@pytest.mark.asyncio
async def test_true_same_tier_renewal_grants_once_after_restart(setup):
    now, ledger, purchases = setup
    client = Client()
    assert (await purchases.reconcile(client, credit=True))["credited"] == 1
    used = ledger.reserve(GUILD, USER, "chat")
    ledger.dispatch(used)
    ledger.settle(used, 1000)
    requests_before = records(ledger, "prepaid_requests")

    now[0] = NOW + 3600
    client.transition("basic", 449, now[0], now[0] + 3600)
    result = await purchases.reconcile(client, credit=True)
    assert result["credited"] == 1 and result["revoked"] == 1
    assert ledger.summary(GUILD)["remaining"]["chat"] == PRODUCTS["basic"].allowances["chat"]
    assert (await purchases.reconcile(client, credit=True))["credited"] == 0
    restarted = DiscordPurchases(ledger, SKUS, application_id=APP, clock=lambda: now[0])
    assert (await restarted.reconcile(client, credit=True))["credited"] == 0
    assert len(records(ledger, "prepaid_grants")) == 2
    assert records(ledger, "prepaid_requests") == requests_before


@pytest.mark.asyncio
async def test_refund_keeps_settled_dispatched_and_reserved_usage(setup):
    now, ledger, purchases = setup
    client = Client()
    await purchases.reconcile(client, credit=True)
    settled = ledger.reserve(GUILD, USER, "chat")
    ledger.dispatch(settled)
    ledger.settle(settled, 1000)
    dispatched = ledger.reserve(GUILD, USER, "chat")
    ledger.dispatch(dispatched)
    ledger.reserve(GUILD, USER, "chat")
    requests_before = records(ledger, "prepaid_requests")
    receipt = records(ledger, "prepaid_grants")[0]["receipt"]

    assert purchases.invalidate_settlement(receipt, "refund", "b" * 64, "refund-1", "reviewer")
    client.subscription.current_period_end = dt(NOW + 3601)
    assert (await purchases.reconcile(client, credit=True))["credited"] == 0
    assert records(ledger, "prepaid_requests") == requests_before
    assert len(records(ledger, "prepaid_grants")) == 1
    assert records(ledger, "prepaid_grants")[0]["revoked"] == 1
    with pytest.raises(Denied):
        ledger.reserve(GUILD, USER, "chat")
    restarted = DiscordPurchases(ledger, SKUS, application_id=APP, clock=lambda: now[0])
    assert (await restarted.reconcile(client, credit=True))["credited"] == 0
    assert records(ledger, "prepaid_requests") == requests_before
