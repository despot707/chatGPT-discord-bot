# Public site and paid launch handoff

Reviewed September 29, 2026. The public website is separate from the Discord worker. Publishing the site does not enable payments or change the running bot.

## Public application URLs

| Developer Portal field | URL |
|---|---|
| Website | https://discord-community-assistant.pages.dev/ |
| Privacy Policy URL | https://discord-community-assistant.pages.dev/privacy/ |
| Terms of Service URL | https://discord-community-assistant.pages.dev/terms/ |
| Support | https://discord-community-assistant.pages.dev/support/ |
| Proposed plans | https://discord-community-assistant.pages.dev/plans/ |

The temporary display name is **Discord Community Assistant**. The owner plans to choose a final name and custom domain later. Change the site configuration and rebuild when those are chosen; update the application profile and policy links at the same time. The bot is not an official Discord or OpenAI product.

The public support route is https://github.com/despot707/chatGPT-discord-bot/issues. Repository issues were enabled and verified during this work. Members should use `/profile` -> **Privacy & data** for account data controls. A public issue is not a place to submit personal records, keys, or payment evidence. Establish a private escalation contact before sales.

## Readiness boundary

The website presents a **plan preview**, with Basic $0.99, Plus $4.99, and Premium $9.99 USD per server/month and finite allowances from [prepaid-plans.md](prepaid-plans.md). It has no checkout, login, payment collection, or paid subscription activation. Image allowances are proposed and depend on the provider contract being verified; voice is not included.

Application code contains the prepaid reservation and lifecycle preparation, but `Ledger.credit(Payment)` is an internal accounting method, not payment authentication. The example approval file stays unapproved. Do not turn approval flags on merely because the website or offline tests pass.

## Required before the first paid server

1. **Discord owner onboarding.** Complete the Developer Portal checklist: verified app, Developer Team ownership, owner age/region eligibility, verified email and 2FA for team members, slash commands or approved privileged access, accurate metadata, public policies, payout setup, and the owner's acceptance of Discord's terms. The actual eligibility state has not been verified here. Put the URLs above into the application's public profile.
2. **Private support and retention.** Confirm the operator identity/contact, a private escalation channel, production retention settings, backup expiry, server-removal cleanup, data export/deletion behavior, and incident handling. Current source defaults saved chat to 30 days, with expiry cleanup on access; this does not prove production configuration or immediate deletion of idle data. Keep the public policy consistent with verified operations.
3. **Authentic purchase integration.** Create the intended guild subscriptions and suitable add-ons only after review. Implement server-side authenticated Discord event handling, authoritative purchase reconciliation, immutable renewal/receipt IDs, correct guild binding, refunds/revocations, duplicate-event handling, and separation of test access from actual payments. Do not trust a client-supplied price or receipt.
4. **Resolve net funding before grants.** A Discord entitlement establishes access to a SKU and its period; it does not itself establish the exact settled net USD proceeds required by the present `Payment` contract. Identify a verified settlement source and reconciliation process, or review and test a revised conservative funding contract. Until then, `receipts_verified` must remain false. Do not turn entitlements directly into fully funded ledger credits.
5. **Provider and resource evidence.** Verify actual account model access, current rates and usage fields, maximum per-request costs, storage lifecycle, and isolated commercial hosting/API projects. Configure native resource/spend controls and startup funding with evidence. Do not reuse unrelated project caps or count alerts as hard enforcement. Offline tests do not establish live prices or billing ceilings.
6. **Purchase lifecycle acceptance test.** In a test environment prove purchase -> correct server allowance, replay -> no duplicate credit, renewal -> distinct grant, expiry -> access removal, refund -> revocation, concurrent requests -> no overdraft, and timeout -> retained reservation. Verify storage downgrade/grace/cleanup and export/deletion independently. Then perform a deliberately bounded live provider smoke test within the approved budget.
7. **Enable a reviewed deployment.** Use one Linux writer in an isolated commercial environment. Record fresh operator evidence, set `PREPAID_MODE=enforce` only after all gates pass, and verify the deployed revision. `off` and `preview` preserve personal-bot behavior and are not commercial spending protection.

The connected Railway app confirmed the production `chatGPT-discord-bot` service in project `happy-nourishment` has a successful deployment, one configured replica, and a volume at `/app/data`. Its source is `codex/modernize-discord-bot`; the prepaid preparation branch is not its configured source. Startup logs confirmed Discord connected in two guilds and that historical scans and passive profile collection were disabled. Environment variable values are redacted through this connection, so exact live retention/provider settings and backup deletion remain unverified. The CLI's existing linked project was unrelated and was left untouched. No production environment variables, deployment, Discord application settings, checkout, SKUs, or payout settings were changed by this website task.

## Official launch references

- [Premium Apps onboarding](https://support-dev.discord.com/hc/en-us/articles/17708927296663-Premium-Apps-Onboarding)
- [SKU and Store setup](https://support-dev.discord.com/hc/en-us/articles/17298449675927-Premium-Apps-SKU-and-Store-Setup)
- [Required Premium Apps support for monetizing apps](https://support-dev.discord.com/hc/en-us/articles/23810643331735-Premium-Apps-Required-Support-for-Monetizing-Apps)
- [Monetization Policy](https://support.discord.com/hc/en-us/articles/10575066024983-Monetization-Policy)
- [Monetization Terms](https://support.discord.com/hc/en-us/articles/5330075836311-Monetization-Terms)
- [App Directory content requirements](https://support-dev.discord.com/hc/en-us/articles/9489299950487-App-Directory-App-Content-Requirements-Policy)

See also [monetization-compliance.md](monetization-compliance.md) for the platform checklist and [prepaid-plans.md](prepaid-plans.md) for allowance semantics and financial gates. Recheck platform requirements at activation; this document does not certify account eligibility or legal compliance.
