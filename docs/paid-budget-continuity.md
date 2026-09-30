# Paid quota continuity: accounting migration v1

This patch targets source revision `591b7b8a52d61e7bb05861f230fb334fd5e8c8da`.
It is a local implementation and test package, not deployment approval or evidence
of production/provider configuration changes.

## Admission and separation

With `PREPAID_MODE=enforce`, `DISCORD_PURCHASE_MODE=enforce`, and
`DISCORD_FUNDING_MODE=entitlement`, the authenticated paid gateway uses
`PaidBudgetLedger` (`paid-entitlement-v1`). `HARD_BUDGET_ENABLED=true` remains
required to enforce durable accounting. The original `BUDGET_*` values identify
the source ledger policy and must remain unchanged during migration. Do not set
`BUDGET_MONTHLY_USD=100`: the personal parser still rejects values above $10.

Every commercial request still needs current authenticated Discord purchase
reconciliation, an active plan, finite remaining feature units, an atomic maximum
cost reservation in the paid quota ledger, and a durable aggregate cash hold
before provider dispatch. The aggregate personal daily/monthly pool is no longer
an admission cutoff for these sold units. The 401st Basic chat is denied after its
400 included attempts. Search, optional web, reasoning, and images retain their
fixed contracts and cost maxima. Freshness and AI permission are checked again
after token counting and before dispatch. Unknown outcomes retain their holds;
usage contract violations and real overruns retain their locks.

Ordinary `BudgetLedger` and personal `HARD_BUDGET` behavior are unchanged. The
commercial legacy manager provides SDK-free Luna metadata for `/status`,
`/provider`, and model completion menus. Its direct completion/image methods
reject calls. This metadata-only behavior applies to both commercial funding
modes because both are wrapped by `PaidManager`; settlement-mode gateway funding
and admission rules remain unchanged.

No provider-native cap, purchase, payout, sales state, renewal, or payment method
is changed. Native limits remain independently configured and may still constrain
provider availability. The owner-funded entitlement path does not fabricate
settled revenue or derive receipts from catalogue prices.

## Migration and recovery

1. Stop the old writer and drain in-flight requests before upgrading. Preserve
   ambiguous/dispatched holds if drain cannot establish their outcome; never
   refund them merely to restart. Start the replacement in the existing
   single-writer Linux deployment, using the same prepaid DB, budget DB, and
   original immutable budget policy. Do not run multiple replicas or overlap old
   personal and new commercial writers. The paid lease protects cooperating paid
   writers; it cannot coordinate an older unrelated personal process.
2. Before the first mutation of an existing personal accounting DB, take a SQLite
   online backup under the paid process lease. The implementation creates a new
   unique `*.before-paid-v1-*.sqlite3`, verifies `PRAGMA integrity_check`, flushes
   the backup, and records its path and SHA-256 in the migration row. A failed
   backup blocks migration. Earlier backups are never overwritten.
3. A `BEGIN IMMEDIATE` transaction adds version-one migration evidence, an exact
   paid-ledger path binding, the accounting-mode marker, and new request-to-cash
   reservation links. It does not rewrite historical periods, charges, original
   reservations, opening baseline, activation month, or lock reasons.
4. Repeated starts validate the same source policy and ledger binding. Migration
   is idempotent and creates no repeated backup on successful restarts. Arbitrary
   policy changes, an unknown accounting mode, or mismatched migration evidence
   fail closed. A stale personal-ledger object cannot reserve from the migrated
   DB, and a new personal constructor rejects it.
5. Unknown opening baseline is not assumed known, including an unknown earlier
   period. Supply only an explicitly verified opening amount through the existing
   configuration. Never delete/recreate the DB, erase pending holds, fabricate a
   zero baseline, or restore a pre-migration backup over later legitimate usage.
   A rollback requires reconciling all post-backup charges and reservations first.

The two ledgers deliberately use conservative interruption semantics. A crash
between quota and cash reservation may leave a quota-only hold; after both holds
or dispatch, the maxima remain until an outcome is verified. They are not
transactionally unified across databases and are not auto-refunded on restart.

## Operator-only planning log

`Runtime.planning_report()` is an operational helper, not a Discord command.
The runtime logs an aggregate report at first authenticated readiness and at most
once per hour while handling further requests. It is not a background scheduler,
external notification, or guaranteed alert delivery. Restrict application-log
access to operators; no global account/customer totals are exposed through the
existing guild-admin `/budget` command.

The advisory $100 USD **monthly** planning baseline is compared with current-month
incurred costs + unresolved maximum holds + remaining active sold quota at its
per-unit maximum + explicitly configured monthly hosting liability. Configure
`PAID_HOSTING_LIABILITY_USD` from a reviewed hosting estimate; an absent value is
reported as unknown, leaving the combined projection incomplete. No estimate is
silently substituted. Lifetime accounting, reporting month/time, source labels,
and absence of verified settled receipts are reported separately.

Current-month incurred costs preserve the existing conservative late-settlement
charge semantics. Lifetime spend counts actual reservation settlement only once.
Historical prepaid/global pending rows have no proven cross-ledger identity;
the report conservatively sums them and explicitly reports possible overlap and
both unlinked amounts. New links deduplicate exactly. This is an upper-bound
projection, not an invoice or available cash balance. Crossing $100 emits warning
severity only; it never pauses service, purchases, or renewals.

For the financial comparison, set `PAID_FINANCE_EVIDENCE_JSON` to an explicitly
reviewed record for the report month, for example:

```json
{
  "period": "2026-09",
  "reviewed_at": 1790769600,
  "reviewed_by": "operator",
  "expected_net_micros": 20000000,
  "expected_net_source": "reviewed monthly revenue forecast",
  "verified_payout_cash_micros": 5000000,
  "verified_payout_source": "reviewed Stripe payout statement"
}
```

`reviewed_at` must be a Unix timestamp no later than report time and no more
than 32 days old. Replace the example amounts, month, and source descriptions
with reviewed evidence; a source is required for each supplied amount. The
report accepts an explicit zero only with its source. Missing, invalid, or
previous-month evidence yields null financial amounts and gaps. `operating_gap`
is projected cost less expected net; `cash_bridge_gap` is projected cost less
operator-verified payout cash. Both remain null when hosting or their respective
financial input is unknown. Positive gaps set `operating_shortfall` or
`cash_bridge_required` and raise the operator log to warning severity. The log
contains a SHA-256 digest of the reviewed JSON for provenance, but omits its
reviewer and source descriptions. The operator must review and update the
forecast and actual payout source for each month; a Discord entitlement alone
cannot supply either figure. The runtime does not authenticate a Stripe payout,
read payment methods, or infer revenue from Discord entitlements or catalogue
prices. The `$100` figure is an advisory monthly cost baseline, not cash deposited
or an admission cutoff.

## Integrated interface and shutdown checks

The integrated release updates customer copy that previously described personal
daily availability or the $10 shared commercial ceiling, including:

- `src/prepaid_client.py` plan footer
- `docs/prepaid-plans.md` owner envelope and shared-budget section
- `docs/commercial-launch.md` launch checklist and daily availability wording
- Customer-facing site source using the same availability wording

The integrated release also repairs `PaidWeb.close()` so the request-scoped
`WebService` can shut down without an absent-method error. The real Linux
startup/restart test closes the complete client without replacing that adapter.
Linux-only accounting tests explicitly skip on Windows and must pass in Linux CI;
Windows personal-mode support is unchanged.
