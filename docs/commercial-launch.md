# Sidecord launch record

## Deployment target

- Application: Sidecord Ai, `1365724363722068120`.
- Website: https://sidecord-ai.com (Cloudflare Pages project `discord-community-assistant`).
- Railway: project `dc425081-5f97-413a-a35c-6aec9822d3df`, production `c5f721fc-4be7-44a9-9638-21869b37ab81`, service `1aa50efd-bc13-480e-b7c0-09c804271685`.
- One replica; persistent volume at `/app/data`.
- Monetization onboarding, team ownership, Stripe validation, and 2FA completed by the owner.

## September 30 verification

The starting live deployment was `715235c7-0b47-4f5f-a306-0eaa6d71437c`, from the personal bot at `e08e4ff`. It connected to Discord but used `AI_ACCESS_MODE=disabled`. There was no live prepaid configuration and no budget database on its volume. The new native subscription integration replaces that preparation state; publication and deployment must be recorded from actual platform results.

Before deployment, SQLite backups were created at `/app/data/launch-backup-20260930-211343`: catalog, profiles, and chat databases each passed `PRAGMA quick_check`. This backup does not delete or reset existing records.

The public install link is now configured with `bot` and `applications.commands` scopes, requesting only View Channels, Send Messages, Send Messages in Threads, Read Message History, Embed Links, and Attach Files. Its explicit public URL reaches Discord's server picker:

https://discord.com/oauth2/authorize?client_id=1365724363722068120&scope=bot%20applications.commands&permissions=274878024704&integration_type=0

The existing API key's read-only model listing verified Luna and the September 8 Flare 2.5 snapshot. It did not list the old image snapshot or gpt-4.1-mini, so the commercial gateway uses Luna for chat/search and Flare for images. Model availability does not alone prove a successful paid request; live smoke results are recorded separately.

## Initial published release (historical)

On September 30, production release `591b7b8a52d61e7bb05861f230fb334fd5e8c8da` reached Railway SUCCESS as deployment `1e98221c-6921-45d9-8661-2e37ee0bb1da`. The bot registered 26 commands, connected to both existing guilds, completed its authenticated entitlement snapshot, and completed storage maintenance with zero records removed. AI is enabled; native Discord purchases and the persistent $10 API cap are enforced ($7 Luna / $3 Extras, Pacific daily reset).

Railway's source now points to `codex/discord-purchase-reconciliation`. A variable-triggered deployment initially rebuilt the old `codex/modernize-discord-bot` branch. That deployment was stopped, the exact tested source restored, and the source setting corrected and independently read back. Future configuration changes must preserve the commercial source and persistent volume.

All three guild-subscription SKUs are published with Store & API visibility and appear in the public store at $1.99, $4.99, and $9.99 per server/month. Public customer-facing buttons and the website's Basic deep link were verified in Discord. The application description and five tags (ai, chatbot, community, gaming, utilities) are saved.

The live website is published at https://sidecord-ai.com with Cloudflare Pages deployment `b50681d9`. Its 76 static checks passed. Desktop and 390-pixel mobile views were checked; the mobile document has no horizontal overflow. All three plan links and the bot invite are present, and the Basic link opens its Discord product details.

Actual provider calls returned chat, a valid 48,160-byte JPEG, a web answer, and explicit reasoning. Ordinary chat also succeeded when optional search capacity was unavailable. Testing found and fixed a startup-readiness lock race and the provider's completed-search-plus-ignored-attempt response shape. Both search dispatches retained their full conservative charge. The production ledger was backed up and audited at `/app/data/launch-reconciliation-1790804523`; no usage was reset. Recorded test charges totaled $0.094157, with zero pending reservations and no lock after final validation.

The LNCCX type-1/null-end purchase fix is integrated. Verification included 544 passing tests and 3 platform skips in the full Windows run, then all 24 parser-level purchase tests after four additional identity negatives. Ruff, formatting, mypy, compilation, and Git diff checks passed. GitHub CI passed for the exact code commit on Linux/Windows and Docker. Payload tests use actual discord.py REST parsers with offline JSON; provider smoke tests use an isolated synthetic allowance. Neither is a real paid Discord checkout.

## Remaining external verification

- Two discounted Basic subscriptions are active with settled chat usage, as detailed below. A real renewal or refund has not been observed. No collected customer revenue or payout is claimed.
- Top.gg submission still awaits sign-in authorization. Discord Bot List is already signed in, and a submission draft is prepared with Games, Social, and Utility tags. Its rules require a help command or alias; the deployed four-command interface removes the older `/help` registration, so submission remains pending resolution of that requirement. No listing or review approval is claimed.
- Discord Discovery requires a Community support server. Its selector had no eligible server; summary/language/description are prepared but not saved because the required server is absent.
- Custom SKU artwork was not uploaded because the browser extension's file-upload permission is unavailable. Discord's default product artwork is live.

No manual invoice, assumed net revenue, or fabricated approval JSON is required for native Discord service access. Financial records remain separate. Unrelated use of the API key is outside this bot's accounting.

## Subscription continuity update

The owner's later funding instructions replace the shared personal $10 ceiling and daily slices for native paid subscriptions. The owner bridges costs while Discord payouts are pending; $100 is a planning baseline, not a cutoff. Finite plan allowances and conservative per-request cost reservations remain required. The original publication above records the earlier policy, not the new admission behavior.

The continuity source integrates LNCCX's [paid accounting migration](paid-budget-continuity.md) and [period identity fix](discord-period-identity.md). Migration preserves the existing budget and prepaid databases, creates a verified backup, and binds the accounting ledgers without resetting spending or unresolved reservations. Corrected end dates update the existing allowance; authenticated new billing periods grant once. Upgrade, downgrade, renewal, replay, restart, and refund regressions are included.

The continuity release from `e5ce62f` reached Railway as deployment `64e85469-b23d-4f94-9d38-ce5000d1b327`. Its migration ran on September 30 at 22:37:06 UTC. An independent live audit on October 1 at 04:08:40 UTC reported `paid-entitlement-v1`, preservation of all historical budget/prepaid preflight rows, and the backup `/app/data/budget.sqlite3.before-paid-v1-85685198d4124beda4fba6779bf88ef5.sqlite3` (SHA-256 `bbfd366cd1948c59ccc0e4847f46c67a1522a5bce75d51cbad34d95edfdb37ad`). The audit recorded 94,660 actual microdollars, zero unresolved holds, nine reservations, two grants and two requests; the ledger was unlocked and the entitlement snapshot was 28 seconds old. No ledger reset or deletion was performed.

The owner reports personally completing both discounted Basic subscriptions at 23:12 UTC. An independent read-only production query confirmed two active Basic grants, each with 400 chat attempts and 1 MiB storage, two authenticated native periods, and two settled chat requests totaling 503 microdollars. This verifies live access and recorded usage; it is not checkout-cash evidence. No collected revenue or payout is claimed, and a true renewal or refund has not been observed. Do not contact or test The Mancave as part of this handoff.

The latest production release is `f87edb9bc28c25295c128598bc1bf8b3a660dacf`, Railway SUCCESS deployment `3bae0a5c-ee44-4ad0-855e-005d324737b8`. PR 6 merged as `0464f6daf75f666c400edb38e190d3ba1df617a2`. CI for `e5ce62f` passed with 608 Linux tests and two skips; all four OS/Python jobs, Docker, and 79 site checks passed. Preserve the production branch and newer compact customer controls; do not redeploy the superseded continuity build.

## Current public surfaces (October 1 UTC)

All three published Discord SKU descriptions were updated and read back in the public store. They now state finite allowances per billing period, monthly renewal, cancellation retaining the paid period, and no rollover or usage overages. The outdated personal daily-availability wording is removed. Prices and quota quantities are unchanged.

The matching website was published at https://sidecord-ai.com with Cloudflare Pages deployment `ce0f4194`. All 79 site checks passed. The live plans page was verified on desktop and at a 390-pixel mobile viewport without horizontal overflow. Its notice says there are no mid-period top-ups, while explaining that a verified monthly renewal refreshes the allowance once.

The subsequent copy-only deployment `0ee8eda6` corrects the Support page's retired commands: chat clearing is under `/settings` → **Chat & data**, leaving a party is under `/games` → **Party & teams**, and removing an existing Steam link is under `/games` → **Steam**. The README now matches these paths and the shared-chat permission requirement. All 79 site checks passed; the published Support page was verified on desktop and at 390 pixels with no horizontal overflow. This follow-up is based on main `f670e407253da7a9f1a7748851e19b79c350e394`, the copy-only PR 7 merge above PR 6; it contains no runtime changes.

These publication changes do not modify the Railway runtime. The separate events/tournaments implementation remains unpublished and is outside this release. Reviewed hosting charges, expected net revenue, and payout evidence have not been supplied to the planning report; those values remain unknown rather than zero. The $100 advisory baseline never blocks paid subscription access.

## Interface references and boundaries

[Dyno](https://dyno.gg/) separates its command/help navigation from Premium. [Discord Bot List](https://discordbotlist.com/) presents a direct Add Bot action and short, specific feature descriptions. Sidecord follows those useful patterns with a direct invite, a concise free-tools section, separate AI plans, and private plan/usage controls under its compact four-command interface.

Free features are profiles, local game discovery, parties, and team building. Paid features are AI chat, image understanding, explicit reasoning, available web lookups, and explicit image generation. Voice, image editing, external file hosting, and Steam-library import are not advertised as included. Regular errors remain generic; updated plan pages disclose finite allowances for the actual subscription billing period and automatic renewal without rollover or overage charges.

[Top.gg submissions](https://support.top.gg/hc/en-us/articles/23135162935708-How-to-Add-Your-Bot) require an online public bot and staff review. Directory OAuth authorizations are separate from Discord's own application settings. Pending authorizations or external reviews must be identified without claiming a public listing.
