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

## Published release

On September 30, production release `591b7b8a52d61e7bb05861f230fb334fd5e8c8da` reached Railway SUCCESS as deployment `1e98221c-6921-45d9-8661-2e37ee0bb1da`. The bot registered 26 commands, connected to both existing guilds, completed its authenticated entitlement snapshot, and completed storage maintenance with zero records removed. AI is enabled; native Discord purchases and the persistent $10 API cap are enforced ($7 Luna / $3 Extras, Pacific daily reset).

Railway's source now points to `codex/discord-purchase-reconciliation`. A variable-triggered deployment initially rebuilt the old `codex/modernize-discord-bot` branch. That deployment was stopped, the exact tested source restored, and the source setting corrected and independently read back. Future configuration changes must preserve the commercial source and persistent volume.

All three guild-subscription SKUs are published with Store & API visibility and appear in the public store at $1.99, $4.99, and $9.99 per server/month. Public customer-facing buttons and the website's Basic deep link were verified in Discord. The application description and five tags (ai, chatbot, community, gaming, utilities) are saved.

The live website is published at https://sidecord-ai.com with Cloudflare Pages deployment `b50681d9`. Its 76 static checks passed. Desktop and 390-pixel mobile views were checked; the mobile document has no horizontal overflow. All three plan links and the bot invite are present, and the Basic link opens its Discord product details.

Actual provider calls returned chat, a valid 48,160-byte JPEG, a web answer, and explicit reasoning. Ordinary chat also succeeded when optional search capacity was unavailable. Testing found and fixed a startup-readiness lock race and the provider's completed-search-plus-ignored-attempt response shape. Both search dispatches retained their full conservative charge. The production ledger was backed up and audited at `/app/data/launch-reconciliation-1790804523`; no usage was reset. Recorded test charges totaled $0.094157, with zero pending reservations and no lock after final validation.

The LNCCX type-1/null-end purchase fix is integrated. Verification included 544 passing tests and 3 platform skips in the full Windows run, then all 24 parser-level purchase tests after four additional identity negatives. Ruff, formatting, mypy, compilation, and Git diff checks passed. GitHub CI passed for the exact code commit on Linux/Windows and Docker. Payload tests use actual discord.py REST parsers with offline JSON; provider smoke tests use an isolated synthetic allowance. Neither is a real paid Discord checkout.

## Remaining external verification

- A real Discord purchase, customer access activation, and renewal have not yet been observed. No customer revenue or payout is claimed.
- Top.gg and Discord Bot List submissions await the owner's OAuth approvals. No listing or review approval is claimed.
- Discord Discovery requires a Community support server. Its selector had no eligible server; summary/language/description are prepared but not saved because the required server is absent.
- Custom SKU artwork was not uploaded because the browser extension's file-upload permission is unavailable. Discord's default product artwork is live.

No manual invoice, assumed net revenue, or fabricated approval JSON is required for native Discord service access. Financial records remain separate. The global cap protects this bot's calls from activation onward; unrelated use of the API key is outside this ledger.

## Interface references and boundaries

[Dyno](https://dyno.gg/) separates its command/help navigation from Premium. [Discord Bot List](https://discordbotlist.com/) presents a direct Add Bot action and short, specific feature descriptions. Sidecord follows those useful patterns with a direct invite, a concise free-tools section, separate AI plans, and private `/plans` and `/usage` views.

Free features are profiles, local game discovery, parties, and team building. Paid features are AI chat, image understanding, explicit reasoning, available web lookups, and explicit image generation. Voice, image editing, external file hosting, and Steam-library import are not advertised as included. Regular errors remain generic; plan pages disclose finite monthly allowances and daily availability.

[Top.gg submissions](https://support.top.gg/hc/en-us/articles/23135162935708-How-to-Add-Your-Bot) require an online public bot and staff review. Directory OAuth authorizations are separate from Discord's own application settings. Pending authorizations or external reviews must be identified without claiming a public listing.
