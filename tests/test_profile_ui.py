import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def modules():
    assert importlib.util.find_spec('src.profile_ui'), 'private profile UI is missing'
    from src.member_settings import ProfileStore
    from src.profile_ui import ProfilePanel, BirthdayModal, GameModal, PreferencesModal
    return ProfileStore, ProfilePanel, BirthdayModal, GameModal, PreferencesModal


def interaction(uid=2, gid=1):
    response = SimpleNamespace(is_done=lambda:False, send_message=AsyncMock(), defer=AsyncMock(), send_modal=AsyncMock(), edit_message=AsyncMock())
    return SimpleNamespace(user=SimpleNamespace(id=uid, display_name='Alex',display_avatar=SimpleNamespace(url='https://cdn.discordapp.com/embed/avatars/0.png')),guild=SimpleNamespace(id=gid,name='Test server'),guild_id=gid,channel_id=10,response=response,followup=SimpleNamespace(send=AsyncMock()),edit_original_response=AsyncMock(),message=SimpleNamespace(flags=SimpleNamespace(ephemeral=True)))


def panel(tmp_path, screen='home'):
    Store, Panel, *_ = modules()
    client = SimpleNamespace(profile_store=Store(str(tmp_path/'p.sqlite3')), allowed=lambda s:True)
    return Panel(client,1,2,client.profile_store.get(1,2),'Alex','https://cdn.discordapp.com/embed/avatars/0.png',screen=screen)


@pytest.mark.asyncio
async def test_native_payload_and_owner_guard(tmp_path):
    p = panel(tmp_path)
    encoded = json.dumps(p.to_components())
    assert 'Your profile' in encoded
    assert 'Birthday' in encoded and 'Games' in encoded and 'Privacy' in encoded
    assert await p.interaction_check(interaction())
    wrong = interaction(uid=3)
    assert not await p.interaction_check(wrong)
    assert wrong.response.send_message.call_args.kwargs['ephemeral'] is True
    wrong_guild = interaction(gid=99)
    assert not await p.interaction_check(wrong_guild)


@pytest.mark.asyncio
async def test_public_message_cannot_be_used_as_settings_session(tmp_path):
    p = panel(tmp_path)
    i = interaction()
    i.message.flags.ephemeral = False
    assert not await p.interaction_check(i)


@pytest.mark.asyncio
@pytest.mark.parametrize('screen',['home','games','privacy','data'])
async def test_all_screens_native_and_within_limits(tmp_path, screen):
    p = panel(tmp_path,screen)
    assert p.content_length() <= 4000
    assert p.total_children_count <= 40
    encoded = json.dumps(p.to_components())
    assert 'http://localhost' not in encoded
    assert 'encrypted at rest' not in encoded


@pytest.mark.asyncio
async def test_modals_owner_bound_and_valid(tmp_path):
    p = panel(tmp_path)
    for modal_type in modules()[2:]:
        modal = modal_type(p)
        payload = modal.to_dict()
        assert len(payload['components']) <= 5
        assert len(payload['title']) <= 45
        assert await modal.interaction_check(interaction())
        bad = interaction(uid=88)
        assert not await modal.interaction_check(bad)
        assert bad.response.send_message.call_args.kwargs['ephemeral']


@pytest.mark.asyncio
async def test_timeout_error_stays_private(tmp_path):
    p = panel(tmp_path)
    i = interaction()
    await p.on_error(i,RuntimeError('secret database path'),None)
    args = i.response.send_message.call_args
    assert args.kwargs['ephemeral']
    assert 'secret database path' not in str(args)


@pytest.mark.asyncio
async def test_profile_slash_command_registered_and_response_private(tmp_path):
    assert importlib.util.find_spec('src.profile_client'), 'profile client missing'
    from src.bot import DiscordClient
    from src.config import BotConfig
    from src.member_settings import ProfileStore
    c = DiscordClient(BotConfig(discord_bot_token='test',enable_long_term_memory=True))
    c.profile_store = ProfileStore(str(tmp_path/'p.sqlite3'))
    c._register_commands()
    c._register_profile_commands()
    assert c.tree.get_command('profile') is not None
    assert c.tree.get_command('remember') is not None
    assert c._get_memory_store() is None
    i = interaction()
    await c.tree.get_command('profile').callback(i)
    assert i.response.defer.call_args.kwargs['ephemeral']
    assert i.followup.send.call_args.kwargs['ephemeral']
    await c.close()
