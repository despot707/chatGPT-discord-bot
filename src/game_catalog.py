"""Local, searchable public game catalog. Member selections use stable IDs."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

_ID = re.compile(r"wikidata:Q[1-9][0-9]*\Z")
FEATURED = [
    "League of Legends",
    "Valorant",
    "Minecraft",
    "Fortnite",
    "Helldivers 2",
    "Counter-Strike 2",
    "World of Warcraft",
    "Rocket League",
    "Apex Legends",
    "Overwatch 2",
    "Baldur's Gate 3",
    "Grand Theft Auto V",
    "Elden Ring",
    "Lethal Company",
    "Left 4 Dead 2",
    "Palworld",
]


def normalized(value: str) -> str:
    return " ".join(
        re.findall(
            r"\w+",
            "".join(
                c
                for c in unicodedata.normalize("NFKD", value.casefold())
                if not unicodedata.combining(c)
            ),
        )
    )


class GameCatalog:
    def __init__(self, path: str = "data/catalog.sqlite3", source: str | None = None):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        src = (
            Path(source)
            if source
            else Path(__file__).resolve().parent.parent / "assets/game_catalog.json.gz"
        )
        packed = src.read_bytes()
        version = hashlib.sha256(packed).hexdigest()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            previous = db.execute("SELECT value FROM catalog_meta WHERE key='version'").fetchone()
            if previous is None or previous[0] != version:
                rows = json.loads(gzip.decompress(packed))
                db.execute("DROP TABLE IF EXISTS catalog_search")
                db.execute("DROP TABLE IF EXISTS catalog_games")
                db.execute(
                    "CREATE TABLE catalog_games (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, priority INTEGER NOT NULL)"
                )
                db.execute(
                    "CREATE VIRTUAL TABLE catalog_search USING fts5(id UNINDEXED, name, aliases, tokenize='unicode61 remove_diacritics 2')"
                )
                for r in rows:
                    if (
                        not _ID.fullmatch(r["id"])
                        or not isinstance(r["name"], str)
                        or not 1 <= len(r["name"]) <= 200
                    ):
                        raise ValueError("Invalid catalog record")
                    name = r["name"]
                    words = normalized(name).split()
                    aliases = [str(x)[:200] for x in r.get("aliases", [])[:8]]
                    if len(words) > 1:
                        aliases.append("".join(x[0] for x in words))
                    priority = FEATURED.index(name) if name in FEATURED else 999
                    db.execute(
                        "INSERT INTO catalog_games VALUES (?,?,?,?)",
                        (r["id"], name, r.get("description", "")[:180], priority),
                    )
                    db.execute(
                        "INSERT INTO catalog_search VALUES (?,?,?)",
                        (r["id"], name, " ".join(aliases)),
                    )
                db.execute("INSERT OR REPLACE INTO catalog_meta VALUES (?,?)", ("version", version))
            self.count = db.execute("SELECT count(*) FROM catalog_games").fetchone()[0]

    def _connect(self):
        return sqlite3.connect(self.path, timeout=3)

    @staticmethod
    def _row(row):
        return {"id": row[0], "name": row[1], "description": row[2]} if row else None

    def get(self, game_id: str):
        if not isinstance(game_id, str) or not _ID.fullmatch(game_id):
            return None
        with closing(self._connect()) as db:
            return self._row(
                db.execute(
                    "SELECT id,name,description FROM catalog_games WHERE id=?", (game_id,)
                ).fetchone()
            )

    def search(self, query: str, *, limit: int = 25):
        if not isinstance(query, str) or len(query) > 100:
            return []
        words = normalized(query).split()[:8]
        limit = max(1, min(25, int(limit)))
        with closing(self._connect()) as db:
            if not words:
                rows = db.execute(
                    "SELECT id,name,description FROM catalog_games ORDER BY priority,name LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                expression = " AND ".join('"' + w + '"*' for w in words)
                rows = db.execute(
                    """SELECT g.id,g.name,g.description FROM catalog_search s
                    JOIN catalog_games g ON g.id=s.id WHERE catalog_search MATCH ?
                    ORDER BY CASE WHEN lower(g.name)=? THEN 0 ELSE 1 END,g.priority,bm25(catalog_search),g.name LIMIT ?""",
                    (expression, query.strip().casefold(), limit),
                ).fetchall()
        return [self._row(r) for r in rows]
