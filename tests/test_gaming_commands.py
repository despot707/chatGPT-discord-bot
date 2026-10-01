from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.aclient import DiscordClient
from src.config import BotConfig
from src.gaming import GamingStore, PartyPlayer, SteamLink
from src.steam import GameSuggestion, SteamError, SteamProfile


class Response:
    def __init__(self):
        self.done = False
        self.send_message = AsyncMock(side_effect=self._done)
        self.defer = AsyncMock(side_effect=self._done)

    def _done(self, *args, **kwargs):
        self.done = True

    def is_done(self):
        return self.done


class Store:
    def __init__(self):
        self.links = {}
        self.players = {}
        self.link_writes = []
        self.clears = []

    def link_steam(self, guild_id, user_id, steam_id, display_name):
        self.link_writes.append((guild_id, user_id, steam_id, display_name))
        self.links[(guild_id, user_id)] = SteamLink(steam_id, display_name)

    def unlink_steam(self, guild_id, user_id):
        return self.links.pop((guild_id, user_id), None) is not None

    def get_steam(self, guild_id, user_id):
        return self.links.get((guild_id, user_id))

    def join_party(self, guild_id, channel_id, player):
        self.players.setdefault((guild_id, channel_id), {})[player.user_id] = player

    def leave_party(self, guild_id, channel_id, user_id):
        return self.players.get((guild_id, channel_id), {}).pop(user_id, None) is not None

    def party(self, guild_id, channel_id):
        return list(self.players.get((guild_id, channel_id), {}).values())

    def clear_party(self, guild_id, channel_id):
        self.clears.append((guild_id, channel_id))
        self.players[(guild_id, channel_id)] = {}


class Steam:
    def __init__(self, ids=(), *, profile=None, failure=None, suggestions=None):
        self.ids = ids
        self.profile = profile or SteamProfile("76561198000000001", "Linked Player")
        self.failure = failure
        self.suggestions = suggestions or []
        self.resolve_calls = []

    async def resolve_profile(self, value):
        self.resolve_calls.append(value)
        if self.failure:
            raise self.failure
        return self.profile

    async def owned_games(self, steam_id):
        if self.failure:
            raise self.failure
        return [SimpleNamespace(app_id=10, name="Test Game", playtime_minutes=20)]

    async def suggest(self, steam_ids, **kwargs):
        self.ids = list(steam_ids)
        if self.failure:
            raise self.failure
        return self.suggestions


class ControlledSteam(Steam):
    def __init__(self):
        super().__init__()
        self.started = __import__("asyncio").Event()
        self.release = __import__("asyncio").Event()

    async def resolve_profile(self, value):
        self.resolve_calls.append(value)
        self.started.set()
        await self.release.wait()
        return self.profile


def make_interaction(*, guild=1, channel=2, user=3, manage=False, name="Player"):
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild),
        channel=SimpleNamespace(id=channel),
        user=SimpleNamespace(id=user, display_name=name),
        permissions=SimpleNamespace(manage_channels=manage),
        response=Response(),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def make_client(*, store=None, steam=None, **config):
    bot_config = BotConfig(discord_bot_token="token", cooldown_seconds=0, **config)
    client = DiscordClient(bot_config, gaming_store=store, steam_service=steam)
    client._register_commands()
    return client


def group_command(client, group, name):
    command = client.tree.get_command(group)
    return next(item for item in command.commands if item.name == name)


def payload(interaction):
    if interaction.response.send_message.await_count:
        sent = interaction.response.send_message.await_args
    else:
        sent = interaction.followup.send.await_args
    return {"content": sent.kwargs.get("content", sent.args[0] if sent.args else ""), **sent.kwargs}


@pytest.mark.asyncio
async def test_gaming_commands_register_valid_bounded_schemas_and_help_text():
    client = make_client()
    commands = {command.name: command for command in client.tree.get_commands()}
    assert {"steam", "party", "games", "teams"} <= commands.keys()
    for command in commands.values():
        command.to_dict(client.tree)
    party_join = group_command(client, "party", "join")
    party_schema = party_join.to_dict(client.tree)
    skill_schema = next(option for option in party_schema["options"] if option["name"] == "skill")
    assert skill_schema["min_value"] == 1 and skill_schema["max_value"] == 10
    games_together = group_command(client, "games", "together")
    limit_schema = next(
        option
        for option in games_together.to_dict(client.tree)["options"]
        if option["name"] == "limit"
    )
    assert limit_schema["min_value"] == 1 and limit_schema["max_value"] == 10
    teams_make = group_command(client, "teams", "make")
    count_schema = next(
        option
        for option in teams_make.to_dict(client.tree)["options"]
        if option["name"] == "team_count"
    )
    assert count_schema["min_value"] == 2 and count_schema["max_value"] == 4
    help_call = make_interaction()
    await client._commands["help"].callback(help_call)
    message = payload(help_call)["content"].lower()
    assert "/settings" in message and "/profile" in message and "/games" in message


@pytest.mark.asyncio
async def test_allowlist_denies_gaming_callback_before_storage_access():
    store = Store()
    client = make_client(store=store, allowed_channel_ids=frozenset({99}))
    interaction = make_interaction()
    await group_command(client, "party", "join").callback(interaction, skill=5, role="any")
    assert payload(interaction)["ephemeral"] is True
    assert not store.players


@pytest.mark.asyncio
async def test_steam_link_is_self_only_ephemeral_and_warns_ownership_unverified():
    store = Store()
    steam = Steam()
    client = make_client(store=store, steam=steam, steam_api_key="fake-key")
    interaction = make_interaction(user=44, name="@everyone Hero")
    await group_command(client, "steam", "link").callback(
        interaction, "https://steamcommunity.com/id/example"
    )
    assert store.link_writes == [(1, 44, steam.profile.steam_id, steam.profile.display_name)]
    result = payload(interaction)
    assert result["ephemeral"] is True
    assert "not verified" in result["content"]
    assert result["allowed_mentions"].everyone is False


@pytest.mark.asyncio
async def test_steam_link_without_key_stays_private_and_does_not_call_steam():
    store, steam = Store(), Steam()
    client = make_client(store=store, steam=steam)
    interaction = make_interaction()
    await group_command(client, "steam", "link").callback(interaction, "vanity")
    assert payload(interaction)["ephemeral"] is True
    assert not steam.resolve_calls and not store.link_writes


@pytest.mark.asyncio
async def test_private_or_empty_library_failure_is_not_silently_omitted():
    store = Store()
    players = [PartyPlayer(3, "A"), PartyPlayer(4, "B")]
    store.players[(1, 2)] = {p.user_id: p for p in players}
    for user_id in (3, 4):
        store.links[(1, user_id)] = SteamLink(f"7656119800000000{user_id}", f"User {user_id}")
    steam = Steam(
        failure=SteamError("Steam returned no owned games; profile may be private or empty.")
    )
    client = make_client(store=store, steam=steam, steam_api_key="key")
    interaction = make_interaction()
    await group_command(client, "games", "together").callback(interaction)
    message = payload(interaction)
    assert message["ephemeral"] is False
    assert "private" in message["content"] or "Steam" in message["content"]
    assert "secret" not in message["content"].lower()


@pytest.mark.asyncio
async def test_games_rejects_duplicate_linked_profiles_and_checks_channel_party():
    store = Store()
    store.players[(1, 2)] = {3: PartyPlayer(3, "A"), 4: PartyPlayer(4, "B")}
    store.links[(1, 3)] = SteamLink("76561198000000001", "A profile")
    store.links[(1, 4)] = SteamLink("76561198000000001", "B profile")
    steam = Steam(suggestions=[GameSuggestion(1, "Game", 2, 2, 0)])
    client = make_client(store=store, steam=steam, steam_api_key="key")
    interaction = make_interaction()
    await group_command(client, "games", "together").callback(interaction)
    assert payload(interaction)["ephemeral"] is False
    assert "duplicate" in payload(interaction)["content"].lower()
    assert not steam.ids


@pytest.mark.asyncio
async def test_party_scope_isolated_by_guild_and_channel_and_clear_requires_admin():
    store = Store()
    client = make_client(store=store)
    first = make_interaction(guild=1, channel=2, user=3, name="One")
    second = make_interaction(guild=2, channel=2, user=3, name="Two")
    await group_command(client, "party", "join").callback(first, skill=7, role="support")
    await group_command(client, "party", "join").callback(second, skill=4, role="any")
    assert [p.display_name for p in store.party(1, 2)] == ["One"]
    assert [p.display_name for p in store.party(2, 2)] == ["Two"]
    denied = make_interaction(guild=1, channel=2, user=9)
    await group_command(client, "party", "clear").callback(denied)
    assert payload(denied)["ephemeral"] is True and not store.clears


@pytest.mark.asyncio
async def test_game_and_team_results_do_not_claim_mmr_or_guaranteed_lobby_fit():
    store = Store()
    players = [PartyPlayer(3, "Alice", 8, "support"), PartyPlayer(4, "Bob", 4, "damage")]
    store.players[(1, 2)] = {p.user_id: p for p in players}
    store.links[(1, 3)] = SteamLink("76561198000000003", "A")
    store.links[(1, 4)] = SteamLink("76561198000000004", "B")
    suggestion = GameSuggestion(1, "Game", 2, 2, 60, None, None)
    client = make_client(store=store, steam=Steam(suggestions=[suggestion]), steam_api_key="key")
    games_call = make_interaction()
    await group_command(client, "games", "together").callback(games_call)
    text = payload(games_call)["content"].lower()
    assert "unknown" in text and "lobby size" in text
    assert "mmr" not in text
    teams_call = make_interaction()
    await group_command(client, "teams", "make").callback(teams_call, team_count=2, balanced=True)
    team_text = payload(teams_call)["content"].lower()
    assert "skill total" in team_text and "self-reported" in team_text
    assert "mmr" not in team_text


@pytest.mark.asyncio
async def test_real_store_join_show_make_leave_survives_threaded_database_calls():
    store = GamingStore(":memory:")
    client = make_client(store=store)
    for user_id in (31, 32, 33):
        joined = make_interaction(user=user_id, name=f"Player {user_id}")
        await group_command(client, "party", "join").callback(
            joined, skill=user_id - 25, role="flex"
        )
    shown = make_interaction(user=31)
    await group_command(client, "party", "show").callback(shown)
    assert "Player 31" in payload(shown)["content"] and "Player 33" in payload(shown)["content"]
    teams = make_interaction(user=31)
    await group_command(client, "teams", "make").callback(teams, team_count=2, balanced=True)
    assert "Team 1" in payload(teams)["content"] and "Team 2" in payload(teams)["content"]
    left = make_interaction(user=32)
    await group_command(client, "party", "leave").callback(left)
    players = store.party(1, 2)
    assert [player.user_id for player in players] == [31, 33]
    store.close()


@pytest.mark.asyncio
async def test_unlink_supersedes_inflight_steam_link_against_real_store():
    import asyncio

    store = GamingStore(":memory:")
    steam = ControlledSteam()
    client = make_client(store=store, steam=steam, steam_api_key="key")
    link_interaction = make_interaction(user=55)
    link_task = asyncio.create_task(
        group_command(client, "steam", "link").callback(link_interaction, "old-profile")
    )
    await steam.started.wait()
    unlink_interaction = make_interaction(user=55)
    await group_command(client, "steam", "unlink").callback(unlink_interaction)
    steam.release.set()
    await link_task
    assert store.get_steam(1, 55) is None
    assert "superseded" in payload(link_interaction)["content"].lower()
    store.close()


@pytest.mark.asyncio
async def test_party_show_chunks_all_twenty_bounded_player_names():
    store = Store()
    store.players[(1, 2)] = {
        user_id: PartyPlayer(user_id, f"Player {user_id} " + ("LongName_" * 7), 5, "role")
        for user_id in range(1, 21)
    }
    client = make_client(store=store)
    interaction = make_interaction()
    await group_command(client, "party", "show").callback(interaction)
    chunks = [
        call.args[0] if call.args else call.kwargs["content"]
        for call in interaction.followup.send.await_args_list
    ]
    if interaction.response.send_message.await_count:
        chunks.insert(0, payload(interaction)["content"])
    output = "\n".join(chunks)
    assert len(chunks) > 1
    assert all(f"Player {user_id} " in output for user_id in range(1, 21))
