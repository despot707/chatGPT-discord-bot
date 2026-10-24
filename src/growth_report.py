"""Offline pricing and unit-economics scenarios; never reads runtime ledgers."""

from __future__ import annotations

import argparse
import json
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any

from src.prepaid import COSTS, PRODUCTS

MICROS_PER_DOLLAR = Decimal(1_000_000)
GIB = 1024**3
SUBSCRIPTION_PLANS = {
    key: product for key, product in PRODUCTS.items() if product.kind == "subscription"
}


def _decimal(
    value: Any,
    name: str,
    *,
    minimum: Decimal = Decimal(0),
    maximum: Decimal | None = None,
) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name} must be a decimal number") from None
    if not result.is_finite() or result < minimum or (maximum is not None and result > maximum):
        bounds = f"at least {minimum}" + (f" and at most {maximum}" if maximum is not None else "")
        raise ValueError(f"{name} must be {bounds}")
    return result


def calculate(
    *,
    fee_percent: Decimal,
    refund_percent: Decimal,
    monthly_hosting: Decimal,
    utilizations: list[Decimal],
    candidate_prices: dict[str, Decimal] | None = None,
) -> dict:
    """Model outcomes for hypothetical paid periods using explicit assumptions."""
    fee_percent = _decimal(fee_percent, "fee-percent", maximum=Decimal(100))
    refund_percent = _decimal(refund_percent, "refund-percent", maximum=Decimal(100))
    monthly_hosting = _decimal(monthly_hosting, "monthly-hosting")
    utilizations = [_decimal(value, "utilization", maximum=Decimal(100)) for value in utilizations]
    if not utilizations:
        raise ValueError("at least one utilization scenario is required")

    candidate_prices = candidate_prices or {}
    unknown_plans = set(candidate_prices) - set(SUBSCRIPTION_PLANS)
    if unknown_plans:
        raise ValueError(f"unknown candidate plan: {sorted(unknown_plans)[0]}")
    prices = {
        key: _decimal(value, f"{key}-price", minimum=Decimal("0.000001"))
        for key, value in candidate_prices.items()
    }

    scenarios = []
    for key, product in SUBSCRIPTION_PLANS.items():
        price = prices.get(key, Decimal(product.price_cents) / 100)
        gross = price * MICROS_PER_DOLLAR
        fees = gross * fee_percent / 100
        refunds = gross * refund_percent / 100
        # Fees stay fully incurred even when a purchase is refunded.
        retained = gross - fees - refunds
        # Match validate_products integer-microdollar allocation rounding.
        infrastructure = (
            product.allowances.get("core", 0) * COSTS["core"]
            + product.storage_bytes * 450_000 // GIB
        )
        max_api = Decimal(product.max_api_cost)

        for utilization in utilizations:
            modeled_api = max_api * utilization / 100
            cost_reserve = Decimal(infrastructure) + modeled_api
            contribution = retained - cost_reserve
            if contribution <= 0:
                breakeven = None
            elif monthly_hosting == 0:
                breakeven = 0
            else:
                breakeven = int(
                    (monthly_hosting * MICROS_PER_DOLLAR / contribution).to_integral_value(
                        rounding=ROUND_CEILING
                    )
                )

            scenarios.append(
                {
                    "plan": key,
                    "price_usd_assumed": str(price),
                    "candidate_price_override": key in prices,
                    "utilization_percent": str(utilization),
                    "gross_receipts_usd_hypothetical": str(gross / MICROS_PER_DOLLAR),
                    "collected_cash_usd": None,
                    "collected_cash_status": "unknown; this is a scenario, not a purchase or payout",
                    "fees_usd_assumed": str(fees / MICROS_PER_DOLLAR),
                    "refunds_usd_assumed": str(refunds / MICROS_PER_DOLLAR),
                    "retained_proceeds_usd_modeled": str(retained / MICROS_PER_DOLLAR),
                    "api_cost_usd_modeled": str(modeled_api / MICROS_PER_DOLLAR),
                    "infrastructure_reserve_usd_modeled": str(
                        Decimal(infrastructure) / MICROS_PER_DOLLAR
                    ),
                    "hosting_usd_monthly_assumed": str(monthly_hosting),
                    "contribution_usd_per_period_modeled": str(contribution / MICROS_PER_DOLLAR),
                    "break_even_subscriptions_for_hosting": breakeven,
                }
            )

    return {
        "report": "offline pricing scenario; no financial ledger or live payment data read",
        "assumptions": {
            "fee_percent_assumed": str(fee_percent),
            "refund_percent_assumed": str(refund_percent),
            "monthly_hosting_usd_assumed": str(monthly_hosting),
            "refund_model": "refund percentage applied to gross; full original payment fee is retained as a cost",
        },
        "scenarios": scenarios,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fee-percent", required=True, help="Explicit hypothetical fee rate, 0 to 100"
    )
    parser.add_argument(
        "--refund-percent", required=True, help="Explicit hypothetical refund rate, 0 to 100"
    )
    parser.add_argument(
        "--monthly-hosting", required=True, help="Explicit monthly hosting cost in USD"
    )
    parser.add_argument(
        "--utilization",
        action="append",
        help="AI allowance utilization percent; repeat for scenarios (default: 10, 50, 100)",
    )
    for plan in SUBSCRIPTION_PLANS:
        parser.add_argument(
            f"--{plan}-price",
            help="Hypothetical new-customer price in USD; does not change current SKUs or sold access",
        )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        utilizations = args.utilization or ["10", "50", "100"]
        prices = {
            plan: getattr(args, f"{plan}_price")
            for plan in SUBSCRIPTION_PLANS
            if getattr(args, f"{plan}_price") is not None
        }
        report = calculate(
            fee_percent=_decimal(args.fee_percent, "fee-percent", maximum=Decimal(100)),
            refund_percent=_decimal(args.refund_percent, "refund-percent", maximum=Decimal(100)),
            monthly_hosting=_decimal(args.monthly_hosting, "monthly-hosting"),
            utilizations=utilizations,
            candidate_prices=prices,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        assumptions = report["assumptions"]
        print(report["report"])
        print(
            "Assumptions: fee={fee_percent_assumed}%, refunds={refund_percent_assumed}%, "
            "hosting=${monthly_hosting_usd_assumed}/month".format(**assumptions)
        )
        print(
            "Plan | price | use | modeled variable cost + reserve | "
            "modeled contribution | hosting break-even servers"
        )
        for row in report["scenarios"]:
            api_and_reserve = Decimal(row["api_cost_usd_modeled"]) + Decimal(
                row["infrastructure_reserve_usd_modeled"]
            )
            breakeven = row["break_even_subscriptions_for_hosting"]
            label = breakeven if breakeven is not None else "no positive contribution"
            print(
                f"{row['plan']} | ${row['price_usd_assumed']} | "
                f"{row['utilization_percent']}% | ${api_and_reserve:.6f} | "
                f"${Decimal(row['contribution_usd_per_period_modeled']):.6f} | {label}"
            )
        print(
            "Collected cash: unknown. Scenarios do not establish sales, payout, or profitability."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
