# Canonical games, branding, and recent conversation context

## Game selection

`/addgame game:` provides live Discord autocomplete: type a few letters and choose
one of at most 25 matching titles. The submitted value must be a known catalog ID;
raw names and fabricated IDs are rejected. `/profile` > Games > Add a game opens a
private search form, then a short results selector. Modals do not provide live
keystroke autocomplete; use `/addgame` for that experience. Selecting a game opens
its settings/confirmation without an editable title field.

The catalog is a packaged snapshot of Wikidata's CC0 video-game records with
English labels/descriptions, indexed locally with SQLite FTS5. See
`assets/game_catalog_source.json` for source date and actual record count. It
covers multiple platforms, not only Steam; it is not a guarantee of every game,
new release, edition, DLC, or platform-specific variant. The importer can refresh
the snapshot without changing saved item IDs. It does not import game accounts,
member libraries, passwords, tokens, or Discord message histories.

Selected titles are saved with stable `wikidata:Q...` IDs. Identical names with
different IDs remain separate titles; the same ID with an updated label remains
one title. Prefix/acronym search helps discovery without storing the user's search
spelling. `/remember` game requests also require selecting a canonical title.
Existing legacy free-text selections are not guessed into IDs; edit/reselect them.

`/findplayers game:` uses the same catalog and searches only current members of
this server who explicitly set that game to **This server**. Private games never
appear. Searching does not ping anyone. Setup, searches, errors and confirmations
are ephemeral and checked against the requesting account/server, as before.

## Actual icon

`assets/luna-avatar.jpg` is the standalone moon-cat icon extracted from the
user's existing concept artwork. On startup, the bot updates its own avatar and
its application icon using Discord's authenticated APIs, without changing names,
credentials or permissions. Each successful operation is recorded by bot ID and
asset hash, so restarts do not continually overwrite owner changes. Failures are
logged separately and are not reported as success. Discord's client caching may
delay the visible change. Individual slash commands use Discord's native display
and the application identity; they are not separate independently branded bots.

## Recent conversation context

When Message Content access and both member/bot channel-history permissions allow,
`/chat` now uses the configured recent-message count by default, up to 20 messages
and 8,000 characters. `context_messages:0` explicitly disables channel context for
that request. Mentions/replies retain their existing automatic-context path. The
reader includes bot replies as well as human messages so follow-up questions can
refer to visible earlier answers and polls. Webhook messages remain excluded.

Context is read only from the invoking channel, with speaker labels and oldest-to-
newest ordering. Private setup responses are not public channel history. Retrieved
context is untrusted reference material, not instructions, and is not added to the
old passive archive. Historical birthday scanning/personality collection remains
disabled. Context availability improves follow-up answers, not a guarantee that
the model will interpret every ambiguous question correctly.

Diagnostics log counts and feature flags, not message content or credentials.
