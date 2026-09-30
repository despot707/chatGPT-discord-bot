# Subscription cycle expiry corrections

An authenticated cycle is identified by application, subscription and period start.
The entitlement, guild, SKU and product must continue to match the issued grant.
Changing only `current_period_end` updates expiry in the existing access grant and
period evidence in one SQLite transaction. It does not issue another allowance.
A genuinely different period start retains the existing renewal behavior. This
change does not define upgrade, proration, grace or provider-budget policy.

New native receipt IDs omit the end timestamp. Existing end-suffixed IDs are
retained permanently, including after an expiry correction, because request rows
and reviewed payment reversals refer to them. Their suffix records the originally
issued end, not the current expiry. The current expiry is in the structured access
payload, `prepaid_grants.ends` and `discord_access_period.ends`. No request keys,
request state/costs, limits, storage allowance, revoked flags or reviewed cash
settlement/reversal records are reset or remapped. Revoked access is never restored.
No schema migration is required for a cycle with one consistent existing period
row. Incomplete or inconsistent historical period/grant identity also fails
closed for audited repair; it cannot be replaced by a new receipt. Refund
receipts keep their last recorded expiry because they are not reactivated.

## Pre-existing duplicate cycles require an audited migration

Older code may already have created multiple end-suffixed receipts for the same
subscription/start. Reconciliation deliberately fails closed for an affected cycle
with `Duplicate Discord access cycles need audited accounting migration.` All
existing period, grant, request and financial evidence rows remain unchanged;
the snapshot is invalidated so paid admission cannot rely on it.

There is no automatic consolidation or balance-reset path in this patch. Do not
remove one duplicate, select the receipt with most remaining quota, clear usage,
unrevoke a receipt, or recreate the accounting database to bypass this denial.

Before any separately authorized recovery:

1. Pause paid admission and take a consistent backup of the complete accounting
   database. Keep the original backup and its hash as immutable audit evidence.
2. Identify every receipt for each application/subscription/start and the linked
   entitlement, guild, SKU/product, limits, current expiry, revocations, reversal
   records, and all request states, reserved costs and actual costs. Also inspect
   `(entitlement_id, starts)` collisions; do not assume changed identity is valid.
3. Reconcile consumed and held allowance across **all** duplicate receipts against
   the single originally issued allowance. Do not let discarded receipt usage,
   refunds, or existing revocations become spendable credit.
4. Have an operator explicitly review the proposed receipt-key mapping and usage,
   financial-evidence and revocation preservation. This patch intentionally ships
   no generic migration command: inconsistent identities or revocation reasons
   need an evidence-backed, separately reviewed recovery plan.
5. Test that plan on a copy, recording exact before/after rows and proving request
   keys resolve, all costs remain accounted, no new allowance appears, and
   reversal tombstones stay effective. Only apply an authorized recovery in one
   transaction; restore admission after fresh authenticated reconciliation.

Rows for a completed historical cycle are not rewritten when a later genuine
cycle starts. Retain them for the accounting audit as well.
