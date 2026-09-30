# Discord purchase reconciliation

The Discord adapter is an optional access-reconciliation layer for Sidecord Ai's prepaid plans. It does not create a checkout, verify a financial statement, or turn a Discord entitlement into proof of payment. Sales stay disabled (`site/site.config.json` uses `coming_soon`) until the external launch requirements are satisfied.

## Configuration

The defaults preserve the personal bot:

```dotenv
DISCORD_PURCHASE_MODE=off
DISCORD_APPLICATION_ID=1365724363722068120
DISCORD_SKU_MAP=
```

The application ID is Sidecord Ai's public Discord application ID. Obtain actual guild-subscription SKU IDs from the Developer Portal after Discord monetization setup. Never put example IDs in the live map, infer a product from a name or price, or treat a test SKU as a paid product. The map format is a comma-separated set of `SKU_ID:product` pairs; supported products are `basic`, `plus`, and `premium` only.

`off` disables purchase polling and keeps the personal bot path. `observe` requires a numeric application ID and nonempty SKU map, fetches Discord's SKU, entitlement, and subscription records, and records current access without granting credits. `enforce` performs the same complete REST reconciliation and may apply only reviewed paid-period settlements. It also requires `PREPAID_MODE=enforce`, a recent complete snapshot, and every existing approval and cost-evidence gate described in [prepaid plans](prepaid-plans.md). Leave approvals false until their evidence has been independently verified. Do not enable enforcement as a test or to try a guessed SKU.

The adapter accepts only mapped guild-subscription SKUs belonging to the configured application. Test entitlements, gifts, one-time/add-on SKUs, unsupported products, missing identity/period fields, and mismatched subscription records do not grant credit. Startup, entitlement/subscription events, and a 60-second timer trigger reconciliation. Each completed pass walks the REST records and replaces the current access snapshot; a failed or incomplete pass leaves the freshness gate expired. Enforce requires a matching complete snapshot at most five minutes old. When a current entitlement/subscription period no longer matches a credited settlement, the adapter revokes that adapter-owned grant, including when a renewal replaces the old period.

## Review each settlement before import

Discord access records establish application, SKU, guild, entitlement, subscription, and period identity. They do not prove invoice amounts, that an invoice is immutable or settled, refunds/chargebacks, or the exact net paid to the developer. No official financial export schema has been validated and no automatic financial verification is implemented. Keep invoice/source documents in a controlled private location; the import stores their SHA-256 hash and a source reference, not the file itself.

For every billing period, independently inspect the relevant financial evidence and confirm all of these facts before preparing an import:

- a unique paid receipt or transaction ID;
- exact entitlement ID, subscription ID, guild ID, SKU ID, and mapped product;
- exact UTC period start and end that match the current Discord subscription;
- gross and net amounts in USD, represented as integer microdollars (`1 USD = 1,000,000`); gross must equal the configured plan price and net must satisfy the code's 65% minimum;
- SHA-256 of the evidence file, a private evidence reference, and the responsible reviewer's name.

The 65% check is a minimum acceptance rule, not an assumed payout, an estimate of Discord's fee, or a guarantee of profit. Enter the actual net amount supported by the evidence. Do not manufacture missing fields or reuse a receipt for another guild, entitlement, product, or period.

Create a record JSON file with these exact fields. `starts` and `ends` are Unix timestamps in seconds; amounts are integer microdollars. Keep the record with the review notes, outside the public site and source control:

```json
{
  "receipt_id": "<verified unique invoice or transaction ID>",
  "entitlement_id": 123456789012345678,
  "subscription_id": 123456789012345678,
  "guild_id": 123456789012345678,
  "sku_id": 123456789012345678,
  "product": "basic",
  "starts": 1780000000,
  "ends": 1782592000,
  "gross_micros": 990000,
  "net_micros": 643500,
  "currency": "USD"
}
```

The values above are illustrative only and are not a real purchase, payout, entitlement, SKU, or invoice. Replace every value with independently verified evidence. The importer validates field consistency and records who reviewed the evidence; it cannot validate the external source or whether the review was correct.

After review, run the explicit operator import command from the repository root:

The operator commands read the process environment, including `DISCORD_APPLICATION_ID`, `DISCORD_SKU_MAP`, and `PREPAID_DATABASE_PATH`; they do not automatically load a `.env` file. Point them at the intended persistent ledger and set the same application/SKU mapping used by the bot.

```sh
python -m src.discord_purchase_import \
  --record-file /private/path/settlement.json \
  --evidence-file /private/path/invoice-or-settlement.pdf \
  --evidence-reference 'private-ledger/invoice-123' \
  --reviewed-by 'Operator name' \
  --confirm-paid-invoice
```

The confirmation switch is an intentional operator boundary. This command imports reviewed facts and hashes the evidence file; it does not contact Discord or the payment system. After import, the bot's authenticated REST reconciliation must find that exact active period before any credit is issued. Each renewal requires its own receipt and evidence. Do not assume the Discord entitlement API provides full financial state.

## Refunds and payment reversals

After independently reviewing a refund, chargeback, or payment reversal for a staged receipt, record it immediately:

```sh
python -m src.discord_purchase_revoke \
  --receipt-id '<the original reviewed receipt ID>' \
  --reason refund \
  --evidence-file /private/path/refund-evidence.pdf \
  --evidence-reference 'private-ledger/refund-123' \
  --reviewed-by 'Operator name' \
  --confirm-revocation
```

Allowed reasons are `refund`, `chargeback`, and `payment_reversal`. This command revokes unused access and permanently records that the receipt must not fund future grants, even if Discord still reports active access. It does not return money through Discord or Stripe and does not erase already incurred usage. Repeating identical evidence is idempotent; changing a recorded reversal's evidence is rejected. Keep the source records private. Resolve any erroneous reversal through an audited correction process, not by editing the database or reusing the receipt.

## Still required before sales

Local verification on September 30, 2026: 458 tests passed and 3 platform-specific tests skipped on Windows; all 26 purchase/import/revocation tests passed. Ruff lint/format, mypy, compilation, dependency consistency, and 68 static-site checks passed. These checks use fake Discord records and temporary databases; they do not prove live checkout, financial-export fields, or production activation.

Finish Discord's current developer verification and monetization onboarding, create and verify real guild-subscription SKUs, test with approved non-production purchases, establish private financial-evidence retention and refund/chargeback procedures, and verify current hosting/provider spending limits. Then complete the prepaid approval gates and review the public customer disclosures. Keep the site's sale status at `coming_soon` until those steps and a real checkout/customer support path are in place. There is no private HTTP dashboard for purchase evidence; do not upload receipts or settlement files to the public site.
