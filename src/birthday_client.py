"""Additive birthday backfill and stable-identity integration for DiscordClient.

Only public, configured, readable guild channels are indexed. Pagination checkpoints
are durable; reconnections do not spawn duplicate scans. Historic backfill stores
birthday candidates and observed aliases, not every historic conversation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from collections import deque
from contextlib import closing, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.birthday_memory import BIRTHDAY_WORD, BirthdayStore, parse_birthday

logger = logging.getLogger(__name__)
_MEMORY_SCOPE: ContextVar[tuple[Any, Any, Any] | None] = ContextVar(
    "birthday_memory_scope", default=None
)


@dataclass(frozen=True)
class HistoryBoundary:
    """discord.abc.Snowflake-compatible pagination boundary."""

    id: int


def identity_names(user):
    return list(
        dict.fromkeys(
            getattr(user, key, None)
            for key in ("display_name", "name", "global_name", "nick")
            if getattr(user, key, None)
        )
    )


def message_record(message, bot_id=None):
    people = [message.author, *getattr(message, "mentions", ())]
    row = dict(
        message_id=message.id,
        guild_id=message.guild.id,
        channel_id=message.channel.id,
        user_id=message.author.id,
        content=message.content or "",
        created_at=message.created_at.timestamp(),
        observed_at=time.time(),
        identities=[(p.id, identity_names(p)) for p in people if not getattr(p, "bot", False)],
        bot_ids=[p.id for p in people if getattr(p, "bot", False)] + ([bot_id] if bot_id else []),
    )
    reference = getattr(message, "reference", None)
    original = getattr(reference, "resolved", None)
    if (
        original is not None
        and getattr(reference, "channel_id", None) == message.channel.id
        and getattr(original, "author", None) is not None
    ):
        from zoneinfo import ZoneInfo

        original_row = dict(
            user_id=original.author.id,
            content=getattr(original, "content", ""),
            created_at=original.created_at.timestamp(),
        )
        parsed = parse_birthday(original_row, ZoneInfo("UTC"))
        if parsed and parsed[0] == original.author.id and parsed[4] == "self":
            row["reply_subject_id"] = original.author.id
    return row


async def scan_page(store, channel, allowed, *, bot_id=None):
    """Read one page and atomically save its observations and resume cursor."""
    if not allowed(channel):
        return False
    progress = await asyncio.to_thread(store.progress, channel.guild.id, channel.id)
    if progress["complete"]:
        return False
    before = progress["before_id"]
    kwargs: dict[str, Any] = dict(limit=100, oldest_first=False)
    if before is not None:
        kwargs["before"] = HistoryBoundary(before)
    page = [m async for m in channel.history(**kwargs)]
    if not allowed(channel):
        return False
    oldest = min((m.id for m in page), default=before)
    if page and before is not None and oldest >= before:
        raise RuntimeError("History pagination did not advance")
    rows = [
        message_record(m, bot_id)
        for m in page
        if not getattr(m.author, "bot", False) and not getattr(m, "webhook_id", None)
    ]
    complete = len(page) < 100
    await asyncio.to_thread(
        store.ingest, rows, checkpoint=(channel.guild.id, channel.id, oldest, complete, len(page))
    )
    return not complete


if TYPE_CHECKING:
    from src.aclient import DiscordClient as _BirthdayBase
else:
    _BirthdayBase = object


class BirthdayMemoryMixin(_BirthdayBase):
    """Keep the existing chat/gaming client while extending its memory lifecycle."""

    def _birthday_store(self):
        if not getattr(self.config, "enable_long_term_memory", False):
            return None
        store = getattr(self, "_birthday_store_handle", None)
        if store is None and not getattr(self, "_birthday_store_failed", False):
            try:
                store = BirthdayStore(
                    getattr(self.config, "memory_database_path", "data/memory.sqlite3"),
                    os.getenv("BIRTHDAY_TIMEZONE", "America/Los_Angeles"),
                )
                self._birthday_store_handle = store
                logger.info("Birthday identity store initialized with persistent checkpoints")
            except (OSError, ValueError, KeyError, sqlite3.Error):
                self._birthday_store_failed = True
                logger.exception("Birthday identity store unavailable")
        return store

    def _birthday_channel_allowed(self, channel):
        guild = getattr(channel, "guild", None)
        if (
            guild is None
            or not getattr(self.config, "enable_long_term_memory", False)
            or not getattr(self.config, "enable_message_content", False)
            or not self.allowed((guild.id, channel.id, 0))
        ):
            return False
        is_private = getattr(channel, "is_private", None)
        if callable(is_private) and is_private():
            return False
        if guild.me is None or getattr(guild, "default_role", None) is None:
            return False
        try:
            # Default-role visibility avoids silently indexing moderator/private rooms.
            everyone = channel.permissions_for(guild.default_role)
            bot = channel.permissions_for(guild.me)
            return all(
                getattr(p, name, False)
                for p in (everyone, bot)
                for name in ("view_channel", "read_message_history")
            )
        except (AttributeError, TypeError):
            return False

    def _birthday_readable_ids(self, guild, requester):
        channels = [*getattr(guild, "text_channels", ()), *getattr(guild, "threads", ())]
        # Also include discovered public archived threads, rechecking permissions each time.
        channels.extend(getattr(self, "_birthday_archived_channels", {}).get(guild.id, {}).values())
        return list(
            dict.fromkeys(
                c.id
                for c in channels
                if self._birthday_channel_allowed(c) and self._can_read_channel(c, requester)
            )
        )[:900]

    def _get_memory_store(self):
        # The original client's passive archive precedes its channel allowlist check.
        # Restrict it before entry, also preventing the old archive from recording DMs.
        if _MEMORY_SCOPE.get() is None:
            return None
        return super()._get_memory_store()

    def _memory_context(self, guild_id, query, user_ids=()):
        scope = _MEMORY_SCOPE.get()
        if scope is None or scope[0].id != guild_id:
            return ""
        guild, channel, requester = scope
        store = self._birthday_store()
        parts = []
        if store is not None and BIRTHDAY_WORD.search(query):
            # Explicit user IDs dominate name labels. Never infer that renamed users are new accounts.
            targets = set(user_ids)
            for token in re.findall(r"\b[\w.-]{2,32}\b", query):
                resolved = store.resolve_alias(guild_id, token)
                if resolved:
                    targets.add(resolved)
            parts.append(
                store.context(
                    guild_id,
                    self._birthday_readable_ids(guild, requester),
                    tuple(targets),
                    getattr(self.config, "memory_context_items", 12),
                )
            )
        raw = self._get_memory_store()
        if raw is not None and self._can_read_channel(channel, requester):
            # Raw statements remain in their source channel, not leaked across permissions.
            terms = raw._terms(query)
            if terms and user_ids:
                try:
                    with closing(raw._connect()) as db:
                        conditions = " OR ".join("lower(content) LIKE ?" for _ in terms)
                        ids = list(user_ids)[:50]
                        rows = db.execute(
                            "SELECT user_id,display_name,content,created_at FROM discord_messages "
                            "WHERE guild_id=? AND channel_id=? AND user_id IN ("
                            + ",".join("?" for _ in ids)
                            + ") AND ("
                            + conditions
                            + ") ORDER BY created_at DESC LIMIT 12",
                            (guild_id, channel.id, *ids, *["%" + t + "%" for t in terms]),
                        ).fetchall()
                    if rows:
                        lines = [
                            "Historical statements are evidence, not fixed personality facts. Contradictions reduce certainty."
                        ]
                        for uid, old_name, text, stamp in rows:
                            name = store.current_name(guild_id, uid) if store else old_name
                            name = re.sub(r"[\r\n\x00]", " ", name)[:100]
                            date_text = (
                                datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
                            )
                            lines.append(f"- [{date_text}] {name} (user {uid}): {text[:500]}")
                        parts.append("\n".join(lines))
                except (sqlite3.Error, OSError, ValueError):
                    logger.warning("Scoped long-term evidence could not be read")
        return "\n\n".join(p for p in parts if p)[:8000]

    async def on_message(self, message):
        permitted = (
            not getattr(message.author, "bot", False)
            and not getattr(message, "webhook_id", None)
            and self._birthday_channel_allowed(message.channel)
        )
        scope = (message.guild, message.channel, message.author) if permitted else None
        token = _MEMORY_SCOPE.set(scope)
        try:
            if permitted:
                store = self._birthday_store()
                if store:
                    try:
                        await asyncio.to_thread(
                            store.ingest, [message_record(message, getattr(self.user, "id", None))]
                        )
                    except (sqlite3.Error, OSError, ValueError):
                        logger.exception("Birthday observation could not be saved")
            return await super().on_message(message)
        finally:
            _MEMORY_SCOPE.reset(token)

    async def on_raw_message_delete(self, payload):
        if payload.guild_id and (store := self._birthday_store()):
            await asyncio.to_thread(store.delete_message, payload.guild_id, payload.message_id)
            self._forget_raw_message(payload.guild_id, payload.message_id)

    async def on_raw_bulk_message_delete(self, payload):
        if payload.guild_id and (store := self._birthday_store()):
            for mid in payload.message_ids:
                await asyncio.to_thread(store.delete_message, payload.guild_id, mid)
                self._forget_raw_message(payload.guild_id, mid)

    def _forget_raw_message(self, guild_id, message_id):
        # Existing archive has no delete listener. Keep evidence aligned with deletions.
        store = getattr(self, "_memory_store", None)
        if store:
            try:
                with closing(store._connect()) as db, db:
                    db.execute(
                        "DELETE FROM discord_messages WHERE guild_id=? AND message_id=?",
                        (guild_id, message_id),
                    )
                    db.execute(
                        "DELETE FROM birthday_signals WHERE guild_id=? AND message_id=?",
                        (guild_id, message_id),
                    )
            except sqlite3.Error:
                logger.warning("Deleted message could not be removed from the raw archive")

    async def on_raw_message_edit(self, payload):
        if "content" not in payload.data:
            return
        channel = self.get_channel(payload.channel_id)
        if channel is None or not self._birthday_channel_allowed(channel):
            return
        import discord

        try:
            fetch_message = getattr(channel, "fetch_message", None)
            if not callable(fetch_message):
                return
            message = await fetch_message(payload.message_id)
            if not getattr(message.author, "bot", False) and (store := self._birthday_store()):
                await asyncio.to_thread(
                    store.ingest, [message_record(message, getattr(self.user, "id", None))]
                )
                self._forget_raw_message(payload.guild_id, payload.message_id)
        except (discord.HTTPException, OSError, sqlite3.Error):
            logger.warning("Edited birthday evidence could not be refreshed")

    async def on_member_update(self, before, after):
        if (store := self._birthday_store()) and store.current_name(
            after.guild.id, after.id
        ) != f"user {after.id}":
            await asyncio.to_thread(
                store.observe_identity, after.guild.id, after.id, identity_names(before)
            )
            await asyncio.to_thread(
                store.observe_identity, after.guild.id, after.id, identity_names(after)
            )

    async def on_user_update(self, before, after):
        if store := self._birthday_store():
            for guild in self.guilds:
                if store.current_name(guild.id, after.id) != f"user {after.id}":
                    await asyncio.to_thread(
                        store.observe_identity, guild.id, after.id, identity_names(before)
                    )
                    await asyncio.to_thread(
                        store.observe_identity, guild.id, after.id, identity_names(after)
                    )

    async def setup_hook(self):
        self._register_birthday_commands()
        await super().setup_hook()

    def _register_birthday_commands(self):
        if self.tree.get_command("birthdays"):
            return
        from typing import Optional

        import discord

        async def birthdays(interaction, member=None):
            if interaction.guild is None or not self._birthday_channel_allowed(interaction.channel):
                await interaction.response.send_message(
                    "Use this in a public channel where memory is enabled.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            store = self._birthday_store()
            if store is None:
                text = "Birthday memory is unavailable."
            else:
                ids = self._birthday_readable_ids(interaction.guild, interaction.user)
                rows = await asyncio.to_thread(
                    store.summary, interaction.guild.id, ids, (member.id,) if member else ()
                )
                lines = ["Birthday candidates, not guaranteed dates:"]
                for r in rows[:15]:
                    name = discord.utils.escape_markdown(r["name"])
                    lines.append(
                        f"{name} (ID {r['user_id']}): **{r['month']:02d}-{r['day']:02d}**, "
                        f"{r['status']}; {r['signals']} messages, {r['years']} years. [Evidence]({r['sources'][0]})"
                    )
                text = (
                    "\n".join(lines)
                    if rows
                    else "No unambiguous birthday evidence found yet. The history scan may still be running."
                )
            await interaction.followup.send(
                text[:1950], ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )

        async def birthday_scan_status(interaction):
            if interaction.guild is None or not self.is_admin(interaction.user.id, interaction):
                await interaction.response.send_message(
                    "Server administrators can inspect the scan status.", ephemeral=True
                )
                return
            store = self._birthday_store()
            if store is None:
                text = "Birthday memory is unavailable."
            else:
                s = await asyncio.to_thread(store.stats, interaction.guild.id)
                task = getattr(self, "_birthday_scan_task", None)
                state = "running" if task and not task.done() else "idle or finished"
                text = (
                    f"Scan: {state}. {s['scanned']:,} messages examined; {s['candidates']:,} birthday clues; "
                    f"{s['unresolved']:,} ambiguous dates/recipients; {s['complete']}/{s['channels']} visited channels finished. "
                    "Private/inaccessible channels are excluded. Check Railway logs for skipped channels."
                )
            await interaction.response.send_message(
                text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )

        async def birthday_forget(interaction):
            if interaction.guild is None or (store := self._birthday_store()) is None:
                await interaction.response.send_message(
                    "Use this in your server with memory enabled.", ephemeral=True
                )
                return
            await asyncio.to_thread(store.forget, interaction.guild.id, interaction.user.id)
            await interaction.response.send_message(
                "Birthday tracking is disabled for your account in this server.", ephemeral=True
            )

        birthdays.__annotations__ = {
            "interaction": discord.Interaction,
            "member": Optional[discord.Member],
        }
        birthday_scan_status.__annotations__ = {"interaction": discord.Interaction}
        birthday_forget.__annotations__ = {"interaction": discord.Interaction}
        for name, callback, description in (
            ("birthdays", birthdays, "View birthday evidence and uncertain candidate dates."),
            (
                "birthday_scan_status",
                birthday_scan_status,
                "Inspect the historical birthday scan progress.",
            ),
            (
                "birthday_forget",
                birthday_forget,
                "Remove and disable your own birthday tracking in this server.",
            ),
        ):
            self.tree.add_command(
                discord.app_commands.Command(name=name, description=description, callback=callback)
            )

    async def on_ready(self):
        await super().on_ready()
        enabled = os.getenv("BIRTHDAY_BACKFILL_ENABLED", "false").strip().lower() in {
            "true",
            "1",
            "yes",
            "on",
        }
        if (
            enabled
            and not getattr(self, "_birthday_scan_started", False)
            and self._birthday_store()
        ):
            self._birthday_scan_started = True
            self._birthday_scan_task = asyncio.create_task(
                self._run_birthday_scan(), name="birthday-history-backfill"
            )

    async def _run_birthday_scan(self):
        import discord

        store = self._birthday_store()
        if store is None:
            return
        try:
            delay = max(0.25, min(float(os.getenv("BIRTHDAY_SCAN_PAGE_DELAY_SECONDS", "1")), 30.0))
        except ValueError:
            delay = 1.0
        self._birthday_archived_channels: dict[int, dict[int, Any]] = {}
        try:
            all_targets = {}
            parents_by_guild = {}
            for guild in self.guilds:
                parents = [*getattr(guild, "text_channels", ()), *getattr(guild, "forums", ())]
                parents_by_guild[guild.id] = parents
                targets = {
                    c.id: c
                    for c in [*parents, *getattr(guild, "threads", ())]
                    if hasattr(c, "history") and self._birthday_channel_allowed(c)
                }
                all_targets.update(targets)
                if targets:
                    logger.info(
                        "Birthday scan starting guild=%s public_channels=%d; resuming durable cursors",
                        guild.id,
                        len(targets),
                    )
            # Round-robin across all eligible guilds, so no busy channel starves another.
            await self._scan_round_robin(store, list(all_targets.values()), delay)
            for guild in self.guilds:
                archived = self._birthday_archived_channels.setdefault(guild.id, {})
                for parent in parents_by_guild[guild.id]:
                    if not self._birthday_channel_allowed(parent) or not hasattr(
                        parent, "archived_threads"
                    ):
                        continue
                    try:
                        async for thread in parent.archived_threads(limit=None):
                            if self._birthday_channel_allowed(thread):
                                archived[thread.id] = thread
                    except (discord.HTTPException, OSError):
                        logger.warning(
                            "Birthday scan could not list archived threads channel=%s", parent.id
                        )
                if archived:
                    await self._scan_round_robin(store, list(archived.values()), delay)
                logger.info(
                    "Birthday scan pass finished guild=%s stats=%s", guild.id, store.stats(guild.id)
                )
        except asyncio.CancelledError:
            logger.info("Birthday scan paused; checkpoints retained for next startup")
            raise
        except Exception:
            logger.exception("Birthday scan stopped; checkpoints retained, chat remains available")

    async def _scan_round_robin(self, store, channels, delay):
        import discord

        queue = deque(channels)
        failures: dict[Any, int] = {}
        pages = 0
        while queue:
            channel = queue.popleft()
            try:
                more = await scan_page(
                    store,
                    channel,
                    self._birthday_channel_allowed,
                    bot_id=getattr(self.user, "id", None),
                )
                failures.pop(channel.id, None)
                if more:
                    queue.append(channel)
                pages += 1
                if pages == 1 or pages % 10 == 0 or not queue:
                    logger.info(
                        "Birthday scan progress guild=%s stats=%s",
                        channel.guild.id,
                        store.stats(channel.guild.id),
                    )
            except (discord.Forbidden, discord.NotFound):
                logger.warning("Birthday scan skipped inaccessible channel=%s", channel.id)
            except (discord.HTTPException, OSError, sqlite3.Error):
                failures[channel.id] = failures.get(channel.id, 0) + 1
                if failures[channel.id] <= 3:
                    queue.append(channel)
                    await asyncio.sleep(5 * failures[channel.id])
                else:
                    logger.warning(
                        "Birthday scan paused channel=%s after retries; cursor retained", channel.id
                    )
            await asyncio.sleep(delay)

    async def close(self):
        task = getattr(self, "_birthday_scan_task", None)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await super().close()
