from decimal import Decimal

import pytest
from src.growth_report import calculate, main
from src.prepaid import COSTS, PRODUCTS


def make(**overrides):
    params = dict(
        fee_percent=Decimal(0),
        refund_percent=Decimal(0),
        monthly_hosting=Decimal(0),
        utilizations=[Decimal(100)],
    )
    params.update(overrides)
    return calculate(**params)


def test_current_price_full_usage_uses_product_and_cost_constants():
    rows = make()["scenarios"]
    basic = next(row for row in rows if row["plan"] == "basic")
    infra = Decimal(PRODUCTS["basic"].allowances["core"] * COSTS["core"])
    assert Decimal(basic["price_usd_assumed"]) == Decimal("1.99")
    assert (
        Decimal(basic["api_cost_usd_modeled"])
        == Decimal(PRODUCTS["basic"].max_api_cost) / 1_000_000
    )
    assert Decimal(basic["infrastructure_reserve_usd_modeled"]) >= infra / 1_000_000
    assert basic["collected_cash_usd"] is None
    assert basic["break_even_subscriptions_for_hosting"] == 0


def test_candidate_price_is_scenario_only_and_hosting_break_even_rounds_up():
    report = make(
        monthly_hosting=Decimal("3"),
        utilizations=[Decimal(0)],
        candidate_prices={"basic": Decimal("10")},
    )
    row = next(row for row in report["scenarios"] if row["plan"] == "basic")
    assert row["candidate_price_override"] is True
    assert row["price_usd_assumed"] == "10"
    assert row["break_even_subscriptions_for_hosting"] == 1


def test_refund_keeps_full_original_fee_as_a_cost():
    report = make(
        candidate_prices={"basic": Decimal("10")},
        fee_percent=Decimal(10),
        refund_percent=Decimal(20),
        utilizations=[Decimal(0)],
    )
    row = next(row for row in report["scenarios"] if row["plan"] == "basic")
    assert Decimal(row["retained_proceeds_usd_modeled"]) == Decimal("7")
    assert Decimal(row["fees_usd_assumed"]) == Decimal("1")
    assert Decimal(row["refunds_usd_assumed"]) == Decimal("2")


def test_zero_or_negative_contribution_has_no_hosting_breakeven():
    zero = make(fee_percent=Decimal(100), monthly_hosting=Decimal(1))["scenarios"][0]
    negative = make(
        fee_percent=Decimal(0),
        monthly_hosting=Decimal(1),
        candidate_prices={"basic": Decimal("0.01")},
    )["scenarios"][0]
    assert Decimal(zero["contribution_usd_per_period_modeled"]) <= 0
    assert zero["break_even_subscriptions_for_hosting"] is None
    assert negative["break_even_subscriptions_for_hosting"] is None
    no_hosting = make(fee_percent=Decimal(100), monthly_hosting=Decimal(0))["scenarios"][0]
    assert no_hosting["break_even_subscriptions_for_hosting"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"fee_percent": Decimal("NaN")},
        {"refund_percent": Decimal(101)},
        {"monthly_hosting": Decimal(-1)},
        {"utilizations": [Decimal("Infinity")]},
        {"utilizations": [Decimal(101)]},
        {"utilizations": []},
        {"candidate_prices": {"chat_pack": Decimal(1)}},
        {"candidate_prices": {"basic": Decimal("NaN")}},
        {"candidate_prices": {"plus": Decimal(-1)}},
    ],
)
def test_public_calculator_rejects_invalid_scenario_inputs(overrides):
    params = dict(
        fee_percent=Decimal(0),
        refund_percent=Decimal(0),
        monthly_hosting=Decimal(0),
        utilizations=[Decimal(0)],
    )
    params.update(overrides)
    with pytest.raises(ValueError):
        calculate(**params)


def test_unknown_hosting_is_not_silently_filled():
    with pytest.raises(TypeError):
        calculate(fee_percent=Decimal(0), refund_percent=Decimal(0), utilizations=[Decimal(0)])


def test_invalid_rate_is_rejected():
    with pytest.raises(SystemExit) as error:
        main(["--fee-percent", "101", "--refund-percent", "0", "--monthly-hosting", "5"])
    assert error.value.code == 2


def test_report_cli_json_marks_cash_unknown(capsys):
    assert (
        main(
            [
                "--fee-percent",
                "5",
                "--refund-percent",
                "1",
                "--monthly-hosting",
                "20",
                "--utilization",
                "25",
                "--format",
                "json",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"collected_cash_usd": null' in output
    assert '"fee_percent_assumed": "5"' in output
