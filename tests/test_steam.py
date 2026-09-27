from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

import httpx
import pytest
from src.steam import GameSuggestion, SteamError, SteamGame, SteamService, _classify_categories

STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"


def _json(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _owned(*games: tuple[int, str, int]) -> dict[str, Any]:
    return {
        "response": {
            "game_count": len(games),
            "games": [
                {"appid": app_id, "name": name, "playtime_forever": minutes}
                for app_id, name, minutes in games
            ],
        }
    }


@pytest.mark.asyncio
async def test_resolve_profile_rejects_unsupported_urls_without_network() -> None:
    service = SteamService(
        "test-key",
        transport=httpx.MockTransport(lambda _request: pytest.fail("unexpected request")),
    )
    try:
        for value in (
            "http://steamcommunity.com/id/name",
            "https://steamcommunity.com.evil.test/id/name",
            "https://steamcommunity.com/profiles/not-an-id",
            "https://steamcommunity.com/id/name?redirect=elsewhere",
            "https://example.com/id/name",
            "https://steamcommunity.com/groups/name",
            "not a vanity name",
        ):
            with pytest.raises(SteamError):
                await service.resolve_profile(value)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_resolve_profile_uses_fixed_endpoints_and_header_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("ResolveVanityURL/v1/"):
            return _json({"response": {"success": 1, "steamid": STEAM_A}})
        return _json({"response": {"players": [{"steamid": STEAM_A, "personaname": "Player One"}]}})

    service = SteamService("secret-test-key", transport=httpx.MockTransport(handler))
    try:
        profile = await service.resolve_profile("https://steamcommunity.com/id/playerone")
        assert profile.steam_id == STEAM_A
        assert profile.display_name == "Player One"
        assert len(seen) == 2
        assert all(request.url.host == "api.steampowered.com" for request in seen)
        assert all(request.headers["x-webapi-key"] == "secret-test-key" for request in seen)
        assert all("secret-test-key" not in str(request.url) for request in seen)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_owned_games_sends_documented_options_and_distinguishes_private_empty() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        data = json.loads(request.url.params["input_json"])
        assert data["include_appinfo"] is True
        assert data["include_played_free_games"] is True
        if data["steamid"] == STEAM_A:
            return _json(
                {
                    "response": {
                        "game_count": 1,
                        "games": [{"appid": 10, "name": "A", "playtime_forever": 45}],
                    }
                }
            )
        assert data["steamid"] == STEAM_B
        return _json({"response": {"game_count": 0, "games": []}})

    service = SteamService("key", transport=httpx.MockTransport(handler))
    try:
        assert await service.owned_games(STEAM_A) == [SteamGame(10, "A", 45)]
        assert await service.owned_games(STEAM_B) == []
        assert seen[0].headers["x-webapi-key"] == "key"
    finally:
        await service.close()

    for payload in ({"response": {}}, {"response": {"game_count": None}}):
        private_service = SteamService(
            "key", transport=httpx.MockTransport(lambda _request, body=payload: _json(body))
        )
        try:
            with pytest.raises(SteamError, match="private or unavailable"):
                await private_service.owned_games(STEAM_A)
        finally:
            await private_service.close()

    malformed = SteamService(
        "key",
        transport=httpx.MockTransport(lambda _request: _json({"response": {"game_count": 1}})),
    )
    try:
        with pytest.raises(SteamError, match="invalid game library"):
            await malformed.owned_games(STEAM_A)
    finally:
        await malformed.close()


@pytest.mark.asyncio
async def test_suggest_all_intersects_and_most_ranks_coverage_then_playtime() -> None:
    libraries = {
        STEAM_A: _owned((1, "Shared", 100), (2, "Often", 300), (3, "Rare", 500)),
        STEAM_B: _owned((1, "Shared", 200), (2, "Often", 100)),
    }
    calls: defaultdict[int, int] = defaultdict(int)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.steampowered.com":
            payload = json.loads(request.url.params["input_json"])
            return _json(libraries[payload["steamid"]])
        app_id = int(request.url.params["appids"])
        assert "x-webapi-key" not in request.headers
        calls[app_id] += 1
        return _json(
            {
                str(app_id): {
                    "success": True,
                    "data": {
                        "name": f"Game {app_id}",
                        "type": "game",
                        "categories": [{"id": 1, "description": "Multi-player"}],
                    },
                }
            }
        )

    service = SteamService("key", transport=httpx.MockTransport(handler))
    try:
        all_games = await service.suggest([STEAM_A, STEAM_B], mode="all")
        assert [game.app_id for game in all_games] == [2, 1]
        assert all(game.owner_count == 2 and game.player_count == 2 for game in all_games)
        assert all(game.multiplayer is True for game in all_games)

        most_games = await service.suggest([STEAM_A, STEAM_B], mode="most")
        assert [game.app_id for game in most_games] == [2, 1, 3]
        assert [game.owner_count for game in most_games] == [2, 2, 1]
        assert [game.total_playtime_minutes for game in most_games] == [400, 300, 500]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_suggest_rejects_duplicate_ids_and_does_not_hide_private_library() -> None:
    service = SteamService(
        "key",
        transport=httpx.MockTransport(lambda _request: _json({"response": {}})),
    )
    try:
        with pytest.raises(SteamError, match="once"):
            await service.suggest([STEAM_A, STEAM_A])
        with pytest.raises(SteamError, match="private or unavailable"):
            await service.suggest([STEAM_A, STEAM_B])
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_private_library_failure_cancels_and_joins_sibling_requests() -> None:
    sibling_cancelled = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.url.params["input_json"])
        if data["steamid"] == STEAM_B:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise
        return _json({"response": {}})

    service = SteamService("key", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SteamError, match="private or unavailable"):
            await service.suggest([STEAM_A, STEAM_B])
        assert sibling_cancelled.is_set()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_upstream_errors_are_sanitized() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            302, headers={"Location": "https://evil.test/steal"}, text="secret provider detail"
        )

    service = SteamService("key", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SteamError, match="temporarily unavailable") as error:
            await service.owned_games(STEAM_A)
        assert "secret provider" not in str(error.value)
        assert len(calls) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_multiplayer_filter_excludes_confirmed_single_player_and_unknown_is_explicit() -> (
    None
):
    library = _owned((10, "Solo", 200), (11, "Unknown", 150), (12, "Multi", 50), (13, "DLC", 25))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.steampowered.com":
            return _json(library)
        app_id = int(request.url.params["appids"])
        data = {
            10: {"name": "Solo", "type": "game", "categories": [{"id": 2}]},
            11: {"name": "Unknown", "type": "game", "categories": []},
            12: {"name": "Multi", "type": "game", "categories": [{"id": 1}]},
            13: {"name": "DLC", "type": "dlc", "categories": [{"id": 1}]},
        }[app_id]
        return _json({str(app_id): {"success": True, "data": data}})

    service = SteamService("key", transport=httpx.MockTransport(handler))
    try:
        results = await service.suggest([STEAM_A], mode="most", limit=10)
        assert [result.app_id for result in results] == [11, 12]
        unknown = next(result for result in results if result.app_id == 11)
        assert unknown.multiplayer is None
        assert unknown.co_op is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_store_failure_keeps_library_candidate_with_unknown_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.steampowered.com":
            return _json(_owned((20, "Local library title", 15)))
        assert "x-webapi-key" not in request.headers
        return httpx.Response(503, text="upstream secret")

    service = SteamService("key", transport=httpx.MockTransport(handler))
    try:
        results = await service.suggest([STEAM_A], mode="most")
        assert results == [GameSuggestion(20, "Local library title", 1, 1, 15, None, None)]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_store_metadata_fallback_requires_unique_matching_inner_app_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.steampowered.com":
            return _json(_owned((440, "Library TF2", 25)))
        assert request.url.params["appids"] == "440"
        return _json(
            {
                "629330": {
                    "success": True,
                    "data": {
                        "steam_appid": 440,
                        "name": "Team Fortress 2",
                        "type": "game",
                        "categories": [{"id": 2}, {"id": 1}, {"id": 9}, {"id": 38}, {"id": 39}],
                    },
                }
            }
        )

    service = SteamService("key", transport=httpx.MockTransport(handler))
    try:
        assert await service.suggest([STEAM_A], mode="most") == [
            GameSuggestion(440, "Team Fortress 2", 1, 1, 25, True, True)
        ]
    finally:
        await service.close()

    def mismatched_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.steampowered.com":
            return _json(_owned((440, "Library title", 25)))
        return _json(
            {
                "440": {
                    "success": True,
                    "data": {
                        "steam_appid": 620,
                        "name": "Wrong product",
                        "type": "game",
                        "categories": [{"id": 1}],
                    },
                }
            }
        )

    mismatch_service = SteamService("key", transport=httpx.MockTransport(mismatched_handler))
    try:
        assert await mismatch_service.suggest([STEAM_A], mode="most") == [
            GameSuggestion(440, "Library title", 1, 1, 25, None, None)
        ]
    finally:
        await mismatch_service.close()

    def ambiguous_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.steampowered.com":
            return _json(_owned((440, "Library title", 25)))
        matching = {
            "steam_appid": 440,
            "name": "Ambiguous product",
            "type": "game",
            "categories": [{"id": 1}],
        }
        return _json(
            {
                "other-a": {"success": True, "data": matching},
                "other-b": {"success": True, "data": matching},
            }
        )

    ambiguous_service = SteamService("key", transport=httpx.MockTransport(ambiguous_handler))
    try:
        assert await ambiguous_service.suggest([STEAM_A], mode="most") == [
            GameSuggestion(440, "Library title", 1, 1, 25, None, None)
        ]
    finally:
        await ambiguous_service.close()


def test_category_classifier_preserves_unknown_and_single_player_status() -> None:
    assert _classify_categories([]) == (None, None)
    assert _classify_categories([{"id": 2}]) == (False, False)
    assert _classify_categories([{"id": 1}, {"id": 9}]) == (True, True)
    assert _classify_categories([{"id": True}]) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("product_type", [None, "", 123, False])
async def test_partial_store_metadata_keeps_owned_game_as_unverified(product_type) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.steampowered.com":
            return _json(_owned((440, "Library title", 25)))
        return _json({"440": {"success": True, "data": {"type": product_type}}})

    async with SteamService("key", transport=httpx.MockTransport(handler)) as service:
        assert await service.suggest([STEAM_A], mode="most") == [
            GameSuggestion(440, "Library title", 1, 1, 25, None, None)
        ]
