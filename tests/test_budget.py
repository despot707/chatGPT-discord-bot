from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from src.budget import BudgetError, BudgetExceeded, BudgetLedger, BudgetPolicy

LA = ZoneInfo("America/Los_Angeles")


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def ledger(
    path: Path,
    clock: MutableClock,
    *,
    baseline: int | None = 0,
    policy: BudgetPolicy | None = None,
) -> BudgetLedger:
    return BudgetLedger(policy or BudgetPolicy(), path, baseline, clock=clock)


def test_first_month_requires_baseline_and_can_be_initialized_once(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite3"
    clock = MutableClock(datetime(2026, 9, 27, 12, tzinfo=LA))
    first = ledger(path, clock, baseline=None)

    with pytest.raises(BudgetExceeded, match="Opening month spend is unknown"):
        first.reserve("luna", 1)
    blocked_snapshot = first.snapshot()
    assert blocked_snapshot["baseline_known"] is False
    assert blocked_snapshot["monthly_remaining_micros"] == 0
    assert blocked_snapshot["blocked_reason"] == "opening_month_spend_unknown"

    configured = ledger(path, clock, baseline=1_250_000)
    assert configured.snapshot()["monthly_spent_micros"] == 1_250_000
    assert configured.snapshot()["monthly_remaining_micros"] == 8_750_000

    reopened = ledger(path, clock, baseline=6_000_000)
    assert reopened.snapshot()["monthly_spent_micros"] == 1_250_000


def test_opening_baseline_never_reseeds_a_future_month(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite3"
    clock = MutableClock(datetime(2026, 9, 27, 12, tzinfo=LA))
    first_month = ledger(path, clock, baseline=500_000)
    assert first_month.snapshot()["monthly_spent_micros"] == 500_000

    clock.value = datetime(2026, 10, 1, 0, 10, tzinfo=LA)
    next_month = ledger(path, clock, baseline=500_000)
    snapshot = next_month.snapshot()
    assert snapshot["monthly_spent_micros"] == 0
    assert snapshot["monthly_remaining_micros"] == 10_000_000


def test_monthly_baseline_is_total_only_and_daily_share_rounds_down(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 2, 2, 12, tzinfo=LA))
    budget = ledger(tmp_path / "budget.sqlite3", clock, baseline=9_000_000)
    snap = budget.snapshot()
    assert snap["monthly_spent_micros"] == 9_000_000
    assert snap["luna"]["monthly_spent_micros"] == 0
    assert snap["luna"]["monthly_limit_micros"] == 7_000_000
    assert snap["extras"]["monthly_limit_micros"] == 3_000_000
    assert snap["luna"]["daily_limit_micros"] == 250_000
    assert snap["extras"]["daily_limit_micros"] == 107_142
    with pytest.raises(BudgetExceeded, match="Monthly budget"):
        budget.reserve("luna", 1_000_001)


def test_reservations_survive_restart_and_unknown_settlement_keeps_full_hold(
    tmp_path: Path,
) -> None:
    path = tmp_path / "budget.sqlite3"
    clock = MutableClock(datetime(2026, 9, 27, 12, tzinfo=LA))
    first = ledger(path, clock)
    reservation = first.reserve("luna", 100_000)

    restarted = ledger(path, clock, baseline=5_000_000)
    assert restarted.snapshot()["luna"]["reserved_micros"] == 100_000
    with pytest.raises(BudgetError, match="Unknown reservation"):
        restarted.settle("does-not-exist", 1)
    assert restarted.snapshot()["luna"]["reserved_micros"] == 100_000

    restarted.settle(reservation.id, 40_000)
    restarted.settle(reservation.id, 40_000)  # safe retry after an uncertain response
    snap = restarted.snapshot()
    assert snap["monthly_spent_micros"] == 40_000
    assert snap["luna"]["reserved_micros"] == 0
    assert snap["luna"]["monthly_remaining_micros"] == 6_960_000


def test_reserve_many_is_atomic_and_settlement_overrun_locks_permanently(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 9, 27, 12, tzinfo=LA))
    budget = ledger(tmp_path / "budget.sqlite3", clock)
    with pytest.raises(BudgetExceeded):
        budget.reserve_many({"luna": 230_000, "extras": 110_000})
    assert budget.snapshot()["reserved_micros"] == 0

    reservation = budget.reserve("extras", 1_000)
    with pytest.raises(BudgetExceeded, match="ledger is locked"):
        budget.settle(reservation.id, 1_001)
    assert budget.snapshot()["locked"] is True
    assert budget.snapshot()["blocked_reason"] == "ledger_locked"
    with pytest.raises(BudgetExceeded, match="locked"):
        budget.reserve("luna", 1)
    with pytest.raises(BudgetExceeded, match="locked"):
        ledger(tmp_path / "budget.sqlite3", clock).reserve("luna", 1)


def test_explicit_contract_lock_persists_a_safe_reason(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite3"
    clock = MutableClock(datetime(2026, 9, 27, 12, tzinfo=LA))
    budget = ledger(path, clock)
    with pytest.raises(ValueError, match="internal code"):
        budget.lock("provider response contained secret data")
    budget.lock("provider_contract_unknown_model_tier")
    restarted = ledger(path, clock)
    assert restarted.snapshot()["lock_reason"] == "provider_contract_unknown_model_tier"
    with pytest.raises(BudgetExceeded, match="locked"):
        restarted.reserve("luna", 1)


def test_midnight_and_month_rollover_keep_pending_hold_and_late_cost(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite3"
    clock = MutableClock(datetime(2026, 10, 31, 23, 50, tzinfo=LA))
    budget = ledger(path, clock)
    reservation = budget.reserve("luna", 100_000)
    before = budget.snapshot()
    assert before["day_resets_at"] == "2026-11-01T00:00:00-07:00"
    assert before["month_resets_at"] == "2026-11-01T00:00:00-07:00"

    clock.value = datetime(2026, 11, 1, 0, 10, tzinfo=LA)
    rotated = ledger(path, clock)
    snap = rotated.snapshot()
    assert snap["monthly_spent_micros"] == 0
    assert snap["reserved_micros"] == 100_000
    assert snap["monthly_remaining_micros"] == 9_900_000
    assert snap["luna"]["daily_remaining_micros"] == 233_333
    current_day_hold = rotated.reserve("luna", 233_333)
    with pytest.raises(BudgetExceeded, match="daily budget"):
        rotated.reserve("luna", 1)
    rotated.cancel_before_dispatch([current_day_hold.id])

    rotated.settle(reservation.id, 80_000)
    after = rotated.snapshot()
    assert after["monthly_spent_micros"] == 80_000
    assert after["luna"]["daily_spent_micros"] == 0
    assert after["luna"]["daily_remaining_micros"] == 233_333
    assert after["luna"]["daily_reserved_micros"] == 0
    assert after["luna"]["reserved_micros"] == 0


def test_late_same_month_settlement_preserves_only_origin_day_charge(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite3"
    clock = MutableClock(datetime(2026, 9, 27, 23, 50, tzinfo=LA))
    budget = ledger(path, clock)
    reservation = budget.reserve("luna", 100_000)

    clock.value = datetime(2026, 9, 28, 0, 10, tzinfo=LA)
    restarted = ledger(path, clock)
    assert restarted.snapshot()["luna"]["daily_remaining_micros"] == 233_333
    restarted.settle(reservation.id, 80_000)
    snapshot = restarted.snapshot()
    assert snapshot["monthly_spent_micros"] == 80_000
    assert snapshot["luna"]["daily_spent_micros"] == 0
    assert snapshot["luna"]["daily_reserved_micros"] == 0
    assert snapshot["luna"]["daily_remaining_micros"] == 233_333
    assert snapshot["monthly_remaining_micros"] == 9_920_000


def test_dst_day_length_and_reset_are_local_calendar_boundaries(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 11, 1, 1, 30, tzinfo=LA, fold=0))
    budget = ledger(tmp_path / "budget.sqlite3", clock)
    snap = budget.snapshot()
    assert snap["day_resets_at"] == "2026-11-02T00:00:00-08:00"
    assert snap["daily_days_in_month"] == 30


def test_concurrent_instances_never_overreserve_daily_or_monthly_limits(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite3"
    clock = MutableClock(datetime(2026, 9, 27, 12, tzinfo=LA))
    ledger(path, clock)

    def attempt(_: int) -> int:
        instance = ledger(path, clock)
        try:
            return instance.reserve("luna", 25_000).reserved_micros
        except BudgetExceeded:
            return 0

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(20)))
    assert sum(results) == 9 * 25_000
    snapshot = ledger(path, clock).snapshot()
    assert snapshot["luna"]["reserved_micros"] == 225_000
    assert snapshot["luna"]["daily_remaining_micros"] == 8_333


def test_explicit_pre_dispatch_cancel_releases_only_all_pending_ids(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 9, 27, 12, tzinfo=LA))
    budget = ledger(tmp_path / "budget.sqlite3", clock)
    first = budget.reserve("luna", 100_000)
    second = budget.reserve("extras", 10_000)
    budget.cancel_before_dispatch([first.id, second.id])
    assert budget.snapshot()["reserved_micros"] == 0

    settled = budget.reserve("luna", 1_000)
    budget.settle(settled.id, 500)
    with pytest.raises(BudgetError, match="Only pending"):
        budget.cancel_before_dispatch([settled.id])
    assert budget.snapshot()["monthly_spent_micros"] == 500


def test_budget_path_or_policy_errors_fail_closed(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 9, 27, 12, tzinfo=LA))
    path = tmp_path / "budget.sqlite3"
    ledger(path, clock)
    changed_policy = BudgetPolicy(monthly_limit_micros=11_000_000, luna_monthly_micros=7_000_000)
    with pytest.raises(BudgetError, match="policy changed"):
        ledger(path, clock, policy=changed_policy)

    with pytest.raises(BudgetError):
        BudgetLedger(BudgetPolicy(), tmp_path, 0, clock=clock)

    with pytest.raises(ValueError, match="durable file path"):
        BudgetLedger(BudgetPolicy(), ":memory:", 0, clock=clock)
