"""Read-only, bounded access to Steam profiles and shared game libraries.

Steam Store appdetails is an undocumented public endpoint. Its category data is
best-effort and is used only to label multiplayer/co-op status, never capacity.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, TypeVar
from urllib.parse import urlsplit

import httpx

_API_ROOT = "https://api.steampowered.com"
_STORE_DETAILS = "https://store.steampowered.com/api/appdetails"
_STEAM_ID = re.compile(r"^[0-9]{17}$")
_VANITY = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_METADATA_CANDIDATES = 20
_MAX_STORE_METADATA_ENTRIES = 100
_MULTIPLAYER_CATEGORIES = {1, 9, 36, 37, 38, 39, 47, 48}
_COOP_CATEGORIES = {9, 37, 38, 48}


class SteamError(RuntimeError):
    """Safe, user-facing Steam failure without upstream response details."""


@dataclass(frozen=True)
class SteamGame:
    app_id: int
    name: str
    playtime_minutes: int = 0


@dataclass(frozen=True)
class SteamProfile:
    steam_id: str
    display_name: str


@dataclass(frozen=True)
class GameSuggestion:
    app_id: int
    name: str
    owner_count: int
    player_count: int
    total_playtime_minutes: int
    multiplayer: bool | None = None
    co_op: bool | None = None


T = TypeVar("T")


class _TTLCache(Generic[T]):
    def __init__(self, capacity: int, ttl_seconds: float) -> None:
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= time.monotonic():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    def put(self, key: str, value: T) -> None:
        self._items[key] = (time.monotonic() + self.ttl_seconds, value)
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)


class SteamService:
    """Steam Web API adapter with a fixed host, bounded responses, and small caches."""

    def __init__(self, api_key: str | None, *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key.strip() if api_key else None
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        self._requests = asyncio.Semaphore(4)
        self._profiles: _TTLCache[SteamProfile] = _TTLCache(256, 300)
        self._owned: _TTLCache[list[SteamGame]] = _TTLCache(128, 120)
        self._metadata: _TTLCache[tuple[dict[str, Any] | None, bool]] = _TTLCache(256, 3600)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> SteamService:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def resolve_profile(self, value: str) -> SteamProfile:
        """Resolve a SteamID64, community profile URL, or bare vanity name."""
        normalized = value.strip() if isinstance(value, str) else ""
        cached = self._profiles.get(normalized.casefold())
        if cached is not None:
            return cached
        steam_id: str
        vanity: str | None
        if _STEAM_ID.fullmatch(normalized):
            steam_id, vanity = normalized, None
        else:
            vanity = self._profile_input_vanity(normalized)
            if _STEAM_ID.fullmatch(vanity):
                steam_id = vanity
            else:
                payload = await self._get_json(
                    f"{_API_ROOT}/ISteamUser/ResolveVanityURL/v1/",
                    params={"vanityurl": vanity},
                    require_key=True,
                )
                response = payload.get("response") if isinstance(payload, dict) else None
                steam_id = str(response.get("steamid", "")) if isinstance(response, dict) else ""
                if (
                    not isinstance(response, dict)
                    or response.get("success") != 1
                    or not _STEAM_ID.fullmatch(steam_id)
                ):
                    raise SteamError("That Steam profile could not be found.")

        payload = await self._get_json(
            f"{_API_ROOT}/ISteamUser/GetPlayerSummaries/v2/",
            params={"steamids": steam_id},
            require_key=True,
        )
        response = payload.get("response") if isinstance(payload, dict) else None
        players = response.get("players") if isinstance(response, dict) else None
        if not isinstance(players, list) or not players or not isinstance(players[0], dict):
            raise SteamError("That Steam profile is unavailable.")
        if players[0].get("steamid") != steam_id:
            raise SteamError("That Steam profile is unavailable.")
        display_name = players[0].get("personaname")
        if not isinstance(display_name, str) or not display_name.strip():
            raise SteamError("That Steam profile is unavailable.")
        profile = SteamProfile(steam_id, display_name.strip()[:100])
        self._profiles.put(normalized.casefold(), profile)
        self._profiles.put(steam_id.casefold(), profile)
        return profile

    async def owned_games(self, steam_id: str) -> list[SteamGame]:
        """Return a visible library, preserving the distinction from a private one."""
        if not _STEAM_ID.fullmatch(steam_id):
            raise SteamError("A valid 17-digit Steam ID is required.")
        cached = self._owned.get(steam_id)
        if cached is not None:
            return list(cached)
        payload = await self._get_json(
            f"{_API_ROOT}/IPlayerService/GetOwnedGames/v1/",
            params={
                "input_json": json.dumps(
                    {
                        "steamid": steam_id,
                        "include_appinfo": True,
                        "include_played_free_games": True,
                    },
                    separators=(",", ":"),
                )
            },
            require_key=True,
        )
        response = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(response, dict) or "game_count" not in response:
            raise SteamError("This Steam profile's game library is private or unavailable.")
        game_count = response.get("game_count")
        if game_count is None:
            raise SteamError("This Steam profile's game library is private or unavailable.")
        if not isinstance(game_count, int) or game_count < 0:
            raise SteamError("Steam returned an invalid game library.")
        games_data = response.get("games")
        if game_count == 0:
            games: list[SteamGame] = []
        elif not isinstance(games_data, list):
            raise SteamError("Steam returned an invalid game library.")
        else:
            games = []
            for item in games_data:
                if not isinstance(item, dict):
                    continue
                app_id = item.get("appid")
                if not isinstance(app_id, int) or app_id <= 0:
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name.strip():
                    name = f"App {app_id}"
                playtime = item.get("playtime_forever", 0)
                if not isinstance(playtime, int) or playtime < 0:
                    playtime = 0
                games.append(SteamGame(app_id, name.strip()[:160], playtime))
        self._owned.put(steam_id, games)
        return list(games)

    async def suggest(
        self,
        steam_ids: list[str],
        *,
        mode: str = "all",
        multiplayer_only: bool = True,
        limit: int = 5,
    ) -> list[GameSuggestion]:
        """Rank shared games by intersection or library coverage and shared playtime.

        Store type/category metadata is checked for only the top 20 ranked library
        candidates. A result list may therefore be shorter than ``limit`` when
        those candidates are confirmed single-player, unavailable in the Store,
        or not game products; titles ranked below the first 20 are not examined.
        Unknown multiplayer status is retained as ``None``.
        """
        if mode not in {"all", "most"}:
            raise SteamError("Choose 'all' or 'most' for the shared-library mode.")
        if not 1 <= limit <= 20:
            raise SteamError("The suggestion limit must be between 1 and 20.")
        if not steam_ids or len(steam_ids) > 20:
            raise SteamError("Add between 1 and 20 Steam profiles.")
        if any(
            not isinstance(steam_id, str) or not _STEAM_ID.fullmatch(steam_id)
            for steam_id in steam_ids
        ):
            raise SteamError("Every shared-library entry must be a 17-digit Steam ID.")
        if len(set(steam_ids)) != len(steam_ids):
            raise SteamError("Each Steam profile can only be included once.")

        # Fail the whole request if any library is private; silently omitting an
        # account would inflate shared ownership and distort the ranking.
        library_tasks = [asyncio.create_task(self.owned_games(steam_id)) for steam_id in steam_ids]
        try:
            libraries = list(await asyncio.gather(*library_tasks))
        except BaseException:
            for task in library_tasks:
                task.cancel()
            await asyncio.gather(*library_tasks, return_exceptions=True)
            raise
        by_owner = [{game.app_id: game for game in games} for games in libraries]
        owner_counts: dict[int, int] = {}
        total_playtime: dict[int, int] = {}
        names: dict[int, str] = {}
        for library in by_owner:
            for app_id, game in library.items():
                owner_counts[app_id] = owner_counts.get(app_id, 0) + 1
                total_playtime[app_id] = total_playtime.get(app_id, 0) + game.playtime_minutes
                names.setdefault(app_id, game.name)

        if mode == "all":
            candidates = [
                app_id for app_id, count in owner_counts.items() if count == len(steam_ids)
            ]
        else:
            candidates = list(owner_counts)
        candidates.sort(
            key=lambda app_id: (
                -owner_counts[app_id],
                -total_playtime[app_id],
                names[app_id].casefold(),
                app_id,
            )
        )
        # Bound metadata fanout independently from the requested output size.
        metadata_candidates = candidates[:_MAX_METADATA_CANDIDATES]
        metadata_tasks: list[asyncio.Task[tuple[dict[str, Any] | None, bool] | None]] = []
        async with asyncio.TaskGroup() as group:
            metadata_tasks = [
                group.create_task(self._app_metadata(app_id)) for app_id in metadata_candidates
            ]
        metadata = [task.result() for task in metadata_tasks]
        suggestions: list[GameSuggestion] = []
        for app_id, metadata_result in zip(metadata_candidates, metadata):
            # None means Store metadata was unavailable; use the library name
            # and leave category claims unknown. The bool marks definite non-games.
            if metadata_result is None:
                details = None
            else:
                details, is_non_game = metadata_result
                if is_non_game:
                    continue
            if details is None:
                multiplayer, co_op = None, None
                raw_name: Any = names[app_id]
            else:
                multiplayer, co_op = _classify_categories(details.get("categories"))
                raw_name = details.get("name")
            if multiplayer_only and multiplayer is False:
                continue
            name = (
                raw_name.strip()[:160]
                if isinstance(raw_name, str) and raw_name.strip()
                else names[app_id]
            )
            suggestions.append(
                GameSuggestion(
                    app_id=app_id,
                    name=name,
                    owner_count=owner_counts[app_id],
                    player_count=len(steam_ids),
                    total_playtime_minutes=total_playtime[app_id],
                    multiplayer=multiplayer,
                    co_op=co_op,
                )
            )
            if len(suggestions) >= limit:
                break
        return suggestions

    async def _app_metadata(self, app_id: int) -> tuple[dict[str, Any] | None, bool] | None:
        cached = self._metadata.get(str(app_id))
        if cached is not None:
            return cached
        try:
            payload = await self._get_json(_STORE_DETAILS, params={"appids": str(app_id)})
            entry = None
            if isinstance(payload, dict):
                requested_key = str(app_id)
                if requested_key in payload:
                    entry = payload[requested_key]
                    keyed_data = entry.get("data") if isinstance(entry, dict) else None
                    keyed_inner_id = (
                        keyed_data.get("steam_appid") if isinstance(keyed_data, dict) else None
                    )
                    if keyed_inner_id is not None and (
                        type(keyed_inner_id) is not int or keyed_inner_id != app_id
                    ):
                        result = (None, False)
                        self._metadata.put(str(app_id), result)
                        return result
                elif len(payload) <= _MAX_STORE_METADATA_ENTRIES:
                    matching_entries = []
                    for candidate in payload.values():
                        candidate_data = (
                            candidate.get("data") if isinstance(candidate, dict) else None
                        )
                        inner_id = (
                            candidate_data.get("steam_appid")
                            if isinstance(candidate_data, dict)
                            else None
                        )
                        if type(inner_id) is int and inner_id == app_id:
                            matching_entries.append(candidate)
                    if len(matching_entries) == 1:
                        entry = matching_entries[0]
            if not isinstance(entry, dict) or entry.get("success") is not True:
                result = (None, False)
                self._metadata.put(str(app_id), result)
                return result
            data = entry.get("data")
            if not isinstance(data, dict):
                result = (None, False)
                self._metadata.put(str(app_id), result)
                return result
            product_type = data.get("type")
            if product_type != "game":
                result = (None, isinstance(product_type, str) and bool(product_type.strip()))
                self._metadata.put(str(app_id), result)
                return result
            categories = data.get("categories")
            category_ids = (
                [
                    {"id": item["id"]}
                    for item in categories
                    if isinstance(item, dict) and type(item.get("id")) is int
                ]
                if isinstance(categories, list)
                else []
            )
            slim = {
                "name": data.get("name") if isinstance(data.get("name"), str) else "",
                "categories": category_ids,
            }
            slim_result: tuple[dict[str, Any] | None, bool] = (slim, False)
            self._metadata.put(str(app_id), slim_result)
            return slim_result
        except SteamError:
            # Store metadata is best effort. A transient store failure must not
            # turn a valid, public Web API library into a failed suggestion.
            return None

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        require_key: bool = False,
    ) -> Any:
        if require_key and not self.api_key:
            raise SteamError(
                "Steam profile lookup is unavailable because no API key is configured."
            )
        headers = {"x-webapi-key": self.api_key} if require_key and self.api_key else None
        try:
            async with self._requests:
                async with self._client.stream(
                    "GET", url, params=params, headers=headers
                ) as response:
                    if response.status_code != 200:
                        raise SteamError(
                            "Steam is temporarily unavailable. Please try again later."
                        )
                    length = response.headers.get("content-length")
                    if length:
                        try:
                            if int(length) > _MAX_RESPONSE_BYTES:
                                raise SteamError("Steam returned a response that was too large.")
                        except ValueError:
                            pass
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > _MAX_RESPONSE_BYTES:
                            raise SteamError("Steam returned a response that was too large.")
            return json.loads(body)
        except SteamError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
            raise SteamError("Steam is temporarily unavailable. Please try again later.") from None

    @staticmethod
    def _profile_input_vanity(value: str) -> str:
        if not value:
            raise SteamError("Enter a SteamID64, profile URL, or vanity name.")
        candidate = value
        if "://" in value or "/" in value:
            try:
                parsed = urlsplit(value)
                if (
                    parsed.scheme.lower() != "https"
                    or parsed.netloc.lower() != "steamcommunity.com"
                    or parsed.query
                    or parsed.fragment
                    or parsed.username
                    or parsed.password
                ):
                    raise ValueError
                parts = parsed.path.strip("/").split("/")
                if len(parts) != 2:
                    raise ValueError
                if parts[0] == "id" and _VANITY.fullmatch(parts[1]):
                    candidate = parts[1]
                elif parts[0] == "profiles" and _STEAM_ID.fullmatch(parts[1]):
                    candidate = parts[1]
                else:
                    raise ValueError
            except (ValueError, UnicodeError):
                raise SteamError(
                    "Use a Steam profile URL or vanity name in the supported format."
                ) from None
        if not _VANITY.fullmatch(candidate):
            raise SteamError("Enter a valid Steam profile vanity name.")
        return candidate


def _classify_categories(categories: Any) -> tuple[bool | None, bool | None]:
    if not isinstance(categories, list):
        return None, None
    ids = {
        item.get("id")
        for item in categories
        if isinstance(item, dict) and type(item.get("id")) is int
    }
    if not ids:
        return None, None
    multiplayer = bool(ids & _MULTIPLAYER_CATEGORIES)
    if not multiplayer and 2 not in ids:
        return None, None
    return multiplayer, bool(ids & _COOP_CATEGORIES)
