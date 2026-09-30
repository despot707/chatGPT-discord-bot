# Discord Premium Apps compliance checklist

Last audited: 2026-09-30

The custom-domain [Privacy Policy](https://sidecord-ai.com/privacy/), [Terms](https://sidecord-ai.com/terms/), and [Support](https://sidecord-ai.com/support/) routes were verified on September 30, 2026 as part of production revision `85b7a20d`; tested public assets and routes matched the local build byte for byte. Cloudflare's domain API reports association, verification, and validation active. Analytics was disabled with the owner's approval; Cloudflare reports “RUM is currently disabled for this zone,” and the live routes contain no `static.cloudflareinsights.com` injection. Developer Portal metadata and the separate Terms and Privacy URLs were saved and persisted after reload. The website/support address appears in the General Information description suffix; there are no separate Website or Support fields. Team ownership and 2FA are green, and owner identity/onboarding plus monetization onboarding have cleared sufficiently for Manage SKUs. Three monthly USD guild-subscription SKUs were created September 30, 2026; they remain drafts, unpublished, and unapproved for sale, with artwork upload pending browser file-URL permission. Checkout, provider-contract review, and financial-settlement validation remain incomplete. A working bot invite still needs verification. See [commercial-launch.md](commercial-launch.md) for the observed portal and deployment state and remaining launch gates. Public policies do not establish Premium Apps approval.

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
- Public policies are maintained at PRIVACY.md and TERMS.md and rendered into the site during its build.

## Required operator actions before enabling paid SKUs

These cannot be completed by application code and must be confirmed in the Discord Developer Portal:

1. App is verified. **Eligibility gate cleared:** Premium Apps accepted its prerequisites and unlocked SKU management on September 30, 2026; the separate verification form was not re-audited in this SKU setup.
2. App is owned by a Discord Developer Team. **GREEN:** team ownership is confirmed.
3. Team owner is at least 18 and in a supported Premium Apps locale. **Eligibility gate cleared by Discord onboarding;** no identity documents or private eligibility data are stored in this repository.
4. Team owner and every team member have verified email and 2FA. **GREEN:** App Verification checklist passes.
5. App uses slash commands and, where required, has approval for privileged Message Content access.
6. General Information Terms of Service URL points to the current public Terms. **Saved and verified after reload.**
7. General Information Privacy Policy URL points to the current public Privacy Policy. **Saved and verified after reload.**
8. App name and general description are saved as **Sidecord Ai** and the recorded product summary; tags are `ai`, `chatbot`, `community`, `gaming`, and `utilities`. Metadata and content checks are green. Basic, Plus, and Premium SKU names, descriptions, benefits, and prices are saved as unpublished guild-subscription drafts. Custom artwork and Store Page review remain pending before sales.
9. The General Information description ends with `Website and support: https://sidecord-ai.com`; website and support are not separate Developer Portal fields. The public GitHub issue route is enabled; confirm the operator identity and a private escalation contact before launch.
10. Payout Settings are completed with a valid payment method. **GREEN:** Team page reports “Payout info added!” with Stripe. Owner identity onboarding subsequently cleared as well.
11. Team owner accepts Discord Monetization Terms and applicable Premium Apps policy. **Onboarding gate cleared:** the portal now allows SKU creation instead of presenting the agreement gate.
12. The Developer Portal Premium Apps eligibility checklist is fully green, and three unpublished SKUs were created successfully.
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
