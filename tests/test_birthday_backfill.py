import asyncio
import importlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS


def api():
    assert Path('src/birthday_client.py').exists(), 'birthday scanner integration is missing'
    return importlib.import_module('src.birthday_client')


class Channel:
    def __init__(self, messages):
        self.id = 10
        self.guild = NS(id=1)
        self.messages = messages
        self.calls = []
    async def history(self, **kw):
        self.calls.append(kw)
        values = [m for m in self.messages if kw.get('before') is None or m.id < kw['before'].id]
        for m in sorted(values, key=lambda x: x.id, reverse=True)[:kw['limit']]:
            yield m


def msg(mid, text='ordinary conversation'):
    return NS(id=mid, content=text, created_at=datetime(2026,8,11,12,tzinfo=timezone.utc),
              author=NS(id=8, display_name='Bob', name='bob', bot=False), mentions=[],
              guild=NS(id=1), channel=NS(id=10), reference=None, webhook_id=None)


def test_scan_pages_and_resume_without_starting_over(tmp_path):
    a = api()
    from src.birthday_memory import BirthdayStore
    store = BirthdayStore(str(tmp_path/'memory.db'))
    c = Channel([msg(i, 'Happy birthday <@42>!' if i == 25 else 'ordinary chat') for i in range(1,206)])
    assert asyncio.run(a.scan_page(store,c,lambda c: True)) is True
    assert store.progress(1,10)['before_id'] == 106
    store = BirthdayStore(str(tmp_path/'memory.db'))
    assert asyncio.run(a.scan_page(store,c,lambda c: True)) is True
    assert asyncio.run(a.scan_page(store,c,lambda c: True)) is False
    assert store.progress(1,10)['scanned'] == 205
    assert store.summary(1,[10])[0]['user_id'] == 42
    assert store.current_name(1,8) == 'Bob'
    assert asyncio.run(a.scan_page(store,c,lambda c: True)) is False
    assert len(c.calls) == 3


def test_disallowed_channel_is_never_read(tmp_path):
    a = api()
    from src.birthday_memory import BirthdayStore
    store = BirthdayStore(str(tmp_path/'memory.db'))
    c = Channel([msg(1,'Happy birthday <@42>')])
    assert asyncio.run(a.scan_page(store,c,lambda c: False)) is False
    assert c.calls == []
    assert store.stats(1)['candidates'] == 0


def test_permission_change_during_fetch_discards_page(tmp_path):
    a = api()
    from src.birthday_memory import BirthdayStore
    store = BirthdayStore(str(tmp_path/'memory.db'))
    c = Channel([msg(1,'Happy birthday <@42>')])
    decisions = iter([True,False])
    assert asyncio.run(a.scan_page(store,c,lambda c: next(decisions))) is False
    assert store.stats(1)['candidates'] == 0
    assert store.progress(1,10)['scanned'] == 0


def test_transient_network_error_does_not_advance_cursor(tmp_path):
    a = api()
    from src.birthday_memory import BirthdayStore
    store = BirthdayStore(str(tmp_path/'memory.db'))
    class Bad(Channel):
        async def history(self,**kw):
            yield msg(1)
            raise OSError('network failure')
    import pytest
    with pytest.raises(OSError):
        asyncio.run(a.scan_page(store,Bad([]),lambda c:True))
    assert store.progress(1,10)['before_id'] is None


def test_bot_and_webhook_messages_excluded(tmp_path):
    a=api()
    from src.birthday_memory import BirthdayStore
    s=BirthdayStore(str(tmp_path/'memory.db'))
    x,y=msg(1,'Happy birthday <@42>'),msg(2,'Happy birthday <@42>')
    x.author.bot=True
    y.webhook_id=11
    asyncio.run(a.scan_page(s,Channel([x,y]),lambda c:True))
    assert s.stats(1)['scanned']==2 and s.stats(1)['candidates']==0


def test_reply_only_attributes_clear_self_reference():
    a=api()
    m=msg(2,'Happy birthday!')
    original=msg(1,'My birthday is today!')
    original.author.id=42
    m.reference=NS(resolved=original,channel_id=10)
    assert a.message_record(m)['reply_subject_id']==42
    original.content='Happy birthday <@43>'
    assert a.message_record(m).get('reply_subject_id') is None


def test_public_gate_checks_everyone_and_allowlists():
    a=api()
    class Base:
        def allowed(self,scope): return scope[0]==1 and scope[1]==10
    class Client(a.BirthdayMemoryMixin,Base): pass
    client=Client()
    client.config=NS(enable_long_term_memory=True,enable_message_content=True)
    c=NS(id=10,guild=NS(id=1,me=object(),default_role=object()))
    c.permissions_for=lambda x:NS(view_channel=True,read_message_history=True)
    assert client._birthday_channel_allowed(c)
    c.permissions_for=lambda x:NS(view_channel=x is c.guild.me,read_message_history=True)
    assert not client._birthday_channel_allowed(c)


def test_live_collection_is_gated_before_base_archive(tmp_path):
    a=api()
    class Base:
        def allowed(self,scope): return True
        def _get_memory_store(self): return 'original store'
        async def on_message(self,m): self.visible = self._get_memory_store()
    class Client(a.BirthdayMemoryMixin,Base): pass
    client=Client()
    client.config=NS(enable_long_term_memory=True,enable_message_content=True,memory_database_path=str(tmp_path/'m.db'))
    client.user=NS(id=99)
    m=msg(2)
    m.channel.guild=m.guild
    m.guild.me,m.guild.default_role=object(),object()
    m.channel.permissions_for=lambda x:NS(view_channel=False,read_message_history=True)
    asyncio.run(client.on_message(m))
    assert client.visible is None
