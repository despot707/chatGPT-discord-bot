"""Durable, shared spend reservations for provider requests.

All amounts are integer microdollars. Pending reservations remain held against
monthly limits through day and month changes. Daily limits use the dispatch day.
Late settlements charge that original day; cross-month settlements also consume
the current monthly allowance without reducing its new daily allowance.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class BudgetError(RuntimeError):
    """The durable ledger could not safely authorize a request."""


class BudgetExceeded(BudgetError):
    """The request exceeds an available budget or the baseline is unknown."""


@dataclass(frozen=True)
class BudgetPolicy:
    monthly_limit_micros: int = 10_000_000
    luna_monthly_micros: int = 7_000_000
    timezone: str = "America/Los_Angeles"

    def __post_init__(self) -> None:
        _validate_amount(self.monthly_limit_micros, allow_zero=False, name="monthly limit")
        _validate_amount(self.luna_monthly_micros, allow_zero=True, name="Luna monthly limit")
        if self.luna_monthly_micros > self.monthly_limit_micros:
            raise ValueError("Luna monthly limit cannot exceed the total monthly limit")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown budget timezone: {self.timezone}") from exc

    @property
    def extras_monthly_micros(self) -> int:
        return self.monthly_limit_micros - self.luna_monthly_micros


@dataclass(frozen=True)
class Reservation:
    id: str
    bucket: str
    origin_day: str
    origin_month: str
    reserved_micros: int


def _validate_amount(value: int, *, allow_zero: bool, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer number of microdollars")
    if value < (0 if allow_zero else 1):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {qualifier}")


class BudgetLedger:
    """SQLite-backed budget shared by all callers using the same database path."""

    def __init__(
        self,
        policy: BudgetPolicy,
        path: str | Path,
        opening_month_spend_micros: int | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if opening_month_spend_micros is not None:
            _validate_amount(
                opening_month_spend_micros,
                allow_zero=True,
                name="opening month spend",
            )
        self.policy = policy
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("BudgetLedger requires a durable file path; ':memory:' is unsupported")
        self._zone = ZoneInfo(policy.timezone)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as db:
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS ledger_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS periods (
                        month TEXT PRIMARY KEY,
                        baseline_micros INTEGER,
                        baseline_known INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS reservations (
                        id TEXT PRIMARY KEY,
                        bucket TEXT NOT NULL CHECK (bucket IN ('luna', 'extras')),
                        origin_day TEXT NOT NULL,
                        origin_month TEXT NOT NULL,
                        reserved_micros INTEGER NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('pending', 'settled', 'cancelled')),
                        actual_micros INTEGER
                    );
                    CREATE TABLE IF NOT EXISTS charges (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        reservation_id TEXT NOT NULL,
                        charge_day TEXT NOT NULL,
                        charge_month TEXT NOT NULL,
                        bucket TEXT NOT NULL CHECK (bucket IN ('luna', 'extras')),
                        amount_micros INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS charges_day_bucket
                        ON charges(charge_day, bucket);
                    CREATE INDEX IF NOT EXISTS charges_month_bucket
                        ON charges(charge_month, bucket);
                    """
                )
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                expected_policy = {
                    "monthly_limit_micros": str(policy.monthly_limit_micros),
                    "luna_monthly_micros": str(policy.luna_monthly_micros),
                    "timezone": policy.timezone,
                }
                for key, value in expected_policy.items():
                    policy_key = "policy:" + key
                    saved = db.execute(
                        "SELECT value FROM ledger_state WHERE key=?", (policy_key,)
                    ).fetchone()
                    if saved is not None and saved[0] != value:
                        raise BudgetError(
                            "Budget policy changed for an existing ledger; requests are blocked."
                        )
                    db.execute(
                        "INSERT OR IGNORE INTO ledger_state(key, value) VALUES (?, ?)",
                        (policy_key, value),
                    )
                count = db.execute("SELECT COUNT(*) FROM periods").fetchone()[0]
                now = self._local_now()
                month = now.strftime("%Y-%m")
                activation_row = db.execute(
                    "SELECT value FROM ledger_state WHERE key='activation_month'"
                ).fetchone()
                if activation_row is None:
                    if count:
                        activation_month = db.execute("SELECT MIN(month) FROM periods").fetchone()[
                            0
                        ]
                    else:
                        activation_month = month
                    db.execute(
                        "INSERT INTO ledger_state(key, value) VALUES ('activation_month', ?)",
                        (activation_month,),
                    )
                else:
                    activation_month = activation_row[0]
                baseline_for_new_period = (
                    opening_month_spend_micros
                    if month == activation_month and opening_month_spend_micros is not None
                    else 0
                )
                baseline_known_for_new_period = int(
                    month != activation_month or opening_month_spend_micros is not None
                )
                db.execute(
                    "INSERT OR IGNORE INTO periods(month, baseline_micros, baseline_known) "
                    "VALUES (?, ?, ?)",
                    (
                        month,
                        baseline_for_new_period,
                        baseline_known_for_new_period,
                    ),
                )
                if opening_month_spend_micros is not None and month == activation_month:
                    db.execute(
                        "UPDATE periods SET baseline_micros=?, baseline_known=1 "
                        "WHERE month=? AND baseline_known=0",
                        (opening_month_spend_micros, month),
                    )
                db.commit()
        except BudgetError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise BudgetError("Budget ledger is unavailable; requests are blocked.") from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=30000")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    def _local_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise BudgetError("Budget clock must return a timezone-aware datetime.")
        return now.astimezone(self._zone)

    def _ensure_period(self, db: sqlite3.Connection, month: str) -> None:
        count = db.execute("SELECT COUNT(*) FROM periods").fetchone()[0]
        # The first observed month needs an explicit baseline. Later months start
        # at zero because this ledger has observed all prior usage itself.
        db.execute(
            "INSERT OR IGNORE INTO periods(month, baseline_micros, baseline_known) "
            "VALUES (?, 0, ?)",
            (month, int(count > 0)),
        )

    def _assert_unlocked(self, db: sqlite3.Connection) -> None:
        row = db.execute("SELECT value FROM ledger_state WHERE key='locked_reason'").fetchone()
        if row:
            raise BudgetExceeded("Budget ledger is locked; requests are blocked.")

    def reserve(self, bucket: str, maximum_micros: int) -> Reservation:
        return self.reserve_many({bucket: maximum_micros})[0]

    def reserve_many(self, amounts: dict[str, int]) -> list[Reservation]:
        """Atomically reserve several buckets, or reserve none of them."""
        if not amounts:
            raise ValueError("At least one reservation is required")
        normalized: list[tuple[str, int]] = []
        for bucket, amount in amounts.items():
            if bucket not in ("luna", "extras"):
                raise ValueError("bucket must be 'luna' or 'extras'")
            _validate_amount(amount, allow_zero=False, name="reservation maximum")
            normalized.append((bucket, amount))

        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._assert_unlocked(db)
                now = self._local_now()
                day = now.date().isoformat()
                month = now.strftime("%Y-%m")
                self._ensure_period(db, month)
                baseline, known = db.execute(
                    "SELECT baseline_micros, baseline_known FROM periods WHERE month=?", (month,)
                ).fetchone()
                if not known:
                    raise BudgetExceeded(
                        "Opening month spend is unknown; requests are blocked until it is supplied."
                    )
                active_total = db.execute(
                    "SELECT COALESCE(SUM(reserved_micros), 0) FROM reservations WHERE status='pending'"
                ).fetchone()[0]
                month_total_charges = db.execute(
                    "SELECT COALESCE(SUM(amount_micros), 0) FROM charges WHERE charge_month=?",
                    (month,),
                ).fetchone()[0]
                limits = {
                    "luna": self.policy.luna_monthly_micros,
                    "extras": self.policy.extras_monthly_micros,
                }
                pending_by_bucket = dict(
                    db.execute(
                        "SELECT bucket, COALESCE(SUM(reserved_micros), 0) "
                        "FROM reservations WHERE status='pending' GROUP BY bucket"
                    ).fetchall()
                )
                pending_today_by_bucket = dict(
                    db.execute(
                        "SELECT bucket, COALESCE(SUM(reserved_micros), 0) "
                        "FROM reservations WHERE status='pending' AND origin_day=? GROUP BY bucket",
                        (day,),
                    ).fetchall()
                )
                month_by_bucket = dict(
                    db.execute(
                        "SELECT bucket, COALESCE(SUM(amount_micros), 0) FROM charges "
                        "WHERE charge_month=? GROUP BY bucket",
                        (month,),
                    ).fetchall()
                )
                day_by_bucket = dict(
                    db.execute(
                        "SELECT bucket, COALESCE(SUM(amount_micros), 0) FROM charges "
                        "WHERE charge_day=? GROUP BY bucket",
                        (day,),
                    ).fetchall()
                )
                days = _days_in_month(now.date())
                daily_limits = {name: amount // days for name, amount in limits.items()}
                addition = sum(amount for _, amount in normalized)
                if (
                    baseline + month_total_charges + active_total + addition
                    > self.policy.monthly_limit_micros
                ):
                    raise BudgetExceeded("Monthly budget is exhausted; this request was not sent.")
                for bucket, amount in normalized:
                    bucket_month_usage = month_by_bucket.get(bucket, 0) + pending_by_bucket.get(
                        bucket, 0
                    )
                    bucket_day_usage = day_by_bucket.get(bucket, 0) + pending_today_by_bucket.get(
                        bucket, 0
                    )
                    if bucket_month_usage + amount > limits[bucket]:
                        raise BudgetExceeded(f"{bucket.title()} monthly budget is exhausted.")
                    if bucket_day_usage + amount > daily_limits[bucket]:
                        raise BudgetExceeded(f"{bucket.title()} daily budget is exhausted.")
                    # Account for earlier entries in this same atomic request.
                    pending_by_bucket[bucket] = pending_by_bucket.get(bucket, 0) + amount
                    pending_today_by_bucket[bucket] = (
                        pending_today_by_bucket.get(bucket, 0) + amount
                    )
                    active_total += amount
                reservations: list[Reservation] = []
                for bucket, amount in normalized:
                    reservation = Reservation(
                        id=str(uuid.uuid4()),
                        bucket=bucket,
                        origin_day=day,
                        origin_month=month,
                        reserved_micros=amount,
                    )
                    db.execute(
                        "INSERT INTO reservations "
                        "(id, bucket, origin_day, origin_month, reserved_micros, status) "
                        "VALUES (?, ?, ?, ?, ?, 'pending')",
                        (reservation.id, bucket, day, month, amount),
                    )
                    reservations.append(reservation)
                db.commit()
                return reservations
        except BudgetError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise BudgetError("Budget ledger is unavailable; requests are blocked.") from exc

    def settle(self, reservation_id: str, actual_micros: int) -> None:
        """Replace a hold with a verified actual cost; unknowns retain their hold."""
        _validate_amount(actual_micros, allow_zero=True, name="actual cost")
        overflow = False
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._assert_unlocked(db)
                row = db.execute(
                    "SELECT bucket, origin_day, origin_month, reserved_micros, status, actual_micros "
                    "FROM reservations WHERE id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    raise BudgetError("Unknown reservation; no budget was released.")
                bucket, origin_day, origin_month, reserved, status, prior_actual = row
                if status == "settled":
                    if prior_actual == actual_micros:
                        db.commit()
                        return
                    raise BudgetError("Reservation was already settled with a different amount.")
                if status != "pending":
                    raise BudgetError("Reservation is no longer pending.")
                if actual_micros > reserved:
                    db.execute(
                        "INSERT OR REPLACE INTO ledger_state(key, value) VALUES "
                        "('locked_reason', 'verified cost exceeded reservation')"
                    )
                    db.commit()
                    overflow = True
                else:
                    now = self._local_now()
                    settle_month = now.strftime("%Y-%m")
                    db.execute(
                        "UPDATE reservations SET status='settled', actual_micros=? WHERE id=?",
                        (actual_micros, reservation_id),
                    )
                    db.execute(
                        "INSERT INTO charges(reservation_id, charge_day, charge_month, bucket, amount_micros) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (reservation_id, origin_day, origin_month, bucket, actual_micros),
                    )
                    if settle_month != origin_month:
                        db.execute(
                            "INSERT INTO charges "
                            "(reservation_id, charge_day, charge_month, bucket, amount_micros) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (reservation_id, "", settle_month, bucket, actual_micros),
                        )
                    db.commit()
        except BudgetError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise BudgetError("Budget ledger is unavailable; requests are blocked.") from exc
        if overflow:
            raise BudgetExceeded(
                "Verified cost exceeded its reservation; the budget ledger is locked."
            )

    def lock(self, reason: str) -> None:
        """Permanently block new work after an untrusted response contract violation."""
        if not isinstance(reason, str) or re.fullmatch(r"[a-z0-9_:-]{1,100}", reason) is None:
            raise ValueError("lock reason must be a short lowercase internal code")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT OR IGNORE INTO ledger_state(key, value) VALUES ('locked_reason', ?)",
                    (reason,),
                )
                db.commit()
        except (sqlite3.Error, OSError) as exc:
            raise BudgetError("Budget ledger is unavailable; requests are blocked.") from exc

    def cancel_before_dispatch(self, reservation_ids: list[str]) -> None:
        """Release only holds whose requests are proven not to have been dispatched."""
        if not reservation_ids:
            return
        if len(set(reservation_ids)) != len(reservation_ids):
            raise ValueError("Reservation IDs must be unique")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                for reservation_id in reservation_ids:
                    row = db.execute(
                        "SELECT status FROM reservations WHERE id=?", (reservation_id,)
                    ).fetchone()
                    if row is None or row[0] != "pending":
                        raise BudgetError(
                            "Only pending reservations can be cancelled before dispatch."
                        )
                db.executemany(
                    "UPDATE reservations SET status='cancelled' WHERE id=?",
                    ((reservation_id,) for reservation_id in reservation_ids),
                )
                db.commit()
        except BudgetError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise BudgetError("Budget ledger is unavailable; requests are blocked.") from exc

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable view of limits, spend, holds, and resets."""
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                now = self._local_now()
                day = now.date().isoformat()
                month = now.strftime("%Y-%m")
                self._ensure_period(db, month)
                baseline, known = db.execute(
                    "SELECT baseline_micros, baseline_known FROM periods WHERE month=?", (month,)
                ).fetchone()
                locked = db.execute(
                    "SELECT value FROM ledger_state WHERE key='locked_reason'"
                ).fetchone()
                total_spent_month = db.execute(
                    "SELECT COALESCE(SUM(amount_micros), 0) FROM charges WHERE charge_month=?",
                    (month,),
                ).fetchone()[0]
                bucket_month = dict(
                    db.execute(
                        "SELECT bucket, SUM(amount_micros) FROM charges WHERE charge_month=? "
                        "GROUP BY bucket",
                        (month,),
                    ).fetchall()
                )
                bucket_day = dict(
                    db.execute(
                        "SELECT bucket, SUM(amount_micros) FROM charges WHERE charge_day=? "
                        "GROUP BY bucket",
                        (day,),
                    ).fetchall()
                )
                pending = dict(
                    db.execute(
                        "SELECT bucket, SUM(reserved_micros) FROM reservations WHERE status='pending' "
                        "GROUP BY bucket"
                    ).fetchall()
                )
                pending_today = dict(
                    db.execute(
                        "SELECT bucket, SUM(reserved_micros) FROM reservations "
                        "WHERE status='pending' AND origin_day=? GROUP BY bucket",
                        (day,),
                    ).fetchall()
                )
                total_pending = sum(pending.values())
                locked_reason = locked[0] if locked else None
                db.commit()
        except (sqlite3.Error, OSError) as exc:
            raise BudgetError("Budget ledger is unavailable; requests are blocked.") from exc

        limits = {
            "luna": self.policy.luna_monthly_micros,
            "extras": self.policy.extras_monthly_micros,
        }
        days = _days_in_month(now.date())
        buckets: dict[str, object] = {}
        for name, monthly_limit in limits.items():
            daily_limit = monthly_limit // days
            month_spent = bucket_month.get(name, 0)
            day_spent = bucket_day.get(name, 0)
            held = pending.get(name, 0)
            held_today = pending_today.get(name, 0)
            buckets[name] = {
                "month": _usage(month_spent, held, monthly_limit, bool(known)),
                "day": _usage(day_spent, held_today, daily_limit, bool(known)),
            }
        next_day = datetime.combine(now.date() + timedelta(days=1), time.min, self._zone)
        next_month_date = date(now.year + (now.month == 12), now.month % 12 + 1, 1)
        next_month = datetime.combine(next_month_date, time.min, self._zone)
        total_limit = self.policy.monthly_limit_micros
        total_spent = baseline + total_spent_month
        total_used = total_spent + total_pending
        result: dict[str, object] = {
            "timezone": self.policy.timezone,
            "as_of": now.isoformat(),
            "day_resets_at": next_day.isoformat(),
            "month_resets_at": next_month.isoformat(),
            "baseline_known": bool(known),
            "locked": bool(locked),
            "blocked_reason": (
                "ledger_locked"
                if locked_reason
                else "opening_month_spend_unknown"
                if not known
                else None
            ),
            "lock_reason": locked_reason,
            "monthly_spent_micros": total_spent,
            "reserved_micros": total_pending,
            "monthly_limit_micros": total_limit,
            "monthly_remaining_micros": max(0, total_limit - total_used) if known else 0,
            "total": _usage(total_spent, total_pending, total_limit, bool(known)),
            "buckets": buckets,
            "daily_days_in_month": days,
        }
        for name, monthly_limit in limits.items():
            daily_limit = monthly_limit // days
            month_spent = bucket_month.get(name, 0)
            day_spent = bucket_day.get(name, 0)
            held = pending.get(name, 0)
            held_today = pending_today.get(name, 0)
            result[name] = {
                "monthly_limit_micros": monthly_limit,
                "daily_limit_micros": daily_limit,
                "monthly_spent_micros": month_spent,
                "daily_spent_micros": day_spent,
                "reserved_micros": held,
                "daily_reserved_micros": held_today,
                "monthly_remaining_micros": max(0, monthly_limit - month_spent - held)
                if known
                else 0,
                "daily_remaining_micros": max(0, daily_limit - day_spent - held_today)
                if known
                else 0,
            }
        return result


def _usage(spent: int, reserved: int, limit: int, known: bool = True) -> dict[str, int]:
    return {
        "spent_micros": spent,
        "reserved_micros": reserved,
        "used_micros": spent + reserved,
        "limit_micros": limit,
        "remaining_micros": max(0, limit - spent - reserved) if known else 0,
    }


def _days_in_month(day: date) -> int:
    next_month = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return (next_month - date(day.year, day.month, 1)).days
