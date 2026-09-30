"""Regression tests for profile deletion and changing game-night rosters."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest
from src.gaming import GamingStore, PartyPlayer
from src.steam import GameSuggestion

from tests.test_gaming_commands import (
    Steam,
    group_command,
    make_client,
    make_interaction,
    payload,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_link", [False, True])
async def test_unlink_waits_for_started_database_insert_even_when_link_cancelled(cancel_link):
    started = threading.Event()
    release = threading.Event()

    class SlowStore(GamingStore):
        def link_steam(self, *args):
            started.set()
            assert release.wait(5), "Test did not release the pending insert"
            return super().link_steam(*args)

    store = SlowStore(":memory:")
    client = make_client(store=store, steam=Steam(), steam_api_key="test-key")
    link = asyncio.create_task(
        group_command(client, "steam", "link").callback(make_interaction(), "profile")
    )
    unlink = None
    try:
        assert await asyncio.to_thread(started.wait, 3)
        if cancel_link:
            link.cancel()
        target = make_interaction()
        unlink = asyncio.create_task(group_command(client, "steam", "unlink").callback(target))
        async with asyncio.timeout(3):
            while not target.response.defer.await_count:
                await asyncio.sleep(0)
        assert not unlink.done()
        release.set()
        outcomes = await asyncio.wait_for(asyncio.gather(link, unlink, return_exceptions=True), 3)
        if cancel_link:
            assert isinstance(outcomes[0], asyncio.CancelledError)
        else:
            assert outcomes[0] is None
        assert outcomes[1] is None
        assert store.get_steam(1, 3) is None
        assert not client._steam_mutations
    finally:
        release.set()
        await asyncio.gather(*[task for task in (link, unlink) if task], return_exceptions=True)
        store.close()


@pytest.mark.asyncio
async def test_older_delayed_unlink_cannot_delete_a_newer_link():
    store = GamingStore(":memory:")
    client = make_client(store=store, steam=Steam(), steam_api_key="test-key")
    started, release = asyncio.Event(), asyncio.Event()
    target = make_interaction()

    async def delayed_defer(**_kwargs):
        target.response.done = True
        started.set()
        await release.wait()

    target.response.defer = AsyncMock(side_effect=delayed_defer)
    unlink = asyncio.create_task(group_command(client, "steam", "unlink").callback(target))
    try:
        await asyncio.wait_for(started.wait(), 3)
        await group_command(client, "steam", "link").callback(make_interaction(), "new-profile")
        assert store.get_steam(1, 3) is not None
        release.set()
        await asyncio.wait_for(unlink, 3)
        assert store.get_steam(1, 3) is not None
        assert not client._steam_mutations
    finally:
        release.set()
        await asyncio.gather(unlink, return_exceptions=True)
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["leave", "unlink"])
async def test_games_does_not_publish_a_result_after_member_withdraws(change):
    store = GamingStore(":memory:")
    for user_id in (3, 4):
        store.join_party(1, 2, PartyPlayer(user_id, f"Player {user_id}"))
        store.link_steam(1, user_id, f"7656119800000000{user_id}", f"Profile {user_id}")

    class ChangingSteam(Steam):
        async def suggest(self, *_args, **_kwargs):
            if change == "leave":
                store.leave_party(1, 2, 4)
            else:
                store.unlink_steam(1, 4)
            return [GameSuggestion(620, "Portal 2", 2, 2, 100, True, True)]

    client = make_client(store=store, steam=ChangingSteam(), steam_api_key="test-key")
    target = make_interaction()
    try:
        await group_command(client, "games", "together").callback(target)
        assert "changed" in payload(target)["content"]
        assert "Portal 2" not in payload(target)["content"]
    finally:
        store.close()
