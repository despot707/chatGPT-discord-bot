"""Operator financial evidence stays separate from paid admission and payout claims."""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_discord_purchases import GUILD, NOW
from tests.test_paid_budget_continuity import setup_paid

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Paid accounting uses the Linux single-writer lease."
)


def evidence(period: str, **changes: object) -> str:
    payload = {
        "period": period,
        "reviewed_at": NOW,
        "reviewed_by": "owner",
        "expected_net_micros": 20_000_000,
        "expected_net_source": "reviewed monthly forecast",
        "verified_payout_cash_micros": 5_000_000,
        "verified_payout_source": "reviewed Stripe payout statement",
    }
    payload.update(changes)
    return json.dumps(payload)


async def test_finance_report_distinguishes_estimate_from_verified_payout(tmp_path, monkeypatch):
    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    period = datetime.fromtimestamp(NOW, timezone.utc).strftime("%Y-%m")
    monkeypatch.setenv("PAID_FINANCE_EVIDENCE_JSON", evidence(period))

    report = rt.planning_report(hosting_micros=1_000_000)
    assert report["planned_micros"] == 11_600_000
    assert report["expected_net_micros"] == 20_000_000
    assert report["verified_payout_cash_micros"] == 5_000_000
    assert report["operating_gap_micros"] == -8_400_000
    assert report["cash_bridge_gap_micros"] == 6_600_000
    assert report["operating_shortfall"] is False
    assert report["cash_bridge_required"] is True
    assert report["finance_evidence_status"] == "current"
    assert len(report["finance_evidence_sha256"]) == 64
    assert report["financial_complete"] is True
    assert report["warning"] is False
    assert report["admission_paused"] is False
    assert report["settled_receipts_micros"] is None


async def test_missing_stale_or_unreviewed_finance_never_becomes_cash(tmp_path, monkeypatch):
    rt, gateway, _ = await setup_paid(tmp_path, monkeypatch)
    period = datetime.fromtimestamp(NOW, timezone.utc).strftime("%Y-%m")

    missing = rt.planning_report(hosting_micros=0)
    assert missing["finance_evidence_status"] == "missing"
    assert missing["expected_net_micros"] is None
    assert missing["verified_payout_cash_micros"] is None
    assert missing["operating_gap_micros"] is None
    assert missing["cash_bridge_gap_micros"] is None
    assert missing["operating_shortfall"] is None
    assert missing["cash_bridge_required"] is None

    previous = (datetime.fromtimestamp(NOW, timezone.utc) - timedelta(days=32)).strftime("%Y-%m")
    monkeypatch.setenv("PAID_FINANCE_EVIDENCE_JSON", evidence(previous))
    stale = rt.planning_report(hosting_micros=0)
    assert stale["finance_evidence_status"] == "stale_period"
    assert stale["financial_complete"] is False
    assert stale["expected_net_micros"] is None

    monkeypatch.setenv("PAID_FINANCE_EVIDENCE_JSON", evidence(period, expected_net_source=None))
    invalid = rt.planning_report(hosting_micros=0)
    assert invalid["finance_evidence_status"] == "invalid"
    assert invalid["verified_payout_cash_micros"] is None

    # Bad advisory input cannot revoke an already admitted subscription.
    assert await gateway.complete(GUILD, 777, [{"role": "user", "content": "hello"}]) == "answer"


async def test_partial_finance_or_unknown_hosting_keeps_gaps_nullable(tmp_path, monkeypatch):
    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    period = datetime.fromtimestamp(NOW, timezone.utc).strftime("%Y-%m")
    monkeypatch.setenv(
        "PAID_FINANCE_EVIDENCE_JSON",
        evidence(period, verified_payout_cash_micros=None, verified_payout_source=None),
    )
    partial = rt.planning_report(hosting_micros=0)
    assert partial["operating_gap_micros"] == partial["planned_micros"] - 20_000_000
    assert partial["cash_bridge_gap_micros"] is None
    assert partial["missing_finance_fields"] == ["verified_payout_cash_micros"]
    assert partial["financial_complete"] is False

    no_hosting = rt.planning_report()
    assert no_hosting["expected_net_micros"] == 20_000_000
    assert no_hosting["planned_micros"] is None
    assert no_hosting["operating_gap_micros"] is None


async def test_sourced_zero_cash_is_distinct_from_unknown(tmp_path, monkeypatch):
    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    period = datetime.fromtimestamp(NOW, timezone.utc).strftime("%Y-%m")
    monkeypatch.setenv(
        "PAID_FINANCE_EVIDENCE_JSON",
        evidence(period, verified_payout_cash_micros=0),
    )
    report = rt.planning_report(hosting_micros=0)
    assert report["verified_payout_cash_micros"] == 0
    assert report["cash_bridge_gap_micros"] == report["planned_micros"]


async def test_projected_shortfall_warns_without_logging_finance_source(
    tmp_path, monkeypatch, caplog
):
    rt, _, _ = await setup_paid(tmp_path, monkeypatch)
    period = datetime.fromtimestamp(NOW, timezone.utc).strftime("%Y-%m")
    monkeypatch.setenv(
        "PAID_FINANCE_EVIDENCE_JSON",
        evidence(
            period,
            expected_net_micros=1_000_000,
            verified_payout_cash_micros=0,
            expected_net_source="private forecast worksheet",
            verified_payout_source="private Stripe statement",
        ),
    )
    monkeypatch.setenv("PAID_HOSTING_LIABILITY_USD", "1")
    rt._last_planning_log = float("-inf")
    with caplog.at_level(logging.WARNING):
        rt._log_planning_report()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert '"operating_shortfall": true' in caplog.records[0].message
    assert "private forecast worksheet" not in caplog.records[0].message
    assert "private Stripe statement" not in caplog.records[0].message
