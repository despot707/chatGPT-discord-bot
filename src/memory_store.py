"""Evidence-first long-term Discord memory.

Stores public guild messages as evidence. It deliberately does not assign personality
traits or certainty scores on write; interpretation happens at retrieval time so
contradictory evidence remains visible instead of being flattened into a false fact.
"""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

_WORD = re.compile(r"[a-z0-9][a-z0-9_'-]{2,}", re.I)
_STOP = {
    "the",
    "and",
    "that",
    "this",
    "with",
    "from",
    "have",
    "just",
    "your",
    "youre",
    "they",
    "them",
    "what",
    "when",
    "where",
    "which",
    "would",
    "could",
    "should",
    "about",
    "into",
    "like",
    "dont",
    "doesnt",
    "didnt",
    "wasnt",
    "were",
    "been",
    "being",
    "because",
    "really",
    "there",
    "their",
    "then",
    "than",
    "some",
    "more",
    "very",
    "will",
    "can",
    "for",
    "are",
    "but",
    "not",
    "its",
    "you",
}


class MemoryStore:
    def __init__(self, path: str) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS discord_messages (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            )""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS memory_user_time ON discord_messages(guild_id,user_id,created_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS memory_guild_time ON discord_messages(guild_id,created_at)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS birthday_signals (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                subject_user_id INTEGER NOT NULL,
                observer_user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL UNIQUE,
                month INTEGER NOT NULL,
                day INTEGER NOT NULL,
                created_at REAL NOT NULL
            )""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def record(
        self,
        *,
        message_id: int,
        guild_id: int,
        channel_id: int,
        user_id: int,
        display_name: str,
        content: str,
        created_at: float | None = None,
    ) -> None:
        text = (content or "").strip()[:4000]
        if not text:
            return
        stamp = float(created_at or time.time())
        with closing(self._connect()) as db, db:
            db.execute(
                """INSERT OR IGNORE INTO discord_messages
                (message_id,guild_id,channel_id,user_id,display_name,content,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (message_id, guild_id, channel_id, user_id, display_name[:100], text, stamp),
            )

    def record_birthday_signal(
        self,
        *,
        guild_id: int,
        subject_user_id: int,
        observer_user_id: int,
        message_id: int,
        month: int,
        day: int,
        created_at: float | None = None,
    ) -> None:
        if subject_user_id == observer_user_id:
            return
        with closing(self._connect()) as db, db:
            db.execute(
                """INSERT OR IGNORE INTO birthday_signals
                (guild_id,subject_user_id,observer_user_id,message_id,month,day,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    guild_id,
                    subject_user_id,
                    observer_user_id,
                    message_id,
                    month,
                    day,
                    float(created_at or time.time()),
                ),
            )

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP][:12]

    def relevant(self, guild_id: int, query: str, *, user_ids=(), limit: int = 12) -> list[tuple]:
        terms = self._terms(query)
        if not terms:
            return []
        clauses = []
        params: list[object] = [guild_id]
        for term in terms:
            clauses.append("lower(content) LIKE ?")
            params.append("%" + term + "%")
        user_clause = ""
        if user_ids:
            ids = [int(x) for x in user_ids if int(x) > 0]
            if ids:
                user_clause = " AND user_id IN (" + ",".join("?" for _ in ids) + ")"
                params.extend(ids)
        params.append(max(limit * 8, 40))
        sql = (
            "SELECT message_id,user_id,display_name,content,created_at FROM discord_messages "
            "WHERE guild_id=? AND ("
            + " OR ".join(clauses)
            + ")"
            + user_clause
            + " ORDER BY created_at DESC LIMIT ?"
        )
        with closing(self._connect()) as db:
            rows = db.execute(sql, params).fetchall()
        scored = []
        q = set(terms)
        for row in rows:
            words = set(self._terms(row[3]))
            score = len(q & words)
            if score:
                scored.append((score, *row))
        scored.sort(key=lambda r: (r[0], r[-1]), reverse=True)
        return [r[1:] for r in scored[:limit]]

    def birthday_summary(self, guild_id: int, user_ids=()) -> list[tuple]:
        params: list[object] = [guild_id]
        where = "guild_id=?"
        if user_ids:
            ids = [int(x) for x in user_ids if int(x) > 0]
            if ids:
                where += " AND subject_user_id IN (" + ",".join("?" for _ in ids) + ")"
                params.extend(ids)
        with closing(self._connect()) as db:
            return db.execute(
                f"""SELECT subject_user_id,month,day,
                COUNT(DISTINCT observer_user_id) observers, COUNT(*) signals,
                MIN(created_at),MAX(created_at)
                FROM birthday_signals WHERE {where}
                GROUP BY subject_user_id,month,day
                ORDER BY signals DESC LIMIT 20""",
                params,
            ).fetchall()

    def close(self) -> None:
        pass
