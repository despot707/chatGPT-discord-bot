# Private member settings

Use `/profile` in a server to open the Discord-native private card. Buttons, menus,
forms, validation errors, previews and exports are ephemeral. The command name may
be visible in Discord's command picker, but another member does not receive your
setup conversation. Ordinary messages posted in a channel cannot be made private
retroactively: use `/remember text:...` or the **Tell me what to save** form instead.

The card uses native Components V2 containers, an avatar thumbnail, a restrained
accent, buttons, select menus and labeled modals. It is not a custom HTML website,
and the concept image is not a pixel-exact screenshot of Discord.

Profiles contain explicitly saved games, roles, play style, month/day birthday,
timezone, availability and game categories. No birth year, password, OAuth token,
personality assessment or relationship graph is stored by this feature. No account
link is needed: Discord supplies the invoking account's user ID. IDs, not names,
are the keys. Fields default to private; optional server sharing is explicit.

`/remember` sends a bounded request to the already configured AI provider, validates
one supported change, and shows Save/Edit/Cancel. It cannot select another user's
ID, turn on sharing, or delete a whole profile. No write occurs until confirmation.
Forms save directly to SQLite without an AI call. Profile context is provided only
for the current speaker; public AI replies receive shared fields only. Visibility
changes do not retract content previously shared in Discord.

Every panel and modal is owner/server bound and expires. Transactional revisions
reject stale or duplicate submissions. Session identifiers do not contain personal
field values. Setup errors never fall back to a public channel send. Saved settings
persist in `PROFILE_DATABASE_PATH` (default `data/profiles.sqlite3`) on `/app/data`.

The production client disconnects the historical birthday mixin and passive archive
regardless of old environment flags. Set `ENABLE_LONG_TERM_MEMORY=false` and
`BIRTHDAY_BACKFILL_ENABLED=false` as additional operational safeguards. Existing
legacy databases are not automatically destroyed or imported into user-entered
settings. A separate operator cleanup of unnecessary old data/backups remains needed.

Privacy offers inspection, settings-only JSON export, make-all-private and explicit
deletion. Deletion removes profile payloads and attributable records from the known
live chat/memory/gaming stores for this account and server. Since legacy shared bot
chat has no per-author IDs, the confirmation explicitly includes resetting shared
bot-chat context in this server. Other users' private chats and other servers are
preserved. A minimal account/server revision marker remains to invalidate old forms.
Partial storage failures are reported, not described as complete deletion. This
control does not erase original Discord messages, provider logs or backup copies.

Birthday lookup lists only month/day values members chose to share. Automatic
birthday announcements, external OAuth account linking, billing, and full legal or
platform approval are not provided by this release. “Private” refers to visibility
to other server members, not invisibility from Discord, hosting or AI processors.
No encryption-at-rest or blanket security guarantee is implied by the interface.
