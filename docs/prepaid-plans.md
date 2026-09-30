# Prepared plans and cost controls

**Status: implemented preparation branch; AI purchases are not enabled. No approved SKUs, live checkout, or supported add-ons are configured.**
Prices and finite AI allowances below are unchanged planning values in USD per server, excluding any separately collected taxes. They are not offers for sale. No purchase can be made and no automatic charge occurs. Review the owner-facing boundaries in [commercial-launch.md](commercial-launch.md) before launch.

## Free tier and paid boundary

The service has an ongoing free tier; it is not a trial and does not include free AI credits. Profiles and code-only game, party, and team features are free and are not measured against the paid AI allowances. The combined free profile and game-record baseline is 1 MiB per server. Paid storage quotas in the plan table are additive and apply to paid AI/chat data and optional capacity above the free baseline. No free operation quota is advertised.

The lowest paid plan begins AI access. Paid AI features are chat, advanced reasoning, web search, and image generation, with finite per-period allowances. Basic has chat only; Plus and Premium include the additional AI types shown below. Exhaustion pauses the applicable AI feature until a future eligible period or a supported purchase becomes available. There are no overages, automatic refills, rollover, trials, or automatically charged purchases. AI purchases are not currently enabled.

## Included allowances

| Plan | Monthly price | Chat attempts | Reasoning attempts | Web searches | Image attempts | Server operations | Saved data |
|---|---:|---:|---:|---:|---:|---:|---:|
| Basic | $0.99 | 100 | 0 | 0 | 0 | 1000 | 1 MiB |
| Plus | $4.99 | 500 | 50 | 10 | 3 | 5000 | 20 MiB |
| Premium | $9.99 | 1000 | 100 | 20 | 10 | 10000 | 100 MiB |

## Unsupported add-ons (internal planning only; not for sale)

These proposed items have no approved SKUs or supported checkout and must not appear as purchasable offers. Keep their values here only for internal cost/control planning.

| Add-on | One-time price | Additional allowance |
|---|---:|---|
| Extra chat | $0.99 | 200 chat, 200 core |
| Extra reasoning | $0.99 | 100 reasoning, 100 core |
| Web search pack | $1.99 | 15 search, 30 core |
| Image pack | $2.99 | 12 images, 12 core |
| Storage boost (100 MiB, 30 days) | $0.99 | 100 MiB storage |

Add-ons require an active base subscription, are bound to one selected server, and expire after at most 30 days. They never auto-renew or auto-refill. Subscription periods follow verified paid invoices, not a local calendar-reset job. Unused included units do not roll over. No free trial or discount may mint unfunded usage. No mid-cycle prorated upgrade is supported until its net-funded allowance is separately defined.

## Meaning of an allowance

- Chat: one dispatched model attempt, at most 8,000 counted input tokens including instructions/history/images and 500 total output tokens. Normal reasoning is off. A provider refusal/timeout after dispatch may consume one attempt; pre-dispatch validation failure restores its AI unit.
- Advanced reasoning: one attempt, the same 8,000 input-token ceiling and 2,000 total output tokens INCLUDING invisible reasoning. No guarantee of answer correctness.
- Web search: one explicit request with at most one native search tool call, not unlimited research. Uses gpt-4.1-mini because its non-preview search content is billed in a fixed 8,000-token block. Includes the summary; does not also consume ordinary chat. Search and reasoning are separate calls, not a hidden combined job.
- Image: one fixed GPT Image 2 snapshot, 1024x1024, medium quality, JPEG; at most 2,000 UTF-8 prompt bytes; no edits, additional variants, auto size/quality, or partial streaming. Image activation still requires availability/price/usage-field verification on the actual account.
- Server operations: request-slot admissions, game/player/party/team commands and profile writes. Some compound flows use more than one operation. Core tools do not call an LLM unless explicitly requested. Privacy inspection/deletion and plan help are not paid; they are rate-limited to prevent abuse.
- Paid saved data: the plan-table allowance adds to the separate 1 MiB per-server free baseline and can hold bot conversation turns and optional profile/game records above that baseline, plus 256 bytes/row for metadata. It is not a count of inactive Discord members. Shared game catalog and branding are operator infrastructure, not charged to every server. No file uploads or indefinite image hosting are included.
- Direct public-link summaries: ordinary chat plus an admitted server operation, one existing bounded page fetch. They are not unlimited browsing and do not invoke an additional paid search engine. Voice is unsupported and is not included or promised. Third-party paid search, code execution and new unmetered tools are disabled in commercial mode.

## Economics validated in code

Each accepted full-price receipt must provide at least 65% of its listed gross price in verifiable net revenue. Reserve up to 35% gross for payment/platform fees, at most 35% gross for AI, and 10% gross for hosting/operations; at least 20% gross is left before tax, refunds, chargebacks, labor and other business overhead. These are conservative allocations, NOT a guarantee of overall profit. Revenue from zero or very few customers cannot automatically cover fixed infrastructure.

| Product | Gross | Max AI liability | Hosting allocation | Remainder with 35% fee reserve |
|---|---:|---:|---:|---:|
| Basic | $0.99 | $0.1500 | $0.0990 | $0.3945 |
| Plus | $4.99 | $1.5400 | $0.4990 | $1.2045 |
| Premium | $9.99 | $3.4000 | $0.9990 | $2.0945 |
| Extra chat | $0.99 | $0.3000 | $0.0990 | $0.2445 |
| Extra reasoning | $0.99 | $0.3000 | $0.0990 | $0.2445 |
| Web search pack | $1.99 | $0.6000 | $0.1990 | $0.4945 |
| Image pack | $2.99 | $0.9600 | $0.2990 | $0.6845 |
| Storage boost (100 MiB, 30 days) | $0.99 | $0.0000 | $0.0990 | $0.5445 |

The per-attempt maximum holds are $0.0015 chat, $0.003 reasoning, $0.04 search, and $0.08 image. A core operation reserves $0.00005 of the infrastructure allocation, not measured Railway CPU. All money arithmetic in the ledger is integer microdollars; UI dollar formatting is display-only.

## Enforcement and failure behavior

BEGIN IMMEDIATE transactions reserve allowance before dispatch. Parallel requests cannot spend the same unit. Once dispatched, a timeout/cancellation retains its maximum reservation, with no automatic retry or fallback. Missing/unexpected usage or a broken price contract freezes paid features instead of assuming zero cost. Monthly renewals need distinct verified invoice IDs. Replaying the same receipt is idempotent; moving it to another server or changing its terms is rejected. Test entitlements, gifts, unsupported currency and unfunded discounts are not accepted as paid receipts. Every receipt also has a money ceiling independent of its unit count.

Paid deployments are single-writer Linux processes with an exclusive file lease, at most two generation requests at once, capped input/output/context, per-user cooldowns, bounded UI/autocomplete frequency and finite ledger/database sizes. No API key is exposed or changed. Writes reserve aggregate per-server data bytes before commit. Failed commits over-reserve rather than hide usage. Cleanup reconciles actual live records.

After a storage downgrade or subscription expiry, new growth beyond the paid limit is rejected immediately. A persisted 48-hour grace period allows export/cleanup. Paid chat history may be removed to fit the remaining paid allowance. Profile and game records are never deleted solely because paid storage expires; if they exceed the free 1 MiB baseline, they remain available read-only until the records fit the free baseline or paid capacity returns. This must be disclosed and approved before launch, with migration and backup-retention testing. The maintenance worker runs hourly only in explicitly approved enforce mode. It never executes in preview/off mode. Native backups are a separate operator obligation.

## Discord subscription reconciliation

The optional Discord adapter observes authenticated guild-subscription entitlements and subscription periods. `DISCORD_PURCHASE_MODE=off` is the default and preserves the personal bot. `observe` records current access but never funds prepaid credits. `enforce` may credit only an exact period backed by a separately imported, operator-reviewed settlement record. Paid operation additionally requires `PREPAID_MODE=enforce`, a complete Discord snapshot no older than five minutes, and every existing prepaid approval/evidence gate. Keep all approval flags false until independently verified.

Only explicitly mapped guild subscription SKUs for Basic, Plus, or Premium are supported. Test entitlements, gifts, add-on SKUs, unknown products, and client-supplied claims do not fund credits. Renewals need a distinct reviewed receipt for the new exact subscription period. Entitlement/subscription create, update, and delete events request a fresh REST reconciliation; the client also reconciles at startup and at least once per minute. A failed/incomplete walk cannot refresh the current-snapshot gate. If a previously credited period no longer matches current access, its adapter-owned grant is revoked. This is access reconciliation, not proof of the amount Discord paid.

The import utility records a SHA-256 hash and reference for externally inspected invoice evidence; it does not fetch or verify a financial export. There is no validated official export schema or automatic financial verification. An operator must independently inspect the invoice/settlement, confirm exact gross and net USD, receipt identity, guild, entitlement, subscription, mapped SKU/product and period, then explicitly confirm and import it. Do not assume a 65% net payout: the importer checks the configured minimum against independently entered net evidence. See [Discord purchase operations](discord-purchases.md) for the exact setup and import steps.

## Launch gate and what remains outside this code

`AI_ACCESS_MODE=disabled` is a separate launch pause that blocks all provider initialization and AI dispatch, including the commercial gateway. It does not grant a subscription or enable checkout. Production uses this setting while free profiles and code-only gaming remain available. Paid activation requires explicitly lifting this pause together with enforce mode and all reviewed purchase gates.

`PREPAID_MODE=off` preserves the existing personal bot. `preview` also preserves existing paid API behavior and only shows draft plans; IT IS NOT SPENDING PROTECTION. `enforce` enables the commercial checks. Missing, unapproved or older-than-seven-days operator evidence blocks paid activity. Native account limits are NOT set or proven by an evidence JSON file.

Before enforce mode, complete Discord verification/monetization approvals, configure real reviewed guild-subscription SKUs, and prepare the independent invoice evidence process described above. Discord entitlements establish access and period identity; they do not establish invoice amounts, immutable invoice status, refunds, or net revenue. There is no automatic financial export importer or live checkout in this release. `Ledger.credit(Payment)` is an internal accounting boundary, not payment authentication.

Use a dedicated Railway project/workspace and dedicated OpenAI project/key for commercial traffic, one replica/process, and verify CPU/RAM/volume ceilings and native hard caps. Railway compute and Agent caps are separate. Set actual caps in the native UI/CLI; do not confuse plan hardware maxima, alerts, or defaults with configured monetary ceilings. Do not change unrelated workspace workloads.

Example operator commands (not run by this preparation):
```sh
railway usage limit status --json
railway usage projects --project <commercial-project> --period current --json
railway usage limit set --target workspace --soft 5 --hard 10
railway usage limit set --target agent --hard 0
```
Configure and enforce an OpenAI project hard-spend limit separately. API enforcement may lag; the application reservations are the first line of defense, not provider dashboard alerts. Account for existing month spend, meter lag, retries outside this application, minimum subscription charges, backups, residual idle storage and any unrelated project usage. No runtime uses Railway Agent for customer conversations.

The activation gate requires prepaid infrastructure allocation plus explicitly budgeted operator startup funds to cover the greater of documented hosting liability or 110% of the configured compute+Agent caps. For a $10 compute cap and $0 Agent cap, that is $11. With no operator funds and only Basic subscriptions, the conservative 10% allocation reaches $11 at 112 active Basic customers. This is a safety funding threshold, NOT a forecast or a claim that hosting actually costs $11.

Validate native caps and metering with screenshots/API evidence, current invoices and a restore/delete exercise; record no secrets in the repository. Verify provider contracts and run actual low-cost account smoke tests before marking review flags true. The current preparation has only offline/mocked API tests; it does not spend the user’s production OpenAI balance on testing.

## Primary pricing/control references (checked for this preparation)

- OpenAI model/pricing: https://developers.openai.com/api/docs/models/gpt-6-luna and https://developers.openai.com/api/docs/pricing
- Search fixed-block pricing: https://developers.openai.com/api/docs/pricing#tools
- Image fixed size/quality cost: https://developers.openai.com/api/docs/guides/image-generation#calculating-costs
- OpenAI native hard-limit latency: https://developers.openai.com/api/docs/guides/spend-limits
- Railway costs: https://docs.railway.com/pricing
- Railway separate Compute/Agent caps: https://docs.railway.com/pricing/cost-control and https://docs.railway.com/cli/usage
- Discord Premium Apps fees (6% processing, then 15% Growth or 30% Standard platform fee, plus applicable extra fees): https://support.discord.com/hc/en-us/articles/5330075836311-Monetization-Terms

No new paid offering should be sold by editing a plan price alone. Add its API/resource contract, allowance reservation, actual-usage reconciliation, privacy lifecycle, labels, economics tests and integration tests first.
