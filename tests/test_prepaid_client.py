from unittest.mock import Mock

import pytest
from src.bot import DiscordClient
from src.config import BotConfig
from src.prepaid import Denied
from src.prepaid_client import plan_embed
from src.prepaid_runtime import SCOPE, PaidManager

from tests.test_bot import Manager, interaction


def test_plan_ui_is_finite_and_has_no_checkout():
    data = plan_embed().to_dict()
    assert "not enabled" in data["description"]
    assert "Basic · $0.99/month" in [f["name"] for f in data["fields"]]
    assert len([f for f in data["fields"] if "add-on" in f["name"]]) == 5
    assert sum(len(f["value"]) for f in data["fields"]) < 5000


@pytest.mark.asyncio
async def test_preview_does_not_replace_current_manager(monkeypatch):
    monkeypatch.setenv("PREPAID_MODE", "preview")
    legacy = Manager()
    client = DiscordClient(BotConfig(discord_bot_token="x"), provider_manager=legacy)
    assert client.provider_manager is legacy
    client._register_prepaid_commands()
    target = interaction()
    await client.tree.get_command("plans").callback(target)
    assert target.response.send_message.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_enforce_interaction_blocked_without_revenue(monkeypatch, tmp_path):
    from src import prepaid_runtime as pr

    monkeypatch.setenv("PREPAID_MODE", "enforce")
    fake = Mock()
    fake.core.side_effect = Denied("No paid plan")
    fake.ready.side_effect = Denied("Not approved")
    monkeypatch.setattr(pr, "_INSTANCE", fake)
    monkeypatch.setattr(pr, "free_interaction_allowed", lambda *args: True)
    legacy = Manager()
    client = DiscordClient(BotConfig(discord_bot_token="x"), provider_manager=legacy)
    assert isinstance(client.provider_manager, PaidManager)
    client._register_prepaid_commands()
    target = interaction()
    target.guild_id = 1
    target.command = Mock(name="chat")
    target.command.name = "chat"
    target.command.root_parent = None
    target.response.is_done = lambda: False
    assert await client.tree.interaction_check(target) is False
    assert legacy.provider.calls == []
    target.command.name = "profile"
    assert await client.tree.interaction_check(target) is True


@pytest.mark.asyncio
async def test_mention_response_cannot_bypass_request_slot(monkeypatch, tmp_path):
    from src import prepaid_runtime as pr

    monkeypatch.setenv("PREPAID_MODE", "enforce")
    fake = Mock()
    fake.core.side_effect = Denied("No paid plan")
    monkeypatch.setattr(pr, "_INSTANCE", fake)
    monkeypatch.setattr(pr, "free_interaction_allowed", lambda *args: True)
    legacy = Manager()
    client = DiscordClient(
        BotConfig(discord_bot_token="x", chat_database_path=None), provider_manager=legacy
    )
    with pytest.raises(Exception, match="No paid plan"):
        await client.respond((1, 2, 3), "hello", private=False)
    assert legacy.provider.calls == [] and SCOPE.get() is None


def test_allowance_errors_are_clear_not_generic_provider_errors():
    from src.aclient import public_error_message

    assert "Web search allowance" in public_error_message(
        Denied("Web search allowance is exhausted. See /plans.")
    )
