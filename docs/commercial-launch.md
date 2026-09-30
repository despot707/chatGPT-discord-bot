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

## Publication steps

1. Deploy the exact tested source with AI paused, native Discord access enforced, and the persistent $10 API ledger enabled ($7 Luna / $3 Extras, Pacific daily reset).
2. Verify live startup, complete authenticated purchase reconciliation, and bounded model requests against that same ledger.
3. Lift the AI pause; publish the three real guild-subscription SKUs with Store & API visibility and add them in Manage Store.
4. Switch the website to `saleStatus: live`, deploy, and verify desktop/mobile checkout links and prices.
5. Submit public bot-directory listings with accurate free/premium features and tags. A submitted review queue is not a public approved listing.

No manual invoice, assumed net revenue, or fabricated approval JSON is required for native Discord service access. Financial records remain separate. A real customer checkout/renewal is distinct from offline lifecycle tests and provider smoke tests.

## Interface references and boundaries

[Dyno](https://dyno.gg/) separates its command/help navigation from Premium. [Discord Bot List](https://discordbotlist.com/) presents a direct Add Bot action and short, specific feature descriptions. Sidecord follows those useful patterns with a direct invite, a concise free-tools section, separate AI plans, and private `/plans` and `/usage` views.

Free features are profiles, local game discovery, parties, and team building. Paid features are AI chat, image understanding, explicit reasoning, available web lookups, and explicit image generation. Voice, image editing, external file hosting, and Steam-library import are not advertised as included. Regular errors remain generic; plan pages disclose finite monthly allowances and daily availability.

[Top.gg submissions](https://support.top.gg/hc/en-us/articles/23135162935708-How-to-Add-Your-Bot) require an online public bot and staff review. Directory OAuth authorizations are separate from Discord's own application settings. Pending authorizations or external reviews must be identified without claiming a public listing.
