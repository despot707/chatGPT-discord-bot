# Offline pricing scenarios

`python -m src.growth_report --fee-percent 0 --refund-percent 0 --monthly-hosting 0` prints an offline scenario table. Set fee, refund, and monthly hosting assumptions explicitly; the example zeroes are command syntax examples, not estimates. Repeat `--utilization` to compare allowance usage levels. Use `--format json` for machine-readable output.

The model reads subscription `PRODUCTS` and per-attempt `COSTS` from `src/prepaid.py` without constructing a runtime, opening a database, or contacting a provider. API cost scales with the explicitly selected utilization. Core-operation and saved-data amounts are modeled reserves using the same integer rounding as product validation; they are not measured spend. The separately entered hosting assumption may overlap those reserves, so the report does not claim a measured margin. Fees are calculated on gross price and are retained as a cost in full; hypothetical refunds reduce gross proceeds separately, without fee recovery. Monthly hosting is divided by positive modeled contribution and rounded up to estimate the number of same-plan subscriptions needed to cover hosting at that scenario. Non-positive contribution has no positive break-even count, including when hosting is zero.

The report is a planning model, not measured cost, accounting, revenue, or a profit claim. Every row marks collected cash unknown because local product and allowance definitions cannot establish a purchase or payout. The tool excludes add-ons. Optional `--basic-price`, `--plus-price`, and `--premium-price` values are candidate prices for new-customer analysis only. They do not change the published catalog, Discord SKUs, renewals, or sold entitlement terms. No fee or refund rate is supplied by default.

## Buyer-facing plan copy

`/plans` leads with the free `/games` entry point and short descriptions of each plan's use case, followed by the existing exact allowances and native Discord purchase buttons. Prices remain sourced from `PRODUCTS`; the copy makes no popularity or best-value claim. `/settings usage` clarifies that subscriptions renew through Discord unless cancelled and that usage has no overages.
