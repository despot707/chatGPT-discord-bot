# Discord purchase reconciliation

Native Discord subscriptions establish service access. They do not establish the developer's payout or net revenue. The bot authenticates Discord API records and grants one bounded allowance for each current subscription period without waiting for a manually imported invoice.

## Native production configuration

```dotenv
PREPAID_MODE=enforce
DISCORD_PURCHASE_MODE=enforce
DISCORD_FUNDING_MODE=entitlement
DISCORD_APPLICATION_ID=1365724363722068120
DISCORD_SKU_MAP=1554920142532513832:basic,1554920641088593990:plus,1554920977488551936:premium
PREPAID_DATABASE_PATH=/app/data/prepaid.sqlite3
HARD_BUDGET_ENABLED=true
BUDGET_DATABASE_PATH=/app/data/budget.sqlite3
BUDGET_MONTHLY_USD=10
BUDGET_LUNA_MONTHLY_USD=7
BUDGET_TIMEZONE=America/Los_Angeles
```

Use the actual previously initialized global budget. An opening amount is a one-time reconciliation input, never a reason to recreate or reset a ledger. The deployment record documents activation and publication separately.

| Product | SKU ID | Price / month |
|---|---:|---:|
| Basic | `1554920142532513832` | $1.99 |
| Plus | `1554920641088593990` | $4.99 |
| Premium | `1554920977488551936` | $9.99 |

The public [Discord storefront](https://discord.com/application-directory/1365724363722068120/store) uses guild subscriptions: one purchase covers the chosen server. Publish each SKU with Store & API visibility and add it in Manage Store. Publishing a SKU and displaying it in a storefront are separate steps.

The adapter accepts only mapped guild-subscription SKUs belonging to this app, application-subscription entitlements, and matching current subscription periods. Null entitlement end dates are normal for ongoing subscriptions. Test entitlements, gifts, unknown types, wrong app/SKU/guild identities, and missing or conflicting period records do not grant access. Startup, relevant gateway events, and a 60-second timer refresh the complete snapshot. Failed reconciliation blocks paid requests once freshness expires.

Each grant key binds app, entitlement, subscription, and period. Replays never refill quota. A renewal grants the new period once. Expiry, deletion, disappearance, or replacement revokes old access; cancellation preserves the remaining authenticated period. A team-owner discount can establish access but records no revenue. Native grants never increase `infrastructure_funding()`.

## Legacy settlement mode

The following commands apply only to `DISCORD_FUNDING_MODE=settlement`. This separate accounting mode requires actual reviewed financial evidence. It is not a requirement for native Discord access and must not be populated with guessed invoices or assumed net revenue.

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
  "sku_id": 1554920142532513832,
  "product": "basic",
  "starts": 1780000000,
  "ends": 1782592000,
  "gross_micros": 1990000,
  "net_micros": 1293500,
  "currency": "USD"
}
```

The amounts above show the $1.99 Basic gross and exactly the 65% minimum accepted net; they are illustrative only, not an actual purchase or payout. The SKU ID is the draft Basic SKU, which remains unpublished; the remaining identity/period values are placeholders, not real settlement evidence. Replace every value with independently verified evidence. The importer validates field consistency and records who reviewed the evidence; it cannot validate the external source or whether the review was correct.

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
