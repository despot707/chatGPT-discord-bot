from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from src.prepaid import COSTS, PRODUCTS, Denied, Ledger, Payment, validate_products

NOW = 1790726400


@pytest.fixture
def ledger(tmp_path):
    return Ledger(str(tmp_path / "prepaid.sqlite3"), clock=lambda: NOW)


def payment(receipt="sale1", guild=1, product="basic", **kwargs):
    p = PRODUCTS[product]
    args = dict(
        receipt_id=receipt,
        guild_id=guild,
        product=product,
        gross_micros=p.price_cents * 10000,
        net_micros=p.price_cents * 6500,
        starts=NOW - 1,
        ends=NOW + 30 * 86400,
        source="verified_payment",
        currency="USD",
    )
    args.update(kwargs)
    return Payment(**args)


def test_price_floor_and_maximum_costs():
    assert PRODUCTS["basic"].price_cents == 99
    validate_products()
    for p in PRODUCTS.values():
        assert p.price_cents >= 99
        assert p.max_api_cost <= p.price_cents * 10000 * 35 // 100
        assert (
            p.allowances.get("core", 0) * COSTS["core"] + p.storage_bytes * 450000 // (1024**3)
            <= p.price_cents * 1000
        )


def test_no_payment_no_service(ledger):
    with pytest.raises(Denied):
        ledger.reserve(1, 2, "chat")


def test_receipt_replay_cannot_multiply_or_move_credit(ledger):
    assert ledger.credit(payment()) is True
    assert ledger.credit(payment()) is False
    with pytest.raises(Denied):
        ledger.credit(payment(guild=2))
    assert ledger.summary(1)["remaining"]["chat"] == PRODUCTS["basic"].allowances["chat"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"currency": "EUR"},
        {"net_micros": 1},
        {"gross_micros": 0},
        {"source": "test_entitlement"},
        {"ends": NOW - 1},
        {"ends": NOW + 90 * 86400},
        {"guild_id": True},
    ],
)
def test_unfunded_or_invalid_grants_rejected(ledger, kwargs):
    with pytest.raises((Denied, ValueError)):
        ledger.credit(replace(payment(), **kwargs))


def test_isolation_extras_and_expiration(ledger):
    ledger.credit(payment())
    with pytest.raises(Denied):
        ledger.reserve(2, 2, "chat")
    with pytest.raises(Denied):
        ledger.reserve(1, 2, "search")
    ledger.credit(payment("pack", product="search_pack"))
    r = ledger.reserve(1, 2, "search")
    ledger.dispatch(r)
    ledger.settle(r, 10000)
    assert (
        ledger.summary(1)["remaining"]["search"] == PRODUCTS["search_pack"].allowances["search"] - 1
    )
    later = Ledger(ledger.path, clock=lambda: NOW + 32 * 86400)
    with pytest.raises(Denied):
        later.reserve(1, 2, "chat")


def test_parallel_requests_never_overdraw(ledger):
    ledger.credit(payment())

    def attempt(i):
        try:
            return ledger.reserve(1, 2, "chat")
        except Denied:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        result = list(pool.map(attempt, range(150)))
    assert sum(r is not None for r in result) == PRODUCTS["basic"].allowances["chat"]
    assert ledger.summary(1)["remaining"]["chat"] == 0


def test_cancel_only_before_dispatch_and_timeout_holds_survive(ledger):
    ledger.credit(payment())
    r = ledger.reserve(1, 2, "chat")
    ledger.cancel(r)
    assert ledger.summary(1)["remaining"]["chat"] == PRODUCTS["basic"].allowances["chat"]
    r = ledger.reserve(1, 2, "chat")
    ledger.dispatch(r)
    with pytest.raises(Denied):
        ledger.cancel(r)
    again = Ledger(ledger.path, clock=lambda: NOW)
    assert again.summary(1)["remaining"]["chat"] == PRODUCTS["basic"].allowances["chat"] - 1


def test_overrun_locks_all_paid_requests(ledger):
    ledger.credit(payment())
    r = ledger.reserve(1, 2, "chat")
    ledger.dispatch(r)
    with pytest.raises(Denied):
        ledger.settle(r, COSTS["chat"] + 1)
    with pytest.raises(Denied):
        Ledger(ledger.path, clock=lambda: NOW).reserve(1, 2, "chat")


def test_refund_revokes_and_unfunded_addon_cannot_start_service(ledger):
    ledger.credit(payment("pack", product="chat_pack"))
    with pytest.raises(Denied):
        ledger.reserve(1, 2, "chat")
    ledger.credit(payment())
    ledger.revoke("sale1")
    with pytest.raises(Denied):
        ledger.reserve(1, 2, "chat")


def test_storage_growth_bounded_and_updates_dont_double_charge(ledger):
    ledger.credit(payment())
    ledger.storage(1, "profile:2", 2000)
    ledger.storage(1, "profile:2", 2000)
    assert ledger.summary(1)["storage_used"] == 2000
    with pytest.raises(Denied):
        ledger.storage(1, "profile:3", PRODUCTS["basic"].storage_bytes)
    ledger.storage(1, "profile:2", 0)
    assert ledger.summary(1)["storage_used"] == 0


def test_unexpected_feature_and_invalid_actual_rejected(ledger):
    ledger.credit(payment())
    with pytest.raises(Denied):
        ledger.reserve(1, 2, "new_unmetered_feature")
    r = ledger.reserve(1, 2, "chat")
    ledger.dispatch(r)
    with pytest.raises((Denied, ValueError)):
        ledger.settle(r, -1)
