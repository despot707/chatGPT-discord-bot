# Birthday history and stable identities

The existing public-guild memory uses Discord user IDs. The new index keeps
`(guild_id, user_id)` as the identity, with non-exclusive observed usernames,
global display names, and server nicknames. Changing a name never creates a new
person. Different accounts are never merged by similar names. Reused names are
ambiguous. Literal untagged name matches are always tentative.

Historical API responses may show a user's current name, not the nickname used
when the original message was sent. Alias timestamps mean **observed by the bot**,
not a reconstructed historical name-change date. Names are refreshed on new
messages and on name-change events Discord actually supplies. No new privileged
Members intent is required; missing update events are reconciled on next sighting.

## Startup configuration

- `ENABLE_LONG_TERM_MEMORY=true` and `ENABLE_MESSAGE_CONTENT=true` remain prerequisites.
- `BIRTHDAY_BACKFILL_ENABLED=true` starts/resumes the historical scan on READY.
  It defaults to false in new deployments. Set false to disable future startup scans.
- `BIRTHDAY_TIMEZONE=America/Los_Angeles` interprets undated wishes and relative days.
  A posting-day inference remains tentative; this is not each member's known timezone.
- `BIRTHDAY_SCAN_PAGE_DELAY_SECONDS=1` spaces pages (clamped to 0.25–30 seconds).
- `MEMORY_DATABASE_PATH=data/memory.sqlite3` uses the existing persistent `/app/data` volume.

The scanner reads pages of at most 100 messages newest-first, round-robin across
public, readable, configured channels. It preserves one durable cursor per channel.
After ordinary channels/active public threads it discovers accessible archived
public threads. Private channels, private threads, DMs, bots, webhooks, and channels
excluded by existing allowlists are not indexed. Missing access and rate limits do
not become fabricated results. Transient failures retry, then preserve the cursor.
Completed historical channels are not repeatedly rescanned on restart. New messages
are tracked live; this is not a promise to recover every message sent while offline.

No LLM call is made per scanned message. Historical backfill persists birthday
candidates and observed aliases, not a full archive of all old messages. It does not
reconstruct years of personality profiles as part of this birthday-only operation.

## Evidence and ambiguity

Recognizes common English birthday wishes, explicit self-reported month/day dates,
and today/tomorrow/yesterday references. IDs in mentions are authoritative identifiers.
A reply recipient is used only when the supplied reply target is a self-birthday
statement. Literal names resolve only when a unique observed alias matches. Later
alias collisions revoke that tentative association at retrieval time. Untagged
unknown names and multiple recipients stay unresolved. This is a conservative
pattern matcher, not perfect natural-language understanding.

Belated/early wishes without an explicit date stay undated. Ambiguous numeric dates
stay unresolved. Multiple competing dates or explicit denials lower confidence.
Repeated annual wishes are called a pattern, not confirmation. No invented
probability percentages, birth year, age, or hidden-account identity inference.
Each shown candidate links to original source messages. Original tables are preserved;
legacy birthday signals are not treated as validated facts by this index.

## Commands

- `/birthdays` shows up to 15 candidates; optional `member` selects an account.
- `/birthday_scan_status` reports counts and progress to bot/server administrators.
- `/birthday_forget` removes and disables the caller's birthday tracking for that server.

Results are ephemeral. Sources are restricted to currently readable public channels.
Raw historical conversational evidence stays in its source channel. Live source
edits/deletions refresh/remove birthday evidence. Deletions while the bot is offline
cannot be discovered automatically by a one-time history scan.
