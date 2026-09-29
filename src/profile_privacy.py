"""Explicit removal of attributable records in known live bot databases."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def erase_legacy_records(config, guild_id, user_id):
    failures = []
    paths = [getattr(config, 'memory_database_path', None),
             getattr(config, 'chat_database_path', None),
             getattr(config, 'gaming_database_path', None)]
    for path in dict.fromkeys(p for p in paths if p and p != ':memory:'):
        if not Path(path).exists():
            continue
        try:
            with closing(sqlite3.connect(path, timeout=5)) as db, db:
                db.execute('PRAGMA secure_delete=ON')
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'birthday_candidates' in tables:
                    db.execute('DELETE FROM birthday_candidates WHERE guild_id=? AND (subject_id=? OR observer_id=?)', (guild_id,user_id,user_id))
                    if 'member_aliases' in tables:
                        # Only unambiguous aliases can be attributed to this account.
                        aliases = [r[0] for r in db.execute('SELECT normalized FROM member_aliases WHERE guild_id=? GROUP BY normalized HAVING COUNT(DISTINCT user_id)=1 AND MAX(user_id)=?', (guild_id,user_id))]
                        for alias in aliases:
                            db.execute('DELETE FROM birthday_candidates WHERE guild_id=? AND lower(trim(target_alias))=?', (guild_id,alias))
                for table in ('member_identities', 'member_aliases', 'discord_messages', 'birthday_optouts', 'steam_links', 'party_players'):
                    if table in tables:
                        db.execute(f'DELETE FROM {table} WHERE guild_id=? AND user_id=?', (guild_id,user_id))
                if 'discord_messages' in tables:
                    db.execute('DELETE FROM discord_messages WHERE guild_id=? AND (content LIKE ? OR content LIKE ?)',
                               (guild_id, f'%<@{user_id}>%', f'%<@!{user_id}>%'))
                if 'birthday_signals' in tables:
                    db.execute('DELETE FROM birthday_signals WHERE guild_id=? AND (subject_user_id=? OR observer_user_id=?)', (guild_id,user_id,user_id))
                if 'chat_turns' in tables:
                    # Legacy shared turns have no author IDs. Explicit confirmation warns
                    # that all shared bot context is reset; other users' private turns remain.
                    db.execute('DELETE FROM chat_turns WHERE guild_id=? AND user_id IN (0,?)', (guild_id,user_id))
        except (OSError, sqlite3.Error):
            failures.append('live storage')
    if failures:
        raise ValueError('Your profile was cleared, but some older bot data could not be removed. Reopen Privacy and retry, or contact the bot operator.')
