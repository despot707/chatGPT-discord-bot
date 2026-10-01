from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.aclient import PUBLIC_FAILURE, DiscordClient
from src.budget import BudgetError
from src.config import BotConfig
from src.providers import ProviderError


class FailingManager:
    def __init__(self, error=None):
        self.error = error or ProviderError("monthly budget exhausted: $0.00 left")

    async def complete(self, messages, **kwargs):
        raise self.error


def interaction():
    return SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        user=SimpleNamespace(id=3, display_name="User"),
        permissions=SimpleNamespace(manage_channels=False),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [True, False])
@pytest.mark.parametrize(
    "error",
    [
        ProviderError("monthly budget exhausted: $0.00 left"),
        BudgetError("Monthly budget exhausted: $0.00 left"),
    ],
)
async def test_chat_budget_failures_are_generic(private, error):
    client = DiscordClient(
        BotConfig(discord_bot_token="test", cooldown_seconds=0), FailingManager(error)
    )
    client.get_settings((1, 2, 3)).private = private
    target = interaction()

    await client._chat_interaction(target, "hello")

    assert target.followup.send.await_args.args[0] == PUBLIC_FAILURE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProviderError("monthly budget exhausted: $0.00 left"),
        BudgetError("Monthly budget exhausted: $0.00 left"),
    ],
)
async def test_search_budget_failures_are_generic(error):
    client = DiscordClient(
        BotConfig(discord_bot_token="test", cooldown_seconds=0, enable_openai_web_search=True),
        FailingManager(error),
    )
    target = interaction()

    await client._web_chat_interaction(target, "query", kind="search")

    assert target.followup.send.await_args.args[0] == PUBLIC_FAILURE


@pytest.mark.asyncio
async def test_draw_budget_provider_error_is_generic():
    client = DiscordClient(
        BotConfig(discord_bot_token="test", enable_image_generation=True), FailingManager()
    )
    client._register_commands()
    client.generate_image = AsyncMock(side_effect=ProviderError("provider cost limit reached"))
    target = interaction()

    await client._commands["draw"].callback(target, "a picture")

    assert target.followup.send.await_args.args[0] == PUBLIC_FAILURE


@pytest.mark.asyncio
async def test_mention_budget_provider_error_is_generic():
    client = DiscordClient(
        BotConfig(discord_bot_token="test", enable_message_content=True, cooldown_seconds=0),
        FailingManager(),
    )
    client._connection.user = SimpleNamespace(id=900)

    @asynccontextmanager
    async def typing():
        yield

    channel = SimpleNamespace(id=2, typing=typing, send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(id=3, bot=False, display_name="User"),
        webhook_id=None,
        guild=SimpleNamespace(id=1),
        channel=channel,
        mentions=[client.user],
        reference=None,
        attachments=[],
        content="<@900> hello",
    )

    await client.on_message(message)

    assert channel.send.await_args.args[0] == PUBLIC_FAILURE


def test_public_error_renderer_preserves_nonbudget_validation_errors():
    from src.aclient import public_error_message

    assert public_error_message(ValueError("Unknown model")) == PUBLIC_FAILURE
    assert public_error_message(ValueError("Choose a valid day")) == "Choose a valid day"
    assert public_error_message(ValueError("Daily quota exhausted")) == PUBLIC_FAILURE
