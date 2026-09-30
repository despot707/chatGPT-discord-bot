"""Small SQLite store for conversations that have actually invoked the bot."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path


class ChatStore:
    """Persist bounded text turns, with separate keys for shared and private chats."""

    def __init__(self, path: str, retention_days: int = 30, max_conversations: int = 1000) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("ChatStore requires a durable SQLite file path.")
        self.retention_seconds = max(1, retention_days) * 24 * 60 * 60
        self.max_conversations = max(1, max_conversations)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS chat_turns (
                    id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS chat_turns_scope_time "
                "ON chat_turns(guild_id, channel_id, user_id, created_at, id)"
            )
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    def _expire(self, db: sqlite3.Connection, now: float) -> None:
        db.execute("DELETE FROM chat_turns WHERE created_at < ?", (now - self.retention_seconds,))

    def load(
        self, key: tuple[int, int, int], *, max_messages: int, max_chars: int
    ) -> list[dict[str, str]]:
        now = time.time()
        with closing(self._connect()) as db, db:
            self._expire(db, now)
            rows = db.execute(
                "SELECT role, content FROM chat_turns "
                "WHERE guild_id=? AND channel_id=? AND user_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (*key, max(0, max_messages // 2 * 2)),
            ).fetchall()
            db.commit()
        rows.reverse()
        messages = [{"role": role, "content": content} for role, content in rows]
        # Apply a strict character bound when loading, keeping only the newest complete pairs.
        kept: list[dict[str, str]] = []
        size = 0
        for index in range(len(messages) - 2, -1, -2):
            pair = messages[index : index + 2]
            if len(pair) != 2 or pair[0]["role"] != "user" or pair[1]["role"] != "assistant":
                continue
            pair_size = sum(len(item["content"]) for item in pair)
            if pair_size > max_chars or (kept and size + pair_size > max_chars):
                break
            kept[0:0] = pair
            size += pair_size
        return kept

    def append_turn(
        self,
        key: tuple[int, int, int],
        user_content: str,
        assistant_content: str,
        *,
        max_messages: int,
        max_chars: int,
    ) -> None:
        now = time.time()
        with closing(self._connect()) as db, db:
            self._expire(db, now)
            db.executemany(
                "INSERT INTO chat_turns(guild_id, channel_id, user_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (*key, "user", user_content, now),
                    (*key, "assistant", assistant_content, now),
                ],
            )
            rows = db.execute(
                "SELECT id, role, content FROM chat_turns "
                "WHERE guild_id=? AND channel_id=? AND user_id=? ORDER BY id DESC",
                key,
            ).fetchall()
            allowed: set[int] = set()
            size = 0
            for index in range(0, len(rows) - 1, 2):
                assistant, user = rows[index], rows[index + 1]
                if assistant[1] != "assistant" or user[1] != "user":
                    continue
                pair_size = len(assistant[2]) + len(user[2])
                if (
                    len(allowed) + 2 > max_messages
                    or pair_size > max_chars
                    or (allowed and size + pair_size > max_chars)
                ):
                    break
                allowed.update((assistant[0], user[0]))
                size += pair_size
            if rows:
                placeholders = ",".join("?" for _ in allowed)
                if allowed:
                    db.execute(
                        f"DELETE FROM chat_turns WHERE guild_id=? AND channel_id=? AND user_id=? "
                        f"AND id NOT IN ({placeholders})",
                        (*key, *allowed),
                    )
                else:
                    db.execute(
                        "DELETE FROM chat_turns WHERE guild_id=? AND channel_id=? AND user_id=?",
                        key,
                    )
            old_scopes = db.execute(
                "SELECT guild_id, channel_id, user_id FROM chat_turns "
                "GROUP BY guild_id, channel_id, user_id "
                "ORDER BY MAX(created_at) DESC, MAX(id) DESC LIMIT -1 OFFSET ?",
                (self.max_conversations,),
            ).fetchall()
            for old_scope in old_scopes:
                db.execute(
                    "DELETE FROM chat_turns WHERE guild_id=? AND channel_id=? AND user_id=?",
                    old_scope,
                )
            db.commit()

    def clear(self, key: tuple[int, int, int]) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "DELETE FROM chat_turns WHERE guild_id=? AND channel_id=? AND user_id=?", key
            )
            db.commit()

    def prune(self) -> None:
        with closing(self._connect()) as db, db:
            self._expire(db, time.time())
            db.commit()

    def close(self) -> None:
        """Connections are scoped to operations, so there is no long-lived handle."""
