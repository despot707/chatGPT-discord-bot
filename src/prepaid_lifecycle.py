"""Measured storage reconciliation and a disclosed 48-hour over-quota grace.

Called only on a separately approved, single-writer paid deployment. Never used
in off/preview mode. Does not touch the game catalog, keys, payment records,
Discord messages, or other providers' logs. Native backups need a separate policy.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from src.prepaid import Ledger
from src.prepaid_runtime import STORAGE_LOCK

# Kind -> (table, expression for UTF-8 payload + fixed record overhead)
TABLES = {
    "profiles": (("member_settings", "length(CAST(payload AS BLOB))+256"),),
    "chat": (("chat_turns", "length(CAST(content AS BLOB))+256"),),
    "gaming": (
        ("party_players", "length(CAST(display_name AS BLOB))+length(CAST(role AS BLOB))+256"),
        ("steam_links", "length(CAST(display_name AS BLOB))+256"),
    ),
}


def measured(paths: dict[str, str]) -> dict[tuple[int, str], int]:
    sizes: dict[tuple[int, str], int] = {}
    for kind, path in paths.items():
        if kind not in TABLES:
            raise ValueError("Unknown customer database")
        if not path or not Path(path).is_file():
            continue
        with closing(sqlite3.connect(path, timeout=5)) as db:
            for table, expression in TABLES[kind]:
                exists = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not exists:
                    continue
                for guild, size in db.execute(
                    f"SELECT guild_id,SUM({expression}) FROM {table} GROUP BY guild_id"
                ):
                    sizes[(guild, kind)] = sizes.get((guild, kind), 0) + size
    return sizes


def reconcile(ledger: Ledger, paths: dict[str, str]):
    sizes = measured(paths)
    # Operator maintenance may record pre-existing over-quota data; it does not
    # create a storage entitlement or permit new writes. Public callers never
    # reach this method. Reconcile after actual DB commits while writers paused.
    with ledger.db() as db:
        for kind in paths:
            db.execute("DELETE FROM prepaid_storage WHERE object_key=?", (kind,))
        for (guild, kind), size in sizes.items():
            db.execute("INSERT INTO prepaid_storage VALUES(?,?,?)", (guild, kind, size))
    return sizes


def maintain_storage(ledger: Ledger, paths: dict[str, str], *, now: int) -> dict:
    """Prune oldest records only after a persistently observed 48-hour excess.

    All same-process writers hold STORAGE_LOCK. Process lease prevents a second
    bot process using these SQLite files in commercial mode. No unbounded archive.
    """
    with STORAGE_LOCK:
        sizes = reconcile(ledger, paths)
        guilds = {g for g, _ in sizes}
        removed = 0
        for guild in guilds:
            summary = ledger.summary(guild)
            limit = summary["storage_limit"]
            used = sum(s for (g, _), s in sizes.items() if g == guild)
            key = f"overquota:{guild}"
            with ledger.db() as db:
                if used <= limit:
                    db.execute("DELETE FROM prepaid_state WHERE key=?", (key,))
                    continue
                row = db.execute("SELECT value FROM prepaid_state WHERE key=?", (key,)).fetchone()
                if not row:
                    db.execute("INSERT INTO prepaid_state VALUES(?,?)", (key, str(now)))
                    continue
                if now - int(row[0]) < 48 * 3600:
                    continue
            # Prefer temporary conversation/party records over deliberate profile
            # settings. Small bounded deletes avoid holding locks indefinitely.
            for kind in ("chat", "gaming", "profiles"):
                path = paths.get(kind, "")
                if not path or not Path(path).is_file():
                    continue
                with closing(sqlite3.connect(path, timeout=5)) as db:
                    db.execute("PRAGMA secure_delete=ON")
                    for table, expression in TABLES[kind]:
                        if used <= limit:
                            break
                        if not db.execute(
                            "SELECT 1 FROM sqlite_master WHERE name=?", (table,)
                        ).fetchone():
                            continue
                        rows = db.execute(
                            f"SELECT rowid,{expression} FROM {table} WHERE guild_id=? ORDER BY rowid LIMIT 2000",
                            (guild,),
                        ).fetchall()
                        chosen = []
                        for rid, size in rows:
                            if used <= limit:
                                break
                            chosen.append((rid,))
                            used -= size
                        if chosen:
                            db.executemany(f"DELETE FROM {table} WHERE rowid=?", chosen)
                            removed += len(chosen)
                    db.commit()
                    # Reclaim released pages, rather than billing allocated files
                    # indefinitely. Paid deployment must own these files alone.
                    if removed:
                        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        db.execute("VACUUM")
        reconcile(ledger, paths)
        return {"servers_checked": len(guilds), "records_removed": removed}
