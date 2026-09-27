import asyncio

import pytest
from src.aclient import DiscordClient
from src.config import BotConfig
from src.providers import CompletionResult, ProviderType


class FakeProvider:
    def __init__(self, answer="ok"):
        self.answer = answer
        self.calls = []
        self.event = None

    async def chat_completion(self, messages, model=None, **kwargs):
        self.calls.append((messages, model, kwargs))
        if self.event:
            await self.event.wait()
        return self.answer

    def supports_image_generation(self):
        return False


class FakeManager:
    def __init__(self):
        self.provider = FakeProvider()

    def get_provider(self, provider_type=None):
        return self.provider

    async def complete(self, messages, *, provider_type=None, model=None, images=(), **kwargs):
        text = await self.provider.chat_completion(messages, model=model, **kwargs)
        selected = provider_type or ProviderType.GEMINI
        return CompletionResult(text, selected, model or "fake-model", (selected,))


def make_client(**overrides):
    values = dict(discord_bot_token="token", cooldown_seconds=0)
    values.update(overrides)
    return DiscordClient(BotConfig(**values), FakeManager())


@pytest.mark.asyncio
async def test_private_and_public_history_are_isolated():
    client = make_client()
    scope = (8, 9, 10)
    await client.respond(scope, "public", private=False)
    await client.respond(scope, "secret", private=True)
    assert len(client.conversations) == 2
    public = next(c for k, c in client.conversations.items() if not k.private)
    private = next(c for k, c in client.conversations.items() if k.private)
    assert "secret" not in str(public.messages)
    assert "public" not in str(private.messages)


@pytest.mark.asyncio
async def test_session_count_trim_and_idle_eviction_are_bounded():
    client = make_client(
        max_sessions=2, history_messages=2, history_chars=50, idle_ttl_seconds=3600
    )
    scope = (1, 2, 1)
    await client.respond(scope, "question", private=False)
    await client.respond(scope, "private question", private=True)
    client.get_settings((1, 2, 2))
    with pytest.raises(RuntimeError, match="session limit"):
        client.get_settings((1, 2, 3))
    assert len(client.conversations) == 2
    conv = next(iter(client.conversations.values()))
    assert len(conv.messages) <= 2
    client._prune(now=10**10)
    assert not client.conversations


@pytest.mark.asyncio
async def test_capacity_fails_fast_and_settings_cannot_change_mid_request():
    manager = FakeManager()
    manager.provider.event = asyncio.Event()
    client = DiscordClient(
        BotConfig(discord_bot_token="x", cooldown_seconds=0, max_concurrent_requests=1), manager
    )
    scope = (1, 2, 3)
    task = asyncio.create_task(client.respond(scope, "hello", private=False))
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="busy|already"):
        await client.respond((1, 2, 4), "too many", private=False)
    with pytest.raises(RuntimeError, match="in progress"):
        client.reset(scope, channel=True)
    manager.provider.event.set()
    await task


@pytest.mark.asyncio
async def test_settings_are_per_user_channel_and_provider_snapshot_is_used():
    manager = FakeManager()
    manager.provider.event = asyncio.Event()
    client = DiscordClient(BotConfig(discord_bot_token="token", cooldown_seconds=0), manager)
    a = client.get_settings((1, 2, 3))
    b = client.get_settings((1, 2, 4))
    a.persona = "creative"
    assert b.persona == "standard"
    assert b.private is True
    assert a.provider == ProviderType.GEMINI
    a.model = "chosen-model"
    snapshot = (a.provider, a.model, a.persona)
    task = asyncio.create_task(
        client.respond((1, 2, 3), "hello", private=False, settings_snapshot=snapshot)
    )
    await asyncio.sleep(0)
    a.model, a.persona = "later-model", "technical"
    manager.provider.event.set()
    await task
    messages, model, _ = manager.provider.calls[-1]
    assert model == "chosen-model"
    assert "enhanced creative capabilities" in messages[0]["content"]


def test_expired_scope_restores_private_default():
    client = make_client(idle_ttl_seconds=1)
    scope = (1, 2, 3)
    settings = client.get_settings(scope)
    settings.private = False
    client._prune(now=settings.last_used + 2)
    assert client.get_settings(scope).private is True


@pytest.mark.asyncio
async def test_cancelled_provider_call_releases_capacity_and_reset_is_scoped():
    manager = FakeManager()
    client = DiscordClient(BotConfig(discord_bot_token="x", cooldown_seconds=0), manager)
    first = (1, 2, 3)
    other = (1, 2, 4)
    await client.respond(other, "keep this", private=False)
    manager.provider.event = asyncio.Event()
    task = asyncio.create_task(client.respond(first, "cancel me", private=False))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client._capacity.locked()
    assert not next(c for k, c in client.conversations.items() if not k.private).in_flight
    client.reset(first)
    assert "keep this" in str(next(iter(client.conversations.values())).messages)


@pytest.mark.asyncio
async def test_persistent_shared_history_survives_restart_and_private_history_isolated(tmp_path):
    path = str(tmp_path / "chat.sqlite3")
    config = BotConfig(discord_bot_token="token", cooldown_seconds=0, chat_database_path=path)
    first = DiscordClient(config, FakeManager())
    await first.respond((1, 2, 10), "hello", private=False, speaker_label="Alice")
    await first.respond((1, 2, 10), "private secret", private=True)
    await first.respond((1, 2, 12), "welcome", private=False, speaker_label="Bob")

    restarted = DiscordClient(config, FakeManager())
    await restarted.respond(
        (1, 2, 13), "what did they say?", private=False, include_shared_history=True
    )
    messages = restarted.provider_manager.provider.calls[0][0]
    prior = str(messages)
    assert "Alice: hello" in prior
    assert "Bob: welcome" in prior
    assert "private secret" not in prior
    await restarted.respond((1, 2, 10), "continue privately", private=True)
    private_messages = restarted.provider_manager.provider.calls[1][0]
    assert "private secret" in str(private_messages)
    assert "Alice: hello" not in str(private_messages)


@pytest.mark.asyncio
async def test_shared_conversation_guard_covers_different_users(tmp_path):
    manager = FakeManager()
    manager.provider.event = asyncio.Event()
    client = DiscordClient(
        BotConfig(
            discord_bot_token="token",
            cooldown_seconds=0,
            chat_database_path=str(tmp_path / "chat.sqlite3"),
        ),
        manager,
    )
    task = asyncio.create_task(
        client.respond((1, 2, 10), "first", private=False, speaker_label="Alice")
    )
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="already being generated"):
        await client.respond((1, 2, 11), "second", private=False, speaker_label="Bob")
    manager.provider.event.set()
    await task


@pytest.mark.asyncio
async def test_reset_defaults_to_private_and_admin_channel_reset_clears_shared(tmp_path):
    client = DiscordClient(
        BotConfig(
            discord_bot_token="token",
            cooldown_seconds=0,
            chat_database_path=str(tmp_path / "chat.sqlite3"),
        ),
        FakeManager(),
    )
    await client.respond((1, 2, 10), "private secret", private=True)
    await client.respond((1, 2, 10), "shared note", private=False, speaker_label="Alice")
    await client.respond((1, 2, 11), "another shared note", private=False, speaker_label="Bob")
    store = client._get_chat_store()
    assert store is not None

    assert client.reset((1, 2, 10))
    assert store.load((1, 2, 10), max_messages=20, max_chars=24000) == []
    assert store.load((1, 2, 0), max_messages=20, max_chars=24000)

    assert client.reset((1, 2, 10), channel=True)
    assert store.load((1, 2, 0), max_messages=20, max_chars=24000) == []
