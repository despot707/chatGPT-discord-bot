import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.profile_privacy import erase_legacy_records
from src.profile_client import parse_proposal


def test_deletion_preserves_other_members_private_records(tmp_path):
    path = str(tmp_path/'all.sqlite3')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE chat_turns (guild_id INTEGER,user_id INTEGER,content TEXT)')
        db.executemany('INSERT INTO chat_turns VALUES (?,?,?)', [(1,0,'shared'),(1,2,'mine'),(1,3,'other private'),(9,2,'other server')])
        db.execute('CREATE TABLE member_aliases (guild_id INTEGER,user_id INTEGER,normalized TEXT)')
        db.executemany('INSERT INTO member_aliases VALUES (?,?,?)',[(1,2,'oldname'),(1,3,'third')])
        db.execute('CREATE TABLE birthday_candidates (guild_id INTEGER,subject_id INTEGER,observer_id INTEGER,target_alias TEXT)')
        db.executemany('INSERT INTO birthday_candidates VALUES (?,?,?,?)',[(1,2,5,''),(1,None,5,'oldname'),(1,3,5,'')])
    erase_legacy_records(SimpleNamespace(memory_database_path=path),1,2)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT content FROM chat_turns ORDER BY content').fetchall() == [('other private',),('other server',)]
        assert db.execute('SELECT user_id FROM member_aliases').fetchall() == [(3,)]
        assert db.execute('SELECT subject_id FROM birthday_candidates').fetchall() == [(3,)]


def test_deletion_failure_is_not_silently_success(tmp_path):
    path = tmp_path/'not-a-database'
    path.write_text('corrupt')
    with pytest.raises(ValueError, match='could not be removed'):
        erase_legacy_records(SimpleNamespace(memory_database_path=str(path)),1,2)


@pytest.mark.parametrize('value', [None, [], {'operation':'forget','data':{}}, {'operation':'game','data':{'name':'League','user_id':3}}, {'operation':'birthday','data':{'month':8,'day':11},'user_id':3}])
def test_model_cannot_choose_identity_or_delete_everything(value):
    with pytest.raises(ValueError):
        parse_proposal(json.dumps(value))


def test_model_cannot_enable_sharing():
    _, data = parse_proposal(json.dumps({'operation':'game','data':{'name':'League','visibility':'server'}}))
    assert data['visibility'] == 'private'


@pytest.mark.asyncio
async def test_private_saved_values_excluded_from_public_model_input(tmp_path):
    from src.bot import DiscordClient
    from src.config import BotConfig
    from src.member_settings import ProfileStore
    from src.providers import ProviderType
    manager = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(text='ok', attempted=(ProviderType.GEMINI,),provider=ProviderType.GEMINI)), close=AsyncMock())
    c = DiscordClient(BotConfig(discord_bot_token='test',cooldown_seconds=0,chat_database_path=None),provider_manager=manager)
    c.profile_store = ProfileStore(str(tmp_path/'profiles.sqlite3'))
    c.profile_store.apply(1,2,'birthday',{'month':8,'day':11},0)
    await c.respond((1,10,2),'When is my birthday?',private=False)
    public = str(manager.complete.call_args.kwargs['messages'])
    assert '"month": 8' not in public
    await c.respond((1,10,2),'When is my birthday?',private=True)
    private = str(manager.complete.call_args.kwargs['messages'])
    assert '"month": 8' in private
    await c.respond((1,10,3),'When is their birthday?',private=True)
    assert '"month": 8' not in str(manager.complete.call_args.kwargs['messages'])
    await c.close()
