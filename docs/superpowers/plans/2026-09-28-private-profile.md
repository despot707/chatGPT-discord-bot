# Private Profile Implementation Plan

Goal: ship the approved Discord-native profile concept without public setup chatter or external sign-in.
Architecture: member-entered SQLite settings keyed by (guild_id,user_id), owner-bound ephemeral Components V2 panels and modals, and a private natural-language proposal command. Existing chat/gaming remain; passive archive/birthday inference is disconnected in the production client.
Stack: existing discord.py 2.7.1, SQLite, current ProviderManager, Railway persistent /app/data.

Scope: games/roles/style, month/day birthday, timezone/availability/game-type preferences, per-field sharing, export, confirmed deletion, /profile and /remember. No passwords, OAuth grants, arbitrary profile facts, automatic birthday announcements, friend-graph inference, or encryption claims. Private setup is not a promise that ordinary channel messages or hosting/provider data are invisible.

Review focus: wrong-user/guild clicks, stale concurrent forms, duplicate saves, private values reaching public chat, failures during deletion, and restart persistence. Do not put personal values in custom IDs or logs. The mockup is directional; use real native controls.

- [ ] Write store/privacy/UI regression tests; confirm failure before implementation on isolated branch.
- [ ] Implement src/member_settings.py (validation, scoped persistence, revisions, explicit sharing).
- [ ] Implement src/profile_ui.py (private panel, forms, Save/Edit/Cancel proposal, private errors/exports).
- [ ] Implement src/profile_client.py; integrate through src/bot.py. Deactivate passive indexing independently of stale environment flags. Use private saved data only for private responses; public responses get shared settings only.
- [ ] Implement src/profile_privacy.py for explicit member-requested deletion across known live stores, with surfaced failures and stale-session invalidation. Preserve other members' private records.
- [ ] Run focused tests, full suite, compilation and payload validation. Report unrelated baseline failures, rather than hiding them.
- [ ] Fast-forward verified changes to codex/modernize-discord-bot, preserve credentials/volume, disable historical flags, verify deployed commit and Discord READY.
