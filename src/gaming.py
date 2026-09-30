"""Persistent party and Steam-link storage plus small-party team balancing."""

from __future__ import annotations

import random
import sqlite3
import threading
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

from src.prepaid_runtime import finish_table, limit_database, reserve_table, storage_transaction


class GamingError(RuntimeError):
    """A safe, user-presentable gaming storage or input error."""


_Result = TypeVar("_Result")


def _serialized(method: Callable[..., _Result]) -> Callable[..., _Result]:
    @wraps(method)
    def wrapper(store: GamingStore, *args: Any, **kwargs: Any) -> _Result:
        with store._lock:
            return method(store, *args, **kwargs)

    return wrapper


@dataclass(frozen=True)
class PartyPlayer:
    user_id: int
    display_name: str
    skill: int = 5
    role: str = "any"


@dataclass(frozen=True)
class SteamLink:
    steam_id: str
    display_name: str


def _validate_id(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
        raise GamingError(f"{label} must be a positive 64-bit integer.")


def _validate_text(value: str, label: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GamingError(f"{label} must contain 1 to {maximum} characters.")


def _validate_player(player: PartyPlayer) -> None:
    if not isinstance(player, PartyPlayer):
        raise GamingError("Player data is invalid.")
    _validate_id(player.user_id, "User ID")
    _validate_text(player.display_name, "Display name", 80)
    if (
        isinstance(player.skill, bool)
        or not isinstance(player.skill, int)
        or not 1 <= player.skill <= 10
    ):
        raise GamingError("Skill rating must be an integer from 1 to 10.")
    _validate_text(player.role, "Role", 24)


class GamingStore:
    """Synchronous SQLite store; opens and initializes its database on first use."""

    def __init__(self, path: str) -> None:
        if not isinstance(path, str) or not path:
            raise GamingError("Database path is invalid.")
        self.path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            try:
                if self.path != ":memory:":
                    expanded_path = Path(self.path).expanduser()
                    expanded_path.parent.mkdir(parents=True, exist_ok=True)
                    database_path = str(expanded_path)
                else:
                    database_path = self.path
                connection = sqlite3.connect(database_path, timeout=5.0, check_same_thread=False)
                connection.execute("PRAGMA busy_timeout = 5000")
                connection.execute("PRAGMA journal_mode = WAL")
                limit_database(connection)
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS steam_links (
                        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                        steam_id TEXT NOT NULL, display_name TEXT NOT NULL,
                        PRIMARY KEY (guild_id, user_id)
                    );
                    CREATE TABLE IF NOT EXISTS party_players (
                        guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL, display_name TEXT NOT NULL,
                        skill INTEGER NOT NULL, role TEXT NOT NULL,
                        PRIMARY KEY (guild_id, channel_id, user_id)
                    );
                    """
                )
                self._connection = connection
            except (OSError, sqlite3.Error) as exc:
                raise GamingError("Could not initialize gaming storage.") from exc
        return self._connection

    @_serialized
    @storage_transaction
    def link_steam(self, guild_id: int, user_id: int, steam_id: str, display_name: str) -> None:
        _validate_id(guild_id, "Guild ID")
        _validate_id(user_id, "User ID")
        if (
            not isinstance(steam_id, str)
            or len(steam_id) != 17
            or not steam_id.isascii()
            or not steam_id.isdigit()
        ):
            raise GamingError("Steam ID must contain exactly 17 digits.")
        _validate_text(display_name, "Display name", 100)
        try:
            self._db().execute(
                "INSERT INTO steam_links VALUES (?, ?, ?, ?) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
                "steam_id=excluded.steam_id, display_name=excluded.display_name",
                (guild_id, user_id, steam_id.strip(), display_name.strip()),
            )
            ticket = reserve_table(self._db(), guild_id, "gaming")
            self._db().commit()
            finish_table(ticket)
        except sqlite3.Error as exc:
            if self._connection is not None:
                self._connection.rollback()
            raise GamingError("Could not save Steam link.") from exc

    @_serialized
    @storage_transaction
    def unlink_steam(self, guild_id: int, user_id: int) -> bool:
        _validate_id(guild_id, "Guild ID")
        _validate_id(user_id, "User ID")
        try:
            cursor = self._db().execute(
                "DELETE FROM steam_links WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            )
            ticket = reserve_table(self._db(), guild_id, "gaming")
            self._db().commit()
            finish_table(ticket)
            return cursor.rowcount > 0
        except sqlite3.Error as exc:
            if self._connection is not None:
                self._connection.rollback()
            raise GamingError("Could not remove Steam link.") from exc

    @_serialized
    def get_steam(self, guild_id: int, user_id: int) -> SteamLink | None:
        _validate_id(guild_id, "Guild ID")
        _validate_id(user_id, "User ID")
        try:
            row = (
                self._db()
                .execute(
                    "SELECT steam_id, display_name FROM steam_links WHERE guild_id=? AND user_id=?",
                    (guild_id, user_id),
                )
                .fetchone()
            )
            return SteamLink(*row) if row else None
        except sqlite3.Error as exc:
            raise GamingError("Could not read Steam link.") from exc

    @_serialized
    @storage_transaction
    def join_party(self, guild_id: int, channel_id: int, player: PartyPlayer) -> None:
        _validate_id(guild_id, "Guild ID")
        _validate_id(channel_id, "Channel ID")
        _validate_player(player)
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute(
                "SELECT 1 FROM party_players WHERE guild_id=? AND channel_id=? AND user_id=?",
                (guild_id, channel_id, player.user_id),
            ).fetchone()
            count = db.execute(
                "SELECT COUNT(*) FROM party_players WHERE guild_id=? AND channel_id=?",
                (guild_id, channel_id),
            ).fetchone()[0]
            if not exists and count >= 20:
                db.rollback()
                raise GamingError("This party is full (20 players maximum).")
            db.execute(
                "INSERT INTO party_players VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id, channel_id, user_id) DO UPDATE SET "
                "display_name=excluded.display_name, skill=excluded.skill, role=excluded.role",
                (
                    guild_id,
                    channel_id,
                    player.user_id,
                    player.display_name.strip(),
                    player.skill,
                    player.role.strip(),
                ),
            )
            ticket = reserve_table(db, guild_id, "gaming")
            db.commit()
            finish_table(ticket)
        except GamingError:
            raise
        except sqlite3.Error as exc:
            db.rollback()
            raise GamingError("Could not update party.") from exc

    @_serialized
    @storage_transaction
    def leave_party(self, guild_id: int, channel_id: int, user_id: int) -> bool:
        _validate_id(guild_id, "Guild ID")
        _validate_id(channel_id, "Channel ID")
        _validate_id(user_id, "User ID")
        try:
            cursor = self._db().execute(
                "DELETE FROM party_players WHERE guild_id=? AND channel_id=? AND user_id=?",
                (guild_id, channel_id, user_id),
            )
            ticket = reserve_table(self._db(), guild_id, "gaming")
            self._db().commit()
            finish_table(ticket)
            return cursor.rowcount > 0
        except sqlite3.Error as exc:
            if self._connection is not None:
                self._connection.rollback()
            raise GamingError("Could not update party.") from exc

    @_serialized
    def party(self, guild_id: int, channel_id: int) -> list[PartyPlayer]:
        _validate_id(guild_id, "Guild ID")
        _validate_id(channel_id, "Channel ID")
        try:
            rows = (
                self._db()
                .execute(
                    "SELECT user_id, display_name, skill, role FROM party_players "
                    "WHERE guild_id=? AND channel_id=? ORDER BY rowid",
                    (guild_id, channel_id),
                )
                .fetchall()
            )
            return [PartyPlayer(*row) for row in rows]
        except sqlite3.Error as exc:
            raise GamingError("Could not read party.") from exc

    @_serialized
    @storage_transaction
    def clear_party(self, guild_id: int, channel_id: int) -> None:
        _validate_id(guild_id, "Guild ID")
        _validate_id(channel_id, "Channel ID")
        try:
            self._db().execute(
                "DELETE FROM party_players WHERE guild_id=? AND channel_id=?",
                (guild_id, channel_id),
            )
            ticket = reserve_table(self._db(), guild_id, "gaming")
            self._db().commit()
            finish_table(ticket)
        except sqlite3.Error as exc:
            if self._connection is not None:
                self._connection.rollback()
            raise GamingError("Could not clear party.") from exc

    @_serialized
    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def make_teams(
    players: list[PartyPlayer],
    team_count: int = 2,
    *,
    balanced: bool = True,
    seed: int | None = None,
) -> list[list[PartyPlayer]]:
    """Split a party, using bounded greedy search to reduce skill/role clustering."""
    if not isinstance(players, list) or not 2 <= len(players) <= 20:
        raise GamingError("Team building requires 2 to 20 players.")
    if isinstance(team_count, bool) or not isinstance(team_count, int) or not 2 <= team_count <= 4:
        raise GamingError("Team count must be from 2 to 4.")
    if len(players) < team_count:
        raise GamingError("Each team must have at least one player.")
    for player in players:
        _validate_player(player)
    if len({p.user_id for p in players}) != len(players):
        raise GamingError("Player IDs must be unique.")
    if not isinstance(balanced, bool):
        raise GamingError("Balanced must be a boolean.")
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    sizes = [
        len(players) // team_count + (index < len(players) % team_count)
        for index in range(team_count)
    ]
    if not balanced:
        pool = list(players)
        rng.shuffle(pool)
        teams: list[list[PartyPlayer]] = [[] for _ in sizes]
        for index, player in enumerate(pool):
            teams[index % team_count].append(player)
        return teams

    def objective(teams: list[list[PartyPlayer]]) -> float:
        totals = [sum(p.skill for p in team) for team in teams]
        mean = sum(totals) / team_count
        skill_cost = sum((total - mean) ** 2 for total in totals)
        role_cost = 0.0
        for team in teams:
            counts: dict[str, int] = {}
            for player in team:
                role = player.role.casefold()
                if role != "any":
                    counts[role] = counts.get(role, 0) + 1
            role_cost += sum(max(0, count - 1) ** 2 for count in counts.values())
        return skill_cost + role_cost * 2.0

    best: list[list[PartyPlayer]] | None = None
    best_cost = float("inf")
    # A fixed, small restart budget keeps work bounded while escaping greedy ties.
    for _ in range(80):
        ordered = list(players)
        rng.shuffle(ordered)
        ordered.sort(key=lambda p: p.skill, reverse=True)
        teams = [[] for _ in sizes]
        for player in ordered:
            choices = [i for i in range(team_count) if len(teams[i]) < sizes[i]]
            rng.shuffle(choices)
            chosen = min(
                choices, key=lambda i: objective(teams[:i] + [teams[i] + [player]] + teams[i + 1 :])
            )
            teams[chosen].append(player)
        # Improve the complete assignment with size-preserving swaps.
        for _ in range(20):
            current = objective(teams)
            best_swap: tuple[int, int, int, int] | None = None
            best_swap_cost = current
            for left in range(team_count):
                for right in range(left + 1, team_count):
                    for left_index, left_player in enumerate(teams[left]):
                        for right_index, right_player in enumerate(teams[right]):
                            teams[left][left_index], teams[right][right_index] = (
                                right_player,
                                left_player,
                            )
                            swap_cost = objective(teams)
                            teams[left][left_index], teams[right][right_index] = (
                                left_player,
                                right_player,
                            )
                            if swap_cost < best_swap_cost:
                                best_swap = (left, left_index, right, right_index)
                                best_swap_cost = swap_cost
            if best_swap is None:
                break
            left, left_index, right, right_index = best_swap
            teams[left][left_index], teams[right][right_index] = (
                teams[right][right_index],
                teams[left][left_index],
            )
        cost = objective(teams)
        if cost < best_cost:
            best, best_cost = [team[:] for team in teams], cost
    assert best is not None
    return best
