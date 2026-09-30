"""Disabled launch mode must leave free commands running without provider calls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from main import validate_environment
from src.aclient import PUBLIC_FAILURE, DiscordClient
from src.config import BotConfig
from src.providers import ProviderError, ProviderManager


def _env(**extra: str) -> dict[str, str]:
    return {"DISCORD_BOT_TOKEN": "test", "AI_ACCESS_MODE": "disabled", **extra}


def _interaction() -> SimpleNamespace:
    response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), is_done=lambda: True)
    return SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        user=SimpleNamespace(id=3, display_name="Player"),
        permissions=SimpleNamespace(manage_channels=False),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_disabled_mode_validates_without_model_key_or_budget_ledger():
    config = validate_environment(
        _env(
            DEFAULT_PROVIDER="openai",
            HARD_BUDGET_ENABLED="true",
            OPENAI_MODEL="otherwise-invalid-for-budget",
            ENABLE_WEB_SEARCH="true",
            ENABLE_OPENAI_WEB_SEARCH="true",
            ENABLE_IMAGE_GENERATION="true",
        )
    )
    assert config.ai_access_mode == "disabled"
    assert not config.enable_web_search
    assert not config.enable_openai_web_search
    assert config.enable_image_generation
    with pytest.raises(ValueError, match="AI_ACCESS_MODE"):
        validate_environment(_env(AI_ACCESS_MODE="unknown"))
    with pytest.raises(ValueError, match="AI_ACCESS_MODE"):
        BotConfig(discord_bot_token="test", ai_access_mode="unknown")


@pytest.mark.asyncio
async def test_disabled_manager_never_constructs_or_dispatches_providers(monkeypatch, caplog):
    def forbidden(*_args, **_kwargs):
        pytest.fail("An AI provider or budget ledger was constructed")

    monkeypatch.setattr("src.providers.OpenAIProvider", forbidden)
    monkeypatch.setattr("src.providers.GeminiProvider", forbidden)
    monkeypatch.setattr("src.providers.ClaudeProvider", forbidden)
    monkeypatch.setattr("src.providers.GroqProvider", forbidden)
    monkeypatch.setattr("src.providers.GrokProvider", forbidden)
    monkeypatch.setattr("src.providers.OpenRouterProvider", forbidden)
    monkeypatch.setattr("src.providers.OllamaProvider", forbidden)
    monkeypatch.setattr("src.providers.BudgetLedger", forbidden)
    manager = ProviderManager(
        _env(
            DEFAULT_PROVIDER="openai",
            HARD_BUDGET_ENABLED="true",
            OPENAI_API_KEY="dummy",
            GEMINI_API_KEY="dummy",
            ANTHROPIC_API_KEY="dummy",
            GROQ_API_KEY="dummy",
            OPENROUTER_API_KEY="dummy",
            XAI_API_KEY="dummy",
            OLLAMA_MODEL="dummy",
            ALLOW_PAID_PROVIDERS="true",
        )
    )
    assert manager.providers == {}
    assert manager.budget is None
    with pytest.raises(ProviderError, match="disabled"):
        await manager.complete([{"role": "user", "content": "hello"}])
    with pytest.raises(ProviderError, match="disabled"):
        manager.get_provider()
    assert "AI_ACCESS_MODE=disabled" in caplog.text


@pytest.mark.asyncio
async def test_disabled_chat_image_and_search_return_generic_before_cost_paths():
    config = BotConfig.from_env(_env(ENABLE_IMAGE_GENERATION="true"))
    manager = ProviderManager(_env())
    manager.complete = AsyncMock()
    manager.get_provider = AsyncMock()
    client = DiscordClient(config, provider_manager=manager)
    client._register_commands()
    assert not client.web_service.allow_paid_search
    context = AsyncMock(return_value="page content")
    with pytest.raises(RuntimeError, match="I can't do that right now"):
        await client.respond((1, 2, 3), "hello", context_loader=context)
    context.assert_not_awaited()
    with pytest.raises(RuntimeError, match="I can't do that right now"):
        await client.generate_image("draw", (1, 2, 3))
    manager.complete.assert_not_awaited()
    manager.get_provider.assert_not_awaited()

    chat = _interaction()
    await client._chat_interaction(chat, "hello")
    assert chat.response.send_message.await_args.args[0] == PUBLIC_FAILURE
    search = _interaction()
    await client._web_chat_interaction(search, "query", kind="search")
    assert search.response.send_message.await_args.args[0] == PUBLIC_FAILURE
    draw = _interaction()
    await client._commands["draw"].callback(draw, "a cat")
    assert draw.response.send_message.await_args.args[0] == PUBLIC_FAILURE


@pytest.mark.asyncio
async def test_disabled_mode_keeps_status_and_free_gaming_commands_available():
    store = SimpleNamespace(party=lambda *_args: [], get_steam=lambda *_args: None)
    client = DiscordClient(
        BotConfig.from_env(_env()), provider_manager=ProviderManager(_env()), gaming_store=store
    )
    client._register_commands()

    status = _interaction()
    await client._commands["status"].callback(status)
    assert "Model: disabled" in status.response.send_message.await_args.args[0]

    party = client.tree.get_command("party")
    show = next(command for command in party.commands if command.name == "show")
    target = _interaction()
    await show.callback(target)
    assert "party is empty" in target.followup.send.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_disabled_profile_forms_work_and_remember_never_dispatches(tmp_path):
    from src.bot import DiscordClient as ProductionDiscordClient
    from src.member_settings import ProfileStore

    from tests.test_profile_ui import interaction

    manager = ProviderManager(_env())
    manager.complete = AsyncMock()
    client = ProductionDiscordClient(BotConfig.from_env(_env()), provider_manager=manager)
    client.profile_store = ProfileStore(str(tmp_path / "profiles.sqlite3"))
    client._register_commands()
    client._register_profile_commands()

    remember = interaction()
    await client.tree.get_command("remember").callback(remember, "Remember my game")
    assert remember.response.send_message.await_args.args[0] == PUBLIC_FAILURE
    remember.response.defer.assert_not_awaited()
    manager.complete.assert_not_awaited()

    profile = interaction()
    await client.tree.get_command("profile").callback(profile)
    profile.response.defer.assert_awaited_once()
    assert profile.followup.send.await_args.kwargs["ephemeral"] is True
    await client.close()
