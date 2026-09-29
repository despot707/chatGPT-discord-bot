"""Explicit, member-controlled settings. No inferred traits or passive collection."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class StaleProfile(ValueError):
    """A newer save or deletion has invalidated this form."""


def empty_profile(revision: int = 0) -> dict:
    return {"revision": revision, "birthday": None, "games": [], "preferences": {}}


def _text(value, label, maximum, *, optional=False):
    if not isinstance(value, str) or len(value) > maximum or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError(f"{label} must be plain text, up to {maximum} characters.")
    value = value.strip()
    if not value and not optional:
        raise ValueError(f"Enter {label.lower()}.")
    return value


def _visibility(data):
    value = data.get("visibility", "private")
    if value not in ("private", "server"):
        raise ValueError("Choose Only me or This server.")
    return value


def validate_change(operation: str, data: dict) -> dict:
    """Reject unknown fields; never accept identity/authorization from model output."""
    schemas = {
        "birthday": {"month", "day", "visibility"},
        "game": {"name", "role", "style", "visibility", "catalog_id"},
        "remove_game": {"name", "catalog_id"},
        "preferences": {"timezone", "availability", "game_types", "visibility"},
        "remove_birthday": set(),
        "hide_all": set(),
        "forget": set(),
    }
    if operation not in schemas or not isinstance(data, dict) or set(data) - schemas[operation]:
        raise ValueError("That setting is not supported. Open /profile to choose a field.")
    if operation == "birthday":
        month, day = data.get("month"), data.get("day")
        if type(month) is not int or type(day) is not int:
            raise ValueError("Choose a month and day, without a year.")
        try:
            date(2000, month, day)
        except ValueError:
            raise ValueError("That day does not exist in the selected month.") from None
        return dict(month=month, day=day, visibility=_visibility(data))
    if operation in ("game", "remove_game"):
        catalog_id = data.get("catalog_id")
        if catalog_id is not None and (
            not isinstance(catalog_id, str)
            or not re.fullmatch(r"wikidata:Q[1-9][0-9]*", catalog_id)
        ):
            raise ValueError("Select a game from the catalog.")
        identity = {"catalog_id": catalog_id} if catalog_id is not None else {}
        name = _text(data.get("name"), "Game", 200 if catalog_id else 80)
        if operation == "remove_game":
            return dict(name=name, **identity)
        style = data.get("style", "both")
        if style not in ("casual", "competitive", "both"):
            raise ValueError("Choose casual, competitive, or both.")
        return dict(
            **identity,
            name=name,
            role=_text(data.get("role", ""), "Role", 60, optional=True),
            style=style,
            visibility=_visibility(data),
        )
    if operation == "preferences":
        zone = _text(data.get("timezone", ""), "Timezone", 80, optional=True)
        if zone:
            try:
                ZoneInfo(zone)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError("Choose a valid timezone, such as America/Los_Angeles.") from None
        allowed = {
            "availability": [
                "Mornings",
                "Afternoons",
                "Evenings",
                "Late nights",
                "Weekdays",
                "Weekends",
            ],
            "game_types": ["Co-op", "PvP", "Single-player", "MMO", "Survival", "Party games"],
        }
        out: dict[str, Any] = dict(timezone=zone, visibility=_visibility(data))
        for key, options in allowed.items():
            values = data.get(key, [])
            if (
                not isinstance(values, list)
                or len(values) > len(options)
                or any(v not in options for v in values)
            ):
                raise ValueError(f"Choose {key.replace('_', ' ')} from the available options.")
            out[key] = list(dict.fromkeys(values))
        return out
    return {}


def same_game(left, right):
    """Known IDs take priority; only exact legacy names may migrate on explicit save."""
    if left.get("catalog_id") and right.get("catalog_id"):
        return left["catalog_id"] == right["catalog_id"]
    return left["name"].casefold() == right["name"].casefold()


def visible_profile(profile: dict, *, owner: bool = False) -> dict:
    out = copy.deepcopy(profile)
    out.pop("revision", None)
    if owner:
        return out
    if (out.get("birthday") or {}).get("visibility") != "server":
        out["birthday"] = None
    out["games"] = [g for g in out.get("games", []) if g.get("visibility") == "server"]
    if out.get("preferences", {}).get("visibility") != "server":
        out["preferences"] = {}
    return out


class ProfileStore:
    """One transaction per save; revisions reject stale modals and duplicate submits."""

    def __init__(self, path: str):
        if not path or path == ":memory:":
            raise ValueError("ProfileStore requires a persistent file.")
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS member_settings (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY (guild_id,user_id))""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA secure_delete=ON")
        return db

    @staticmethod
    def _ids(guild_id, user_id):
        if any(type(v) is not int or not 0 < v < 2**63 for v in (guild_id, user_id)):
            raise ValueError("Use your own account in a Discord server.")

    @staticmethod
    def _read(db, guild_id, user_id):
        row = db.execute(
            "SELECT revision,payload FROM member_settings WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
        return dict(json.loads(row[1]), revision=row[0]) if row else empty_profile()

    def get(self, guild_id, user_id):
        self._ids(guild_id, user_id)
        with closing(self._connect()) as db:
            return self._read(db, guild_id, user_id)

    def apply(self, guild_id, user_id, operation, data, expected_revision):
        self._ids(guild_id, user_id)
        data = validate_change(operation, data)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = self._read(db, guild_id, user_id)
            if type(expected_revision) is not int or current["revision"] != expected_revision:
                raise StaleProfile("Your settings changed. Reopen /profile before saving again.")
            revision = current["revision"] + 1
            if operation == "birthday":
                current["birthday"] = data
            elif operation == "remove_birthday":
                current["birthday"] = None
            elif operation == "preferences":
                current["preferences"] = data
            elif operation == "game":
                index = next(
                    (i for i, g in enumerate(current["games"]) if same_game(g, data)),
                    None,
                )
                if index is not None:
                    current["games"][index] = data
                else:
                    if len(current["games"]) >= 20:
                        raise ValueError(
                            "Your profile can hold 20 games. Remove one before adding another."
                        )
                    current["games"].append(data)
            elif operation == "remove_game":
                current["games"] = [g for g in current["games"] if not same_game(g, data)]
            elif operation == "hide_all":
                for item in [current["birthday"], current["preferences"], *current["games"]]:
                    if item:
                        item["visibility"] = "private"
            elif operation == "forget":
                current = empty_profile()
            current["revision"] = revision
            payload = {k: v for k, v in current.items() if k != "revision"}
            db.execute(
                """INSERT INTO member_settings VALUES (?,?,?,?,?)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET revision=excluded.revision,
                payload=excluded.payload,updated_at=excluded.updated_at""",
                (guild_id, user_id, revision, json.dumps(payload, ensure_ascii=False), time.time()),
            )
        return current

    def shared(self, guild_id, *, birthday_only=False, limit=25):
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT user_id,payload FROM member_settings WHERE guild_id=? ORDER BY user_id",
                (guild_id,),
            )
            result = []
            for uid, payload in rows:
                p = visible_profile(json.loads(payload))
                if (
                    p["birthday"]
                    if birthday_only
                    else p["birthday"] or p["games"] or p["preferences"]
                ):
                    result.append((uid, p))
                if len(result) >= limit:
                    break
            return result

    def players_for(self, guild_id, catalog_id, *, limit=20):
        """Only opted-in games from this server; no fuzzy identity matching."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT user_id,payload FROM member_settings WHERE guild_id=?", (guild_id,)
            )
            matches = []
            for uid, payload in rows:
                for game in json.loads(payload).get("games", []):
                    if game.get("catalog_id") == catalog_id and game.get("visibility") == "server":
                        matches.append((uid, game))
                        break
                if len(matches) >= min(20, limit):
                    break
            return matches

    def close(self):
        pass
