# Plans and cost controls

Monthly USD guild-subscription SKUs: Basic (`1554920142532513832`), Plus (`1554920641088593990`), and Premium (`1554920977488551936`). The production release record in [commercial-launch.md](commercial-launch.md) tracks publication and verification separately from these source definitions. The public site's `saleStatus` controls whether it shows a preview or Discord checkout links. No add-ons are sold.
Prices below are USD per server, excluding separately collected taxes. Discord handles checkout, subscription renewal, and cancellation. A subscription renews monthly unless cancelled; there are no usage overage charges or automatic credit refills.

## Free tier and paid boundary

The service has an ongoing free tier; it is not a trial and does not include free AI credits. Profiles and code-only game, party, and team features are free and are not measured against the paid AI allowances. The combined free profile and game-record baseline is 1 MiB per server. Paid storage quotas in the plan table are additive and apply to paid AI/chat data and optional capacity above the free baseline. No free operation quota is advertised.

The lowest paid plan begins AI access. Basic includes chat; Plus and Premium include the additional AI types shown below. Allowances are shared by one server for each authenticated Discord billing period. A verified renewal grants the next period's allowance once. When an allowance runs out, that feature pauses until the next verified paid period. Discord renews a subscription automatically unless cancelled; cancellation stops the next renewal, while access and remaining allowance continue through the current paid period. There are no usage overages, automatic refills, rollover, or free AI trials.

## Included allowances

| Plan | Monthly price | Chat attempts | Reasoning attempts | Web searches | Image attempts | Server operations | Saved data |
|---|---:|---:|---:|---:|---:|---:|---:|
| Basic | $1.99 | 400 | 0 | 0 | 0 | 1000 | 1 MiB |
| Plus | $4.99 | 500 | 50 | 10 | 3 | 5000 | 20 MiB |
| Premium | $9.99 | 1000 | 100 | 20 | 10 | 10000 | 100 MiB |

## Unsupported add-ons (internal planning only; not for sale)

These proposed items have no SKUs or supported checkout and must not appear as purchasable offers. Keep their values here only for internal cost/control planning.

| Add-on | One-time price | Additional allowance |
|---|---:|---|
| Extra chat | $0.99 | 200 chat, 200 core |
| Extra reasoning | $0.99 | 100 reasoning, 100 core |
| Web search pack | $1.99 | 15 search, 30 core |
| Image pack | $2.99 | 12 images, 12 core |
| Storage boost (100 MiB, 30 days) | $0.99 | 100 MiB storage |

These internal add-on proposals are not connected to checkout. Native subscriptions follow Discord's authenticated current period, not a local calendar-reset job. Unused included units do not roll over. Discounted team-owner subscriptions can establish access but never create recorded revenue. Native entitlement access and financial settlement records remain separate.

## Meaning of an allowance

- Chat: one dispatched model attempt, at most 8,000 counted input tokens including instructions/history/images and 500 total output tokens. Normal reasoning is off. A provider refusal/timeout after dispatch may consume one attempt; pre-dispatch validation failure restores its AI unit.
- Advanced reasoning: one attempt, the same 8,000 input-token ceiling and 2,000 total output tokens INCLUDING invisible reasoning. No guarantee of answer correctness.
- Web search: Luna can search when needed, or on an explicit search request, using at most one native search tool call. A reply that actually searches consumes one search allowance and includes its summary; a normal reply consumes only chat. If optional search is unavailable, normal chat remains available and must not claim live verification. Search and explicit advanced reasoning are separate bounded calls. The documented 128k web context plus the 8k initial prompt fits the $0.04 reservation; searched replies conservatively settle that full amount because hosted search-content usage is not independently itemized.
- Image: one `gpt-image-2.5-flare-2026-09-08` image, 1024x1024, medium quality, JPEG; at most 2,000 UTF-8 prompt bytes. No edits, additional variants, auto size/quality, or partial streaming. The September 30 official calculator estimates 439 output tokens ($0.01317) plus text input; the $0.08 hold includes headroom. Actual usage must be present and within the reservation.
- Server operations: request-slot admissions, game/player/party/team commands and profile writes. Some compound flows use more than one operation. Core tools do not call an LLM unless explicitly requested. Privacy inspection/deletion and plan help are not paid; they are rate-limited to prevent abuse.
- Paid saved data: the plan-table allowance adds to the separate 1 MiB per-server free baseline and can hold bot conversation turns and optional profile/game records above that baseline, plus 256 bytes/row for metadata. It is not a count of inactive Discord members. Shared game catalog and branding are operator infrastructure, not charged to every server. No file uploads or indefinite image hosting are included.
- Direct public-link summaries: ordinary chat plus an admitted server operation, one existing bounded page fetch. They are not unlimited browsing and do not invoke an additional paid search engine. Voice is unsupported and is not included or promised. Third-party paid search, code execution and new unmetered tools are disabled in commercial mode.

## Economics validated in code

The planning model reserves 35% of listed gross for payment/platform fees, at most 35% for AI, and 10% for hosting/operations. The table is conservative capacity planning, not verified revenue or a guarantee of profit. The legacy financial importer requires documented net revenue of at least 65%; native subscription access does not invent those values. While Discord payouts are pending, the owner bridges hosting and AI costs against verified subscription purchases. Expected proceeds remain separate from cash actually received, and payout delay alone does not pause paid service. The $100 monthly planning baseline is advisory: crossing it raises an operator warning and does not stop admission or renewals. Revenue from zero or very few customers can still fall short of fixed infrastructure costs.

| Product | Gross | Max AI liability | Hosting allocation | Remainder with 35% fee reserve |
|---|---:|---:|---:|---:|
| Basic | $1.99 | $0.6000 | $0.1990 | $0.4945 |
| Plus | $4.99 | $1.5400 | $0.4990 | $1.2045 |
| Premium | $9.99 | $3.4000 | $0.9990 | $2.0945 |
| Extra chat | $0.99 | $0.3000 | $0.0990 | $0.2445 |
| Extra reasoning | $0.99 | $0.3000 | $0.0990 | $0.2445 |
| Web search pack | $1.99 | $0.6000 | $0.1990 | $0.4945 |
| Image pack | $2.99 | $0.9600 | $0.2990 | $0.6845 |
| Storage boost (100 MiB, 30 days) | $0.99 | $0.0000 | $0.0990 | $0.5445 |

The per-attempt maximum holds are $0.0015 chat, $0.003 reasoning, $0.04 search, and $0.08 image. A core operation reserves $0.00005 of the infrastructure allocation, not measured Railway CPU. All money arithmetic in the ledger is integer microdollars; UI dollar formatting is display-only.

## Enforcement and failure behavior

SQLite transactions reserve allowance before dispatch. Concurrent calls cannot spend the same unit. Optional web reserves both possible outcomes, then atomically consumes only the feature used by a verified response. A dispatched timeout or cancellation retains the maximum reservation and never automatically retries. Missing usage or an unexpected price contract locks further paid calls. Duplicate access grants and renewals are idempotent; authenticated period identity prevents replay from refilling quota.

Every AI operation still reserves its maximum provider cost in the durable paid accounting ledger before dispatch. Verified usage settles the reservation to its actual rated cost. If a request was dispatched but its outcome or usage is unknown, its reservation remains held until reconciled; it is not refunded to make room for another request. The legacy `BUDGET_*` policy values and personal daily/monthly calculations are preserved as immutable migration metadata, not commercial paid-usage cutoffs. `HARD_BUDGET_ENABLED=true` remains required to initialize durable accounting. Preserve recorded charges, reservations, and unknown periods across restart and migration; never delete or reset a ledger to restore access.

Native access uses `PREPAID_MODE=enforce`, `DISCORD_PURCHASE_MODE=enforce`, and `DISCORD_FUNDING_MODE=entitlement`. A complete authenticated Discord snapshot, at most five minutes old, must match the configured app and SKU map. Only mapped application subscriptions with matching guild and subscription periods grant allowances. Active entitlements may have a null end; the subscription supplies its current period. Test and gift entitlements are rejected. Ended, disappeared, or deleted entitlements revoke access. Cancellation keeps access only through Discord's remaining period. Entitlements do not prove invoice totals, net payments, or payouts; their infrastructure revenue is zero.

`DISCORD_FUNDING_MODE=settlement` retains the separate reviewed financial-import path described in [discord-purchases.md](discord-purchases.md). Its operator evidence requirements do not apply to native access. No approval flags or fabricated invoices are needed for normal native subscriptions.

Production runs one Linux process with an exclusive file lease and at most two concurrent generation requests. It uses the existing Railway `/app/data` volume. Free profile/game records keep a 1 MiB baseline per server; paid capacity is additive. Storage cleanup preserves those free records and gives expired paid chat a persisted 48-hour grace period before pruning. Native backups remain an operator responsibility.

`AI_ACCESS_MODE=disabled` blocks AI initialization and dispatch independently of payment mode. `PREPAID_MODE=off` or `preview` does not enforce paid access. Production activation requires the native access configuration, durable paid accounting, verified provider calls, and a published Discord purchase path. Regular chat reports only "I can't do that right now." when unavailable; `/usage` and `/plans` explain member allowances without exposing internal costs. Per-request maximum reservations, finite plan quotas, and retained holds for unresolved usage remain in force; the $100 operator planning baseline is not an admission threshold.

## Primary references

- [Luna model and rates](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [Web-search context limit](https://developers.openai.com/api/docs/guides/tools-web-search#limitations)
- [Flare image rates](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare)
- [Image size/quality calculator](https://developers.openai.com/api/docs/guides/image-generation#cost-and-latency)
- [Discord subscriptions](https://docs.discord.com/developers/monetization/implementing-app-subscriptions)
- [Discord entitlements](https://docs.discord.com/developers/resources/entitlement)

New offerings need bounded resource contracts and verified access, accounting, and lifecycle behavior before sale.
