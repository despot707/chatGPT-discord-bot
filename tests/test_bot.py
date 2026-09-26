from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.aclient import DiscordClient
from src.config import BotConfig


class Provider:
    def __init__(self):
        self.answer = "A useful answer. " * 300
        self.calls = []
        self.image_model = None

    async def chat_completion(self, messages, model=None, **kwargs):
        self.calls.append(messages)
        return self.answer

    def supports_image_generation(self):
        return True

    def get_available_models(self):
        return [
            SimpleNamespace(name="chat-model", supports_image_generation=False),
            SimpleNamespace(name="image-model", supports_image_generation=True),
        ]

    async def generate_image(self, prompt, model=None):
        self.image_model = model
        return "https://example.test/image.png"


class Manager:
    def __init__(self):
        self.provider = Provider()

    def get_provider(self, provider_type=None):
        return self.provider

    def get_provider_models(self, provider_type=None):
        return self.provider.get_available_models()


def interaction(private=True):
    return SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        user=SimpleNamespace(id=3),
        permissions=SimpleNamespace(manage_channels=False),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_private_chat_keeps_all_chunks_ephemeral_and_does_not_log_content():
    manager = Manager()
    client = DiscordClient(BotConfig(discord_bot_token="x", cooldown_seconds=0), manager)
    client.get_settings((1, 2, 3)).private = True
    target = interaction()
    await client._chat_interaction(target, "do not log this secret")
    target.response.defer.assert_awaited_once_with(ephemeral=True)
    assert target.followup.send.await_count > 1
    assert all(call.kwargs["ephemeral"] is True for call in target.followup.send.await_args_list)
    assert not hasattr(client, "message_queue")


@pytest.mark.asyncio
async def test_allowlist_rejects_before_defer():
    client = DiscordClient(
        BotConfig(discord_bot_token="x", allowed_channel_ids=frozenset({99})), Manager()
    )
    target = interaction()
    await client._chat_interaction(target, "hello")
    target.response.defer.assert_not_awaited()
    target.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_replyall_ignores_bots_and_webhooks():
    client = DiscordClient(
        BotConfig(
            discord_bot_token="x",
            enable_message_content=True,
            replyall_channel_ids=frozenset({2}),
            cooldown_seconds=0,
        ),
        Manager(),
    )
    client.replyall_enabled.add(2)
    message = SimpleNamespace(
        author=SimpleNamespace(id=8, bot=True),
        webhook_id=None,
        channel=SimpleNamespace(id=2),
        guild=SimpleNamespace(id=1),
        content="hello",
    )
    await client.on_message(message)
    message.author.bot = False
    message.webhook_id = 123
    await client.on_message(message)
    assert not client.conversations


@pytest.mark.asyncio
async def test_replyall_requires_admin_and_configured_channel():
    client = DiscordClient(
        BotConfig(
            discord_bot_token="x", enable_message_content=True, replyall_channel_ids=frozenset({2})
        ),
        Manager(),
    )
    client._register_commands()
    target = interaction()
    await client._commands["replyall"].callback(target)
    target.response.send_message.assert_awaited_once()
    assert "permission" in target.response.send_message.await_args.args[0].lower()
    target = interaction()
    target.user.id = 99
    target.permissions.manage_channels = True
    await client._commands["replyall"].callback(target)
    assert 2 in client.replyall_enabled


@pytest.mark.asyncio
async def test_draw_rejection_does_not_echo_provider_exception():
    client = DiscordClient(
        BotConfig(discord_bot_token="x", enable_image_generation=False), Manager()
    )
    client._register_commands()
    target = interaction()
    await client._commands["draw"].callback(target, "secret prompt")
    target.response.send_message.assert_awaited_once()
    assert target.response.send_message.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_provider_rejects_image_only_models_and_draw_uses_image_model():
    manager = Manager()
    client = DiscordClient(
        BotConfig(discord_bot_token="x", enable_image_generation=True, cooldown_seconds=0), manager
    )
    client._register_commands()
    target = interaction()
    await client._commands["provider"].callback(target, "openai", "image-model")
    assert client.get_settings((1, 2, 3)).model == "auto"
    target = interaction()
    await client._commands["draw"].callback(target, "a landscape")
    assert manager.provider.image_model == "image-model"


@pytest.mark.asyncio
async def test_draw_captures_private_visibility_before_defer():
    manager = Manager()
    client = DiscordClient(
        BotConfig(discord_bot_token="x", enable_image_generation=True, cooldown_seconds=0), manager
    )
    client._register_commands()
    target = interaction()
    settings = client.get_settings((1, 2, 3))
    settings.private = True

    async def defer(*, ephemeral):
        settings.private = False

    target.response.defer.side_effect = defer
    await client._commands["draw"].callback(target, "a landscape")
    target.response.defer.assert_awaited_once_with(ephemeral=True)
    assert target.followup.send.await_args.kwargs["ephemeral"] is True
