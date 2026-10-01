from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from main import load_explicit_environment, validate_environment
from src.aclient import DiscordClient
from src.budget_view import format_budget
from src.config import BotConfig
from src.web import WebError, WebService


def test_strict_configuration_excludes_unmetered_search():
    config = BotConfig.from_env(
        {
            "DISCORD_BOT_TOKEN": "test",
            "HARD_BUDGET_ENABLED": "true",
            "TAVILY_API_KEY": "test-only",
            "ENABLE_WEB_SEARCH": "true",
            "ENABLE_OPENAI_WEB_SEARCH": "true",
        }
    )
    assert config.hard_budget_enabled
    assert config.default_provider == "openai"
    assert not config.enable_web_search
    assert config.enable_openai_web_search
    with pytest.raises(ValueError, match="HARD_BUDGET_ENABLED"):
        BotConfig.from_env({"DISCORD_BOT_TOKEN": "test", "HARD_BUDGET_ENABLED": "typo"})
    with pytest.raises(ValueError, match="DEFAULT_PROVIDER"):
        BotConfig.from_env(
            {
                "DISCORD_BOT_TOKEN": "test",
                "HARD_BUDGET_ENABLED": "true",
                "DEFAULT_PROVIDER": "gemini",
            }
        )
    with pytest.raises(ValueError, match="DEFAULT_MODEL"):
        BotConfig.from_env(
            {
                "DISCORD_BOT_TOKEN": "test",
                "HARD_BUDGET_ENABLED": "true",
                "DEFAULT_MODEL": "gpt-6-astra",
            }
        )


@pytest.mark.asyncio
async def test_unmetered_search_is_blocked_before_network():
    factory = AsyncMock()
    service = WebService("test-only", allow_paid_search=False, _session_factory=factory)
    with pytest.raises(WebError, match="hard spending limit"):
        await service.search("query")
    factory.assert_not_called()


def test_explicit_env_does_not_inherit_a_baseline_or_budget_override(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("BUDGET_OPENING_MONTH_SPEND_USD", "0")
    monkeypatch.setenv("BUDGET_MONTHLY_USD", "100")
    monkeypatch.setenv("HARD_BUDGET_ENABLED", "false")
    env = tmp_path / "bot.env"
    env.write_text("HARD_BUDGET_ENABLED=true\n", encoding="utf-8")
    load_explicit_environment(env)
    assert "BUDGET_OPENING_MONTH_SPEND_USD" not in os.environ
    assert "BUDGET_MONTHLY_USD" not in os.environ
    assert os.environ["HARD_BUDGET_ENABLED"] == "true"


def test_explicit_hard_budget_authorizes_only_the_bounded_paid_path():
    config = validate_environment(
        {
            "HARD_BUDGET_ENABLED": "true",
            "DISCORD_BOT_TOKEN": "test-only",
            "OPENAI_API_KEY": "test-only",
        }
    )
    assert config.default_provider == "openai"
    assert config.hard_budget_enabled
    assert not config.allow_paid_providers


@pytest.mark.parametrize("value", ["11", "NaN", "-1"])
def test_startup_rejects_invalid_strict_budget_without_opening_ledger(value, tmp_path):
    path = tmp_path / "not-created.sqlite3"
    with pytest.raises(ValueError, match="BUDGET_MONTHLY_USD"):
        validate_environment(
            {
                "HARD_BUDGET_ENABLED": "true",
                "DISCORD_BOT_TOKEN": "test-only",
                "OPENAI_API_KEY": "test-only",
                "BUDGET_MONTHLY_USD": value,
                "BUDGET_DATABASE_PATH": str(path),
            }
        )
    assert not path.exists()


def _snapshot():
    return {
        "monthly_limit_micros": 10_000_000,
        "monthly_spent_micros": 900_000,
        "reserved_micros": 10_000,
        "monthly_remaining_micros": 9_090_000,
        "luna": {
            "daily_remaining_micros": 200_000,
            "daily_limit_micros": 233_333,
            "monthly_limit_micros": 7_000_000,
        },
        "extras": {
            "daily_remaining_micros": 90_000,
            "daily_limit_micros": 100_000,
            "monthly_limit_micros": 3_000_000,
        },
        "day_resets_at": "2026-09-28T00:00:00-07:00",
        "month_resets_at": "2026-10-01T00:00:00-07:00",
        "blocked_reason": None,
    }


def test_status_distinguishes_total_reservations_and_daily_allocations():
    snapshot = _snapshot()
    text = format_budget(snapshot)
    assert "$9.0900" in text
    assert "reserved: $0.0100" in text
    assert "Luna: $0.2000 left today / $0.2333" in text
    assert "Extras: $0.0900 left today / $0.1000" in text
    assert "Daily reset: <t:" in text
    snapshot["blocked_reason"] = "Opening spend is unknown"
    assert "Paid requests blocked: Opening spend is unknown" in format_budget(snapshot)


@pytest.mark.asyncio
async def test_budget_command_is_private_and_never_calls_ai():
    manager = SimpleNamespace(
        budget=SimpleNamespace(snapshot=lambda: _snapshot()), complete=AsyncMock()
    )
    client = DiscordClient(
        BotConfig(discord_bot_token="test", bot_admin_ids=frozenset({1, 3})),
        provider_manager=manager,
    )
    client._register_commands()
    target = SimpleNamespace(
        user=SimpleNamespace(id=1),
        permissions=SimpleNamespace(manage_channels=True),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    await client._commands["budget"].callback(target)
    manager.complete.assert_not_called()
    assert target.response.send_message.call_args.kwargs["ephemeral"] is True
    assert "$10.0000" in target.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_budget_command_denies_nonadmin_without_reading_ledger():
    ledger = SimpleNamespace(snapshot=AsyncMock(side_effect=AssertionError("ledger read")))
    client = DiscordClient(
        BotConfig(discord_bot_token="test"),
        provider_manager=SimpleNamespace(budget=ledger),
    )
    client._register_commands()
    target = SimpleNamespace(
        user=SimpleNamespace(id=1),
        permissions=SimpleNamespace(manage_channels=False),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await client._commands["budget"].callback(target)

    ledger.snapshot.assert_not_awaited()
    assert target.response.send_message.await_args.kwargs["ephemeral"] is True
    assert target.response.send_message.await_args.args[0] == "I can't do that right now."
    command = client._commands["budget"]
    assert command.default_permissions.administrator is True


@pytest.mark.asyncio
async def test_status_does_not_promote_budget_report():
    manager = SimpleNamespace(
        get_provider=lambda provider: SimpleNamespace(default_model="test-model")
    )
    client = DiscordClient(
        BotConfig(discord_bot_token="test", bot_admin_ids=frozenset({1, 3})),
        provider_manager=manager,
    )
    client._register_commands()
    target = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        user=SimpleNamespace(id=3),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await client._commands["status"].callback(target)

    message = target.response.send_message.await_args.args[0]
    assert "budget" not in message.lower()
    assert "spend" not in message.lower()
