from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from src.bot import DiscordClient
from src.config import BotConfig
from src.prepaid import Denied
from src.prepaid_client import PLAN_SKUS, active_plan_skus, plan_embed, plan_view
from src.prepaid_runtime import SCOPE, PaidManager

from tests.test_bot import Manager, interaction


def test_plan_ui_is_finite_and_has_no_checkout():
    data = plan_embed().to_dict()
    assert "not enabled" in data["description"]
    assert "Basic · $1.99/month" in [f["name"] for f in data["fields"]]
    assert any("400 chat attempts" in f["value"] for f in data["fields"])
    assert len(data["fields"]) == 4
    assert "Profiles" in data["fields"][0]["value"]
    assert "no trial or free ai credits" in data["fields"][0]["value"].lower()
    assert "daily availability limits also apply" in data["footer"]["text"].lower()
    assert all("server operations" not in f["value"] for f in data["fields"])
    assert sum(len(f["value"]) for f in data["fields"]) < 5000
    assert plan_view(None) is None


def test_native_checkout_requires_all_live_gates_and_exact_sku_map(monkeypatch):
    from src.config import BotConfig

    config = BotConfig(
        discord_bot_token="x",
        hard_budget_enabled=True,
        discord_purchase_mode="enforce",
        discord_application_id=1365724363722068120,
        discord_sku_map=",".join(f"{sku}:{product}" for product, sku in PLAN_SKUS.items()),
    )
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    monkeypatch.setenv("DISCORD_PURCHASE_MODE", "enforce")
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "entitlement")
    skus = active_plan_skus(config)
    assert skus == {sku: product for product, sku in PLAN_SKUS.items()}
    assert active_plan_skus(replace(config, ai_access_mode="disabled")) is None
    assert active_plan_skus(replace(config, hard_budget_enabled=False)) is None
    view = plan_view(skus)
    assert view is not None
    assert {button.sku_id for button in view.children} == set(PLAN_SKUS.values())
    for button in view.children:
        component = button.to_component_dict()
        assert component["style"] == 6
        assert "sku_id" in component
        assert "label" not in component and "custom_id" not in component
    active = plan_embed(checkout_available=True).to_dict()
    assert "cancel through discord" in active["description"].lower()
    assert any("400 chat attempts" in f["value"] for f in active["fields"])

    monkeypatch.setenv("PREPAID_MODE", "preview")
    assert active_plan_skus(config) is None
    monkeypatch.setenv("PREPAID_MODE", "enforce")
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "settlement")
    assert active_plan_skus(config) is None
    assert plan_view(active_plan_skus(config)) is None

    bad_map = BotConfig(
        discord_bot_token="x",
        hard_budget_enabled=True,
        discord_purchase_mode="enforce",
        discord_application_id=1365724363722068120,
        discord_sku_map="999:basic",
    )
    monkeypatch.setenv("DISCORD_FUNDING_MODE", "entitlement")
    assert active_plan_skus(bad_map) is None


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
    assert "view" not in target.response.send_message.call_args.kwargs


@pytest.mark.asyncio
async def test_enforce_interaction_blocked_without_revenue(monkeypatch, tmp_path):
    from src import prepaid_runtime as pr

    monkeypatch.setenv("PREPAID_MODE", "enforce")
    fake = Mock()
    fake.core.side_effect = Denied("No paid plan")
    fake.ready.side_effect = Denied("Not approved")
    monkeypatch.setattr(pr, "_INSTANCE", fake)
    monkeypatch.setattr(pr, "free_interaction_allowed", lambda *args: True)
    monkeypatch.setattr("src.prepaid_client.DiscordPurchases", lambda *args, **kwargs: Mock())
    legacy = Manager()
    client = DiscordClient(
        BotConfig(
            discord_bot_token="x",
            discord_purchase_mode="enforce",
            discord_application_id=222,
            discord_sku_map="111:basic",
        ),
        provider_manager=legacy,
    )
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
    fake.core.side_effect = None
    target.command.name = "addgame"
    assert await client.tree.interaction_check(target) is True
    fake.core.assert_called_with(1, target.user.id)
    fake.ready.assert_called()
    fake.ready.reset_mock()
    async with client._request_slot((1, 2, target.user.id)):
        assert SCOPE.get() == (1, 2, target.user.id)
    fake.ready.assert_not_called()


@pytest.mark.asyncio
async def test_mention_response_cannot_bypass_request_slot(monkeypatch, tmp_path):
    from src import prepaid_runtime as pr

    monkeypatch.setenv("PREPAID_MODE", "enforce")
    fake = Mock()
    fake.ready.side_effect = Denied("No paid plan")
    fake.model_gateway.side_effect = Denied("No paid plan")
    monkeypatch.setattr(pr, "_INSTANCE", fake)
    monkeypatch.setattr(pr, "free_interaction_allowed", lambda *args: True)
    monkeypatch.setattr("src.prepaid_client.DiscordPurchases", lambda *args, **kwargs: Mock())
    legacy = Manager()
    client = DiscordClient(
        BotConfig(
            discord_bot_token="x",
            chat_database_path=None,
            discord_purchase_mode="enforce",
            discord_application_id=222,
            discord_sku_map="111:basic",
        ),
        provider_manager=legacy,
    )
    with pytest.raises(Denied, match="No paid plan") as denied:
        await client.respond((1, 2, 3), "hello", private=False)
    from src.aclient import public_error_message

    assert public_error_message(denied.value) == "I can't do that right now."
    assert legacy.provider.calls == [] and SCOPE.get() is None


@pytest.mark.asyncio
async def test_remember_paid_denial_uses_generic_notice(monkeypatch):
    from src import prepaid_runtime as pr

    monkeypatch.setenv("PREPAID_MODE", "enforce")
    fake = Mock()
    fake.model_gateway.side_effect = Denied("private paid quota detail")
    monkeypatch.setattr(pr, "_INSTANCE", fake)
    monkeypatch.setattr("src.prepaid_client.DiscordPurchases", lambda *args, **kwargs: Mock())
    notice = AsyncMock()
    monkeypatch.setattr("src.profile_client.private_notice", notice)
    client = DiscordClient(
        BotConfig(
            discord_bot_token="x",
            discord_purchase_mode="enforce",
            discord_application_id=222,
            discord_sku_map="111:basic",
        ),
        provider_manager=Manager(),
    )
    client.profile_store = Mock()
    client.profile_store.get.return_value = {"revision": 0, "games": []}
    target = interaction()
    target.channel_id = 2
    await client.propose_profile_change(target, "Save my birthday")
    assert notice.await_args.args[1] == "I can't do that right now."


def test_commercial_allowance_errors_are_generic_to_members(monkeypatch):
    from src.aclient import public_error_message

    monkeypatch.setenv("PREPAID_MODE", "enforce")
    assert (
        public_error_message(Denied("Web search allowance is exhausted. See /plans."))
        == "I can't do that right now."
    )
    monkeypatch.setenv("PREPAID_MODE", "off")
    assert "Web search allowance" in public_error_message(Denied("Web search allowance"))
