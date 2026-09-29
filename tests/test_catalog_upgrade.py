import gzip
import importlib.util
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest


def catalog(tmp_path):
    assert importlib.util.find_spec("src.game_catalog"), "game catalog is missing"
    from src.game_catalog import GameCatalog

    source = tmp_path / "games.json.gz"
    source.write_bytes(
        gzip.compress(
            json.dumps(
                [
                    {
                        "id": "wikidata:Q1",
                        "name": "League of Legends",
                        "description": "2009 multiplayer game",
                        "aliases": ["LoL"],
                    },
                    {
                        "id": "wikidata:Q2",
                        "name": "Left 4 Dead 2",
                        "description": "2009 co-op game",
                        "aliases": [],
                    },
                    {
                        "id": "wikidata:Q3",
                        "name": "Prey",
                        "description": "2006 video game",
                        "aliases": [],
                    },
                    {
                        "id": "wikidata:Q4",
                        "name": "Prey",
                        "description": "2017 video game",
                        "aliases": [],
                    },
                ]
            ).encode()
        )
    )
    return GameCatalog(str(tmp_path / "catalog.sqlite3"), str(source))


def test_catalog_prefix_alias_and_no_arbitrary_ids(tmp_path):
    c = catalog(tmp_path)
    assert c.search("lea")[0]["name"] == "League of Legends"
    assert c.search("lol")[0]["id"] == "wikidata:Q1"
    assert len(c.search("l", limit=100)) <= 25
    assert c.get("League of Legends") is None
    assert c.get("wikidata:Q99999999") is None
    assert len(c.search("Prey")) == 2
    assert c.search('" OR 1=1 --') == []


def test_catalog_ids_survive_label_changes_and_distinguish_same_title(tmp_path):
    from src.member_settings import ProfileStore

    s = ProfileStore(str(tmp_path / "p.sqlite3"))
    p = s.apply(1, 2, "game", {"name": "Prey", "catalog_id": "wikidata:Q3"}, 0)
    p = s.apply(1, 2, "game", {"name": "Prey", "catalog_id": "wikidata:Q4"}, p["revision"])
    assert len(p["games"]) == 2
    p = s.apply(1, 2, "game", {"name": "Prey (2006)", "catalog_id": "wikidata:Q3"}, p["revision"])
    assert len(p["games"]) == 2
    p = s.apply(1, 2, "remove_game", {"name": "Prey", "catalog_id": "wikidata:Q4"}, p["revision"])
    assert [g["catalog_id"] for g in p["games"]] == ["wikidata:Q3"]


def test_matching_only_shared_same_guild_same_catalog_id(tmp_path):
    from src.member_settings import ProfileStore

    s = ProfileStore(str(tmp_path / "p.sqlite3"))
    assert hasattr(s, "players_for"), "canonical-ID player matching missing"
    for gid, uid, shared in [(1, 2, True), (1, 3, False), (2, 4, True)]:
        s.apply(
            gid,
            uid,
            "game",
            {
                "name": "League",
                "catalog_id": "wikidata:Q1",
                "visibility": "server" if shared else "private",
            },
            0,
        )
    assert [x[0] for x in s.players_for(1, "wikidata:Q1")] == [2]


@pytest.mark.asyncio
async def test_chat_defaults_to_recent_context_and_keeps_bot_answers():
    from tests.test_discord_features import interaction, make_client

    c, manager, _ = make_client(enable_message_content=True, automatic_context_count=10)
    i = interaction()
    user = i.user
    i.channel.guild = NS(me=NS(id=77))
    i.channel.permissions_for = lambda who: NS(view_channel=True, read_message_history=True)

    async def history(**kwargs):
        yield NS(
            author=NS(id=77, bot=True, name="Luna", display_name="Luna"),
            webhook_id=None,
            content="Ranked changes your rank; casual does not.",
            attachments=[],
            embeds=[],
        )
        yield NS(
            author=NS(id=user.id, bot=False, name="Alex", display_name="Alex"),
            webhook_id=None,
            content="Ranked versus casual?",
            attachments=[],
            embeds=[],
        )

    i.channel.history = history
    await c._chat_interaction(i, "what's the difference")
    sent = str(manager.calls[0][0])
    assert "Ranked versus casual?" in sent
    assert "Ranked changes your rank" in sent
    await c.close()


@pytest.mark.asyncio
async def test_explicit_context_zero_and_permissions_stay_enforced():
    from tests.test_discord_features import interaction, make_client

    c, manager, _ = make_client(enable_message_content=True)
    i = interaction()
    i.channel.history = AsyncMock(side_effect=AssertionError("history must not be read"))
    await c._chat_interaction(i, "hi", context_messages=0)
    await c._chat_interaction(i, "hi again")
    assert len(manager.calls) == 2
    await c.close()


@pytest.mark.asyncio
async def test_bot_and_application_branding_updated_once(tmp_path):
    assert importlib.util.find_spec("src.bot_branding"), "branding integration missing"
    import base64

    from src.bot_branding import apply_branding

    image = tmp_path / "icon.png"
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII="
        )
    )
    app = NS(id=77, icon=None, edit=AsyncMock())
    client = NS(
        user=NS(id=77, avatar=None, edit=AsyncMock()), application_info=AsyncMock(return_value=app)
    )
    await apply_branding(client, image_path=str(image), state_path=str(tmp_path / "state.json"))
    assert client.user.edit.await_count == 1
    assert app.edit.await_count == 1
    await apply_branding(client, image_path=str(image), state_path=str(tmp_path / "state.json"))
    assert client.user.edit.await_count == 1 and app.edit.await_count == 1


@pytest.mark.asyncio
async def test_catalog_ui_selects_known_ids_and_never_saves_free_names(tmp_path):
    from src.profile_ui import GameModal

    from tests.test_profile_ui import interaction, panel

    p = panel(tmp_path)
    assert hasattr(p.client, "x") is False
    m = GameModal(p, {"name": "League of Legends", "catalog_id": "wikidata:Q1"})
    assert not hasattr(m, "game_input"), "game titles must not be editable free text"
    assert "League of Legends" in str(m.to_dict())
    assert not await m.interaction_check(interaction(uid=99))


@pytest.mark.asyncio
async def test_catalog_commands_autocomplete_and_reject_raw_names(tmp_path):
    from src.bot import DiscordClient
    from src.config import BotConfig
    from src.member_settings import ProfileStore

    from tests.test_profile_ui import interaction

    c = DiscordClient(BotConfig(discord_bot_token="offline", cooldown_seconds=0))
    c.profile_store = ProfileStore(str(tmp_path / "p.sqlite3"))
    assert hasattr(c, "_catalog"), "catalog integration missing"
    c.catalog = catalog(tmp_path)
    c._register_catalog_commands()
    cmd = c.tree.get_command("addgame")
    assert cmd and cmd.parameters[0].autocomplete
    i = interaction()
    await cmd.callback(i, "Leage of Legens")
    assert c.profile_store.get(1, 2)["revision"] == 0
    notice = i.followup.send.call_args or i.response.send_message.call_args
    assert notice.kwargs["ephemeral"]
    await c.close()


def test_container_includes_catalog_and_branding_assets():
    from pathlib import Path

    assert "COPY --chown=botuser:botuser assets/ ./assets/" in Path("Dockerfile").read_text()


@pytest.mark.asyncio
async def test_final_save_rejects_unknown_game_and_overrides_forged_title(tmp_path):
    from tests.test_profile_ui import interaction, panel

    p = panel(tmp_path)
    p.client._catalog = AsyncMock(return_value=catalog(tmp_path))
    i = interaction()
    await p.save(i, "game", {"name": "League", "catalog_id": "wikidata:Q999"})
    assert p.client.profile_store.get(1, 2)["revision"] == 0
    await p.save(interaction(), "game", {"name": "Leege", "catalog_id": "wikidata:Q1"})
    saved = p.client.profile_store.get(1, 2)
    assert saved["games"][0]["name"] == "League of Legends"
    assert saved["games"][0]["catalog_id"] == "wikidata:Q1"
