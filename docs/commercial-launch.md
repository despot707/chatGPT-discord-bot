# Public site and paid launch handoff

Reviewed September 30, 2026. The public website is separate from the Discord worker. Publishing the site does not enable payments or change the running bot.

## Public application URLs

| Public route | URL |
|---|---|
| Website | https://sidecord-ai.com/ |
| Privacy Policy URL | https://sidecord-ai.com/privacy/ |
| Terms of Service URL | https://sidecord-ai.com/terms/ |
| Support | https://sidecord-ai.com/support/ |
| Proposed plans | https://sidecord-ai.com/plans/ |

The production site is deployed at revision `c760ffc8`. On September 30, 2026, the root, privacy, terms, support, plans, `styles.css`, and `favicon.svg` returned HTTP 200 and matched the local build byte for byte. Cloudflare's domain API reports association, verification, and validation active. The owner approved disabling analytics, and Cloudflare now reports “RUM is currently disabled for this zone.” Live checks found no `static.cloudflareinsights.com` injection. Desktop (2560 px) and mobile (390 px) layouts were visually checked with no horizontal overflow; screenshots are saved outside the repository under `work/sidecord-launch-proof`. The app name **Sidecord Ai**, description (“AI-powered conversations and game-night help for your Discord server. Ask questions, discuss screenshots and polls, compare shared Steam libraries, and build teams. Member-controlled profiles keep preferences private by default. Website and support: https://sidecord-ai.com”), tags (`ai`, `chatbot`, `community`, `gaming`, `utilities`), Terms URL, and Privacy URL were saved in General Information and remained saved after reload. Website and support are part of the description suffix; there are no separate Website or Support fields. The bot is not an official Discord or OpenAI product.

The live installation link provided was https://discord.com/oauth2/authorize?client_id=1365724363722068120. In both user and guild install contexts it contains only the `applications.commands` scope, with no `bot` scope. It does not establish that the bot can be added to a server; `inviteUrl` remains blank until an actual bot invite is confirmed. No permissions were changed.

Portal readiness is partial. The **Sidecord Ai** Developer Team now exists with `despot707` as its sole member/owner, and its Payout Settings page reports **Payout info added!** with Stripe. The refreshed App Verification page passes metadata, Terms, Privacy, install, and email/2FA, but still lists personal ownership (`despot707`) and one missing criterion: team ownership. The app-to-team transfer was submitted and is awaiting Discord's fresh multi-factor authentication challenge; submitting the transfer button does not establish that ownership changed. App verification and Premium Apps activation remain incomplete.

The public support route is https://github.com/despot707/chatGPT-discord-bot/issues. Repository issues were enabled and verified during this work. Members should use `/profile` -> **Privacy & data** for account data controls. A public issue is not a place to submit personal records, keys, or payment evidence. Establish a private escalation contact before sales.

## Readiness boundary

The website presents a **plan preview**, with Basic $0.99, Plus $4.99, and Premium $9.99 USD per server/month and finite allowances from [prepaid-plans.md](prepaid-plans.md). It has no checkout, login, payment collection, or paid subscription activation. Image allowances are proposed and depend on the provider contract being verified; voice is not included.

Application code contains prepaid reservations plus [Discord purchase reconciliation](discord-purchases.md): explicit guild-subscription SKU mapping, authenticated access refresh, lifecycle events, and staging of independently reviewed settlement evidence. `Ledger.credit(Payment)` remains an internal accounting method, not payment authentication. The adapter does not derive invoice amounts or net proceeds from a Discord entitlement. The example approval file stays unapproved. Do not turn approval flags on merely because the website or offline tests pass.

## Required before the first paid server

1. **Finish Discord owner onboarding.** Complete the open transfer authentication challenge, verify that the app belongs to the existing Sidecord Ai team and recognizes its completed Stripe setup, then complete app verification and the owner's legal/eligibility attestations. The team, payout setup, 2FA, metadata, tags, and policy URLs already exist; do not recreate them. The current OAuth link lacks the `bot` scope, so a working bot invite also remains to be verified.
2. **Private support and retention.** Confirm the operator identity/contact, a private escalation channel, production retention settings, backup expiry, server-removal cleanup, data export/deletion behavior, and incident handling. Current source defaults saved chat to 30 days, with expiry cleanup on access; this does not prove production configuration or immediate deletion of idle data. Keep the public policy consistent with verified operations.
3. **Configure and exercise purchase integration.** The guild-subscription adapter and operator settlement importer are implemented; configure real reviewed SKU IDs and exercise them against Discord. Each paid period must match authenticated app/SKU/guild/subscription identity and independently reviewed financial evidence. Test entitlements never become paid grants. One-time add-ons remain unsupported by this adapter and must not be offered for sale.
4. **Resolve net funding before grants.** A Discord entitlement establishes access to a SKU and its period; it does not itself establish the exact settled net USD proceeds required by the present `Payment` contract. The importer records a human-reviewed receipt, exact gross/net USD amounts, period, evidence hash/reference, and reviewer; it cannot authenticate the external financial record. No actual Discord/Stripe export schema has been verified. Identify a source with the required immutable transaction identity and amounts, or review and test a revised conservative funding contract. Until then, `receipts_verified` must remain false.
5. **Provider and resource evidence.** Verify actual account model access, current rates and usage fields, maximum per-request costs, storage lifecycle, and isolated commercial hosting/API projects. Configure native resource/spend controls and startup funding with evidence. Do not reuse unrelated project caps or count alerts as hard enforcement. Offline tests do not establish live prices or billing ceilings.
6. **Purchase lifecycle acceptance test.** In a test environment prove purchase -> correct server allowance, replay -> no duplicate credit, renewal -> distinct grant, expiry -> access removal, refund -> revocation, concurrent requests -> no overdraft, and timeout -> retained reservation. Verify storage downgrade/grace/cleanup and export/deletion independently. Then perform a deliberately bounded live provider smoke test within the approved budget.
7. **Enable a reviewed deployment.** Use one Linux writer in an isolated commercial environment. Record fresh operator evidence, set `PREPAID_MODE=enforce` only after all gates pass, and verify the deployed revision. `off` and `preview` preserve personal-bot behavior and are not commercial spending protection.

The connected Railway app confirmed the production `chatGPT-discord-bot` service in project `happy-nourishment` has a successful deployment, one configured replica, and a volume at `/app/data`. Its source is `codex/modernize-discord-bot`; the prepaid preparation branch is not its configured source. Startup logs confirmed Discord connected in two guilds and that historical scans and passive profile collection were disabled. Environment variable values are redacted through this connection, so exact live retention/provider settings and backup deletion remain unverified. The CLI's existing linked project was unrelated and was left untouched. No production environment variables, deployments, checkout, SKUs, or payout settings were changed by this website task; Developer Portal metadata and policy URLs were updated as described above.

## Official launch references

- [Premium Apps onboarding](https://support-dev.discord.com/hc/en-us/articles/17708927296663-Premium-Apps-Onboarding)
- [SKU and Store setup](https://support-dev.discord.com/hc/en-us/articles/17298449675927-Premium-Apps-SKU-and-Store-Setup)
- [Required Premium Apps support for monetizing apps](https://support-dev.discord.com/hc/en-us/articles/23810643331735-Premium-Apps-Required-Support-for-Monetizing-Apps)
- [Monetization Policy](https://support.discord.com/hc/en-us/articles/10575066024983-Monetization-Policy)
- [Monetization Terms](https://support.discord.com/hc/en-us/articles/5330075836311-Monetization-Terms)
- [App Directory content requirements](https://support-dev.discord.com/hc/en-us/articles/9489299950487-App-Directory-App-Content-Requirements-Policy)

See also [monetization-compliance.md](monetization-compliance.md) for the platform checklist and [prepaid-plans.md](prepaid-plans.md) for allowance semantics and financial gates. Recheck platform requirements at activation; this document does not certify account eligibility or legal compliance.
