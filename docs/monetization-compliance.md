# Discord Premium Apps compliance checklist

Last audited: 2026-09-29

## Implemented in the application

- Member profiles are explicit, member-controlled, private by default, and keyed to Discord IDs.
- Passive personality/relationship profiling and historical birthday scans are disabled in the production client.
- Disabled legacy long-term-memory storage is purged from the live volume at startup.
- Profile inspection/export/privacy/deletion controls are available through private Discord interactions.
- Discord passwords and user login tokens are never requested by the profile system.
- Recent-message context is request-time context, not a passive personality archive.
- Commercial system instructions do not encourage sexually explicit output or gratuitous profanity/vulgarity.
- Secrets are kept in hosting-provider variables rather than the repository.
- Persistent storage is hosted on Railway; Railway's Trust Center/support documentation states customer data at rest, including persistent volumes and native volume backups, is encrypted at rest. Obtain the applicable Trust Center/SOC documentation for the operator's compliance records.
- Public policy drafts are maintained at PRIVACY.md and TERMS.md.

## Required operator actions before enabling paid SKUs

These cannot be completed by application code and must be confirmed in the Discord Developer Portal:

1. App is verified.
2. App is owned by a Discord Developer Team.
3. Team owner is at least 18 and in a supported Premium Apps locale.
4. Team owner and every team member have verified email and 2FA.
5. App uses slash commands and, where required, has approval for privileged Message Content access.
6. Developer Portal Terms of Service URL points to the current public Terms.
7. Developer Portal Privacy Policy URL points to the current public Privacy Policy.
8. App name, description, command metadata, role-connection metadata, SKU names/descriptions/artwork, and Store Page contain no harmful/bad language and accurately describe the product.
9. A durable public support/contact method is configured in the application profile. Replace the policy drafts' generic contact language with that contact before launch.
10. Payout Settings are completed with a valid payment method.
11. Team owner accepts Discord Monetization Terms and Monetization Policy.
12. The Developer Portal Premium Apps eligibility checklist is fully green.
13. Any required App Review/Message Content review accurately describes recent-context use and member-controlled profiles.
14. The operator reviews Discord's then-current Developer, Monetization, Community, and Premium Apps policies immediately before launch.

## Data-operation checks before launch

- Confirm ENABLE_LONG_TERM_MEMORY=false and BIRTHDAY_BACKFILL_ENABLED=false in production.
- Confirm memory.sqlite3 and related WAL/SHM files no longer exist after the compliance deployment.
- Confirm no unnecessary legacy backup containing obsolete API data is intentionally retained.
- Confirm CHAT_RETENTION_DAYS is documented and matches production.
- Confirm member deletion still removes attributable live records and reports partial failures.
- Maintain an incident-response process for unauthorized API-data access, including Discord/user notification where required.
- Maintain a process for user/Discord requests to correct or delete API data.
- Keep provider/service-provider agreements and data settings consistent with Discord Developer Terms.
- Re-audit whenever a new provider, storage system, data category, or paid feature is added.

## Monetization integration gate

Do not publish paid SKUs until every applicable operator item above is confirmed. After onboarding, paid offerings must accurately state price, allowances, limitations, renewal/consumption rules, and eligibility. Material reductions to an existing paid offering require the notice/refund/cancellation treatment required by Discord's Monetization Terms and applicable law.

This checklist is an engineering/platform-compliance aid, not legal advice.
