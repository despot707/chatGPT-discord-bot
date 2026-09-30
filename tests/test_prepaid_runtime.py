import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from src.prepaid import Denied, Ledger
from src.prepaid_runtime import PaidManager, Runtime

from tests.test_prepaid import NOW, payment


def manifest(**kw):
    base = dict(
        reviewed_at=NOW,
        approved=False,
        receipts_verified=False,
        provider_contracts_verified=False,
        storage_lifecycle_verified=False,
        workload_isolated=False,
        native_compute_cap_micros=10_000_000,
        native_agent_cap_micros=0,
        native_openai_cap_micros=1_000_000,
        hosting_liability_micros=10_000_000,
        operator_funding_micros=0,
        reconciliation_current=False,
        image_contract_verified=False,
    )
    base.update(kw)
    return base


def make(tmp_path, data):
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(data))
    ledger = Ledger(str(tmp_path / "paid.db"), clock=lambda: NOW)
    return Runtime(ledger, str(path), clock=lambda: NOW), ledger


def test_unreviewed_launch_is_locked(tmp_path):
    rt, _ = make(tmp_path, manifest())
    with pytest.raises(Denied):
        rt.ready()


def test_revenue_must_cover_hosting_not_just_ai(tmp_path, monkeypatch):
    # This unit isolates the hosting calculation; purchase binding is exercised
    # by the Discord reconciliation lifecycle tests.
    monkeypatch.setenv("DISCORD_PURCHASE_MODE", "enforce")
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "222")
    monkeypatch.setenv("DISCORD_SKU_MAP", "111:basic")
    monkeypatch.setattr("src.discord_purchases.assert_purchase_current", lambda *args, **kw: None)
    data = manifest(
        approved=True,
        receipts_verified=True,
        provider_contracts_verified=True,
        storage_lifecycle_verified=True,
        workload_isolated=True,
        reconciliation_current=True,
    )
    rt, ledger = make(tmp_path, data)
    ledger.credit(payment())
    with pytest.raises(Denied):
        rt.ready()
    data["operator_funding_micros"] = 11_000_000
    rt, _ = make(tmp_path, data)
    rt.ready()


def test_stale_price_review_rejected(tmp_path):
    rt, _ = make(tmp_path, manifest(reviewed_at=NOW - 8 * 86400))
    with pytest.raises(Denied):
        rt.ready()


@pytest.mark.asyncio
async def test_missing_scope_never_uses_legacy_manager(tmp_path):
    rt, _ = make(tmp_path, manifest())
    legacy = NS(complete=AsyncMock())
    manager = PaidManager(legacy, rt)
    with pytest.raises(Denied):
        await manager.complete(messages=[{"role": "user", "content": "hi"}])
    legacy.complete.assert_not_called()


def test_housekeeping_allows_same_interaction_checks_but_throttles_new_ones(tmp_path, monkeypatch):
    from src import prepaid_runtime as pr

    rt, _ = make(tmp_path, manifest())
    monkeypatch.setattr(pr, "_INSTANCE", rt)
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    assert pr.free_interaction_allowed(1, 123)
    assert pr.free_interaction_allowed(1, 123)
    assert not pr.free_interaction_allowed(1, 124)


def test_second_process_lease_refused(tmp_path):
    import sys

    if sys.platform == "win32":
        pytest.skip("Paid runtime is single-writer Linux; preview supported on Windows")
    a, _ = make(tmp_path, manifest())
    b, _ = make(tmp_path, manifest())
    a.acquire_process()
    with pytest.raises(Denied):
        b.acquire_process()
    a._lease.close()
