import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
import src.prepaid as prepaid
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
    # Separate Ledger instances model concurrent callers that do not share
    # Python state; SQLite must still serialize reservations against the file.
    clients = [Ledger(ledger.path, clock=lambda: NOW) for _ in range(16)]

    def attempt(i):
        try:
            return clients[i % len(clients)].reserve(1, 2, "chat")
        except Denied:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        result = list(pool.map(attempt, range(150)))
    assert sum(r is not None for r in result) == PRODUCTS["basic"].allowances["chat"]
    assert ledger.summary(1)["remaining"]["chat"] == 0


def test_database_setup_retries_while_exclusive_writer_holds_lock(ledger):
    ledger.credit(payment())
    blocker = sqlite3.connect(ledger.path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    started = Event()

    def reserve():
        started.set()
        return ledger.reserve(1, 2, "chat")

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(reserve)
            assert started.wait(timeout=2)
            sleep(0.4)
            assert not result.done()
            blocker.execute("COMMIT")
            assert result.result(timeout=5)
    finally:
        blocker.close()


def test_database_setup_contention_retry_is_bounded(ledger, monkeypatch):
    ledger.credit(payment())
    blocker = sqlite3.connect(ledger.path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    monkeypatch.setattr(prepaid, "SQLITE_BEGIN_RETRY_SECONDS", 0.05)
    started = monotonic()

    try:
        with pytest.raises(sqlite3.OperationalError):
            ledger.reserve(1, 2, "chat")
        assert monotonic() - started < 1.5
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    assert ledger.summary(1)["remaining"]["chat"] == PRODUCTS["basic"].allowances["chat"]


def test_commit_retries_until_reader_releases_shared_lock(ledger):
    ledger.credit(payment())
    reader = sqlite3.connect(ledger.path, isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM prepaid_grants").fetchall()
    started = Event()

    def reserve():
        started.set()
        return ledger.reserve(1, 2, "chat")

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(reserve)
            assert started.wait(timeout=2)
            sleep(0.4)
            assert not result.done()
            reader.execute("ROLLBACK")
            assert result.result(timeout=5)
    finally:
        reader.close()


def test_database_setup_error_closes_connection(ledger, monkeypatch):
    class BrokenConnection:
        closed = False
        rolled_back = False

        def execute(self, statement):
            if statement == "PRAGMA synchronous=FULL":
                raise sqlite3.OperationalError("synthetic setup failure")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    connection = BrokenConnection()
    fake_sqlite = SimpleNamespace(
        connect=lambda *args, **kwargs: connection,
        Row=sqlite3.Row,
        OperationalError=sqlite3.OperationalError,
        SQLITE_BUSY=sqlite3.SQLITE_BUSY,
        SQLITE_LOCKED=sqlite3.SQLITE_LOCKED,
    )
    monkeypatch.setattr(prepaid, "sqlite3", fake_sqlite)

    with pytest.raises(sqlite3.OperationalError, match="synthetic setup failure"):
        with ledger.db():
            pass

    assert connection.rolled_back
    assert connection.closed


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
