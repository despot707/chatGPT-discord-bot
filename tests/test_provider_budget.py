from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.budget import BudgetExceeded, BudgetLedger, BudgetPolicy
from src.providers import OpenAIProvider, ProviderError, ProviderManager, ProviderType


def _manager(tmp_path, **overrides):
    values = {
        "HARD_BUDGET_ENABLED": "true",
        "DEFAULT_PROVIDER": "openai",
        "OPENAI_API_KEY": "placeholder-key",
        "BUDGET_DATABASE_PATH": str(tmp_path / "budget.sqlite3"),
        "BUDGET_OPENING_MONTH_SPEND_USD": "0",
    }
    values.update(overrides)
    return ProviderManager(values)


def _usage_response(*, input_tokens=10, output_tokens=20, text="answer", **overrides):
    values = {
        "model": "gpt-6-luna",
        "service_tier": "default",
        "usage": SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        "output_text": text,
        "output": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _count(provider, *counts):
    provider.client.responses.input_tokens.count = AsyncMock(
        side_effect=[SimpleNamespace(input_tokens=value) for value in counts]
    )


@pytest.mark.asyncio
async def test_strict_manager_only_exposes_budgeted_luna_and_blocks_draw(tmp_path):
    manager = _manager(tmp_path, ALLOW_PAID_PROVIDERS="false", ENABLE_IMAGE_GENERATION="true")
    assert manager.get_available_providers() == [ProviderType.OPENAI]
    assert [model.name for model in manager.get_all_models()[ProviderType.OPENAI]] == ["gpt-6-luna"]
    assert manager.get_provider().default_model == "gpt-6-luna"
    assert str(manager.get_provider().client.base_url).rstrip("/") == "https://api.openai.com/v1"
    with pytest.raises(ProviderError, match="only permits OpenAI"):
        manager.get_provider(ProviderType.GEMINI)
    with pytest.raises(ProviderError, match="strict budget mode"):
        await manager.get_provider().generate_image("draw a cat")
    with pytest.raises(ProviderError, match="only permits the gpt-6-luna"):
        await manager.get_provider().chat_completion(
            [{"role": "user", "content": "hello"}], model="gpt-6-astra"
        )


def test_budget_settings_reject_bad_money_timezone_and_limits_without_opening_a_ledger(tmp_path):
    base = {
        "BUDGET_DATABASE_PATH": str(tmp_path / "must-not-exist.sqlite3"),
    }
    for key, value in (
        ("BUDGET_MONTHLY_USD", "10.01"),
        ("BUDGET_MONTHLY_USD", "NaN"),
        ("BUDGET_LUNA_USD", "11"),
        ("BUDGET_TIMEZONE", "No/Such_Zone"),
    ):
        with pytest.raises(ProviderError):
            ProviderManager.budget_settings({**base, key: value})
    assert not (tmp_path / "must-not-exist.sqlite3").exists()


@pytest.mark.asyncio
async def test_budgeted_luna_preflights_and_reserves_before_paid_generation(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(return_value=_usage_response())

    assert await provider.chat_completion([{"role": "user", "content": "hello"}]) == "answer"
    provider.client.responses.input_tokens.count.assert_awaited_once_with(
        model="gpt-6-luna",
        input=[{"role": "user", "content": "hello"}],
        reasoning={"effort": "none"},
    )
    request = provider.client.responses.create.await_args.kwargs
    assert request["store"] is False
    assert request["max_output_tokens"] == 500
    assert request["reasoning"] == {"effort": "none"}
    assert request["service_tier"] == "default"
    assert "temperature" not in request and "top_p" not in request
    assert manager.budget.snapshot()["luna"]["monthly_spent_micros"] == 12
    assert manager.budget.snapshot()["reserved_micros"] == 0


@pytest.mark.asyncio
async def test_count_failure_fails_closed_before_paid_generation(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    provider.client.responses.input_tokens.count = AsyncMock(side_effect=RuntimeError("private"))
    provider.client.responses.create = AsyncMock()

    with pytest.raises(ProviderError, match="could not verify the input token count"):
        await provider.chat_completion([{"role": "user", "content": "secret text"}])
    provider.client.responses.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_negative_output_limit_is_rejected_without_clamping_or_dispatch(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    provider.client.responses.input_tokens.count = AsyncMock()
    provider.client.responses.create = AsyncMock()

    with pytest.raises(ProviderError, match="max_tokens must be a positive integer"):
        await provider.chat_completion([{"role": "user", "content": "hello"}], max_tokens=-1)
    provider.client.responses.input_tokens.count.assert_not_awaited()
    provider.client.responses.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_web_falls_back_to_plain_luna_with_truthful_context_when_extras_too_small(
    tmp_path,
):
    manager = _manager(
        tmp_path,
        BUDGET_MONTHLY_USD="0.10",
        BUDGET_LUNA_USD="0.09",
    )
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(return_value=_usage_response())

    result = await provider.chat_completion(
        [{"role": "user", "content": "What happened today?"}], web_search=True
    )
    assert result == "answer"
    provider.client.responses.create.assert_awaited_once()
    request = provider.client.responses.create.await_args.kwargs
    assert "tools" not in request
    assert "Live web search is unavailable" in request["input"][0]["content"]


@pytest.mark.asyncio
async def test_optional_web_reservation_race_recounts_and_reserves_luna_only(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10, 20)
    provider.client.responses.create = AsyncMock(return_value=_usage_response(input_tokens=20))
    reserve_many = manager.budget.reserve_many

    def reject_web_holds(amounts):
        if "extras" in amounts:
            raise BudgetExceeded("Extras daily budget is exhausted.")
        return reserve_many(amounts)

    monkeypatch.setattr(manager.budget, "reserve_many", reject_web_holds)
    result = await provider.chat_completion(
        [{"role": "user", "content": "What happened today?"}], web_search=True
    )

    assert result == "answer"
    assert provider.client.responses.input_tokens.count.await_count == 2
    first, second = provider.client.responses.input_tokens.count.await_args_list
    assert "tools" in first.kwargs
    assert "tools" not in second.kwargs
    assert "Live web search is unavailable" in second.kwargs["input"][0]["content"]
    assert "tools" not in provider.client.responses.create.await_args.kwargs


@pytest.mark.asyncio
async def test_required_web_search_stops_before_token_count_when_extras_cannot_cover_hold(tmp_path):
    manager = _manager(
        tmp_path,
        BUDGET_MONTHLY_USD="0.10",
        BUDGET_LUNA_USD="0.09",
    )
    provider = manager.get_provider()
    provider.client.responses.input_tokens.count = AsyncMock()
    provider.client.responses.create = AsyncMock()

    with pytest.raises(ProviderError, match="Extras daily budget is exhausted"):
        await provider.chat_completion(
            [{"role": "user", "content": "search for news"}],
            web_search=True,
            require_web_search=True,
        )
    provider.client.responses.input_tokens.count.assert_not_awaited()
    provider.client.responses.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_required_web_search_is_forced_and_rejects_a_response_without_tool_use(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(return_value=_usage_response())

    with pytest.raises(ProviderError, match="did not perform the required web search"):
        await provider.chat_completion(
            [{"role": "user", "content": "search for news"}],
            web_search=True,
            require_web_search=True,
        )
    assert (
        provider.client.responses.input_tokens.count.await_args.kwargs["tool_choice"] == "required"
    )
    assert provider.client.responses.create.await_args.kwargs["tool_choice"] == "required"
    assert manager.budget.snapshot()["reserved_micros"] == 0


@pytest.mark.asyncio
async def test_web_search_reserves_extras_and_settles_actual_tool_and_token_usage(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(
        return_value=_usage_response(
            input_tokens=110,
            output_tokens=20,
            output=[
                SimpleNamespace(type="web_search_call", action=SimpleNamespace(type="search")),
                SimpleNamespace(type="web_search_call", action=SimpleNamespace(type="open_page")),
                SimpleNamespace(
                    type="web_search_call", action=SimpleNamespace(type="find_in_page")
                ),
            ],
        )
    )

    result = await provider.chat_completion(
        [{"role": "user", "content": "latest news"}], web_search=True
    )
    assert result == "answer"
    assert provider.client.responses.input_tokens.count.await_args.kwargs["tools"] == [
        {"type": "web_search", "search_context_size": "low"}
    ]
    request = provider.client.responses.create.await_args.kwargs
    assert request["max_tool_calls"] == 1
    assert request["parallel_tool_calls"] is False
    assert request["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    snapshot = manager.budget.snapshot()
    assert snapshot["luna"]["monthly_spent_micros"] == 12
    assert snapshot["extras"]["monthly_spent_micros"] == 10_013
    assert snapshot["locked"] is False
    assert snapshot["reserved_micros"] == 0


@pytest.mark.asyncio
async def test_two_search_actions_lock_and_log_only_sanitized_diagnostics(tmp_path, caplog):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(
        return_value=_usage_response(
            input_tokens=20,
            output=[
                SimpleNamespace(type="web_search_call", action=SimpleNamespace(type="search")),
                SimpleNamespace(type="web_search_call", action=SimpleNamespace(type="search")),
            ],
            id="resp_safe_123",
            status="completed",
            query="private search query",
        )
    )

    with pytest.raises(ProviderError, match="strict web-search call cap"):
        await provider.chat_completion(
            [{"role": "user", "content": "private prompt"}], web_search=True
        )

    snapshot = manager.budget.snapshot()
    assert snapshot["locked"] is True
    assert "provider_contract_search_call_cap_exceeded" in caplog.text
    assert "actions={'search': 2" in caplog.text
    assert "resp_safe_123" in caplog.text
    assert "private search query" not in caplog.text
    assert "private prompt" not in caplog.text


@pytest.mark.asyncio
async def test_web_activity_without_offered_tool_locks_the_ledger(tmp_path, caplog):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(
        return_value=_usage_response(
            input_tokens=10,
            output=[
                SimpleNamespace(type="web_search_call", action=SimpleNamespace(type="open_page"))
            ],
        )
    )

    with pytest.raises(ProviderError, match="when it was not offered"):
        await provider.chat_completion([{"role": "user", "content": "hello"}])

    assert manager.budget.snapshot()["locked"] is True
    assert "provider_contract_unrequested_web_activity" in caplog.text


@pytest.mark.asyncio
async def test_unknown_web_action_is_charged_as_a_search_conservatively(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(
        return_value=_usage_response(
            input_tokens=110,
            output=[
                SimpleNamespace(
                    type="web_search_call", action=SimpleNamespace(type="future_action")
                )
            ],
        )
    )

    assert (
        await provider.chat_completion(
            [{"role": "user", "content": "latest news"}], web_search=True
        )
        == "answer"
    )
    snapshot = manager.budget.snapshot()
    assert snapshot["extras"]["monthly_spent_micros"] == 10_013
    assert snapshot["locked"] is False


@pytest.mark.asyncio
async def test_unknown_web_action_is_not_proof_of_required_search(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(
        return_value=_usage_response(
            input_tokens=110,
            output=[
                SimpleNamespace(
                    type="web_search_call", action=SimpleNamespace(type="future_action")
                )
            ],
        )
    )

    with pytest.raises(ProviderError, match="did not perform the required web search"):
        await provider.chat_completion(
            [{"role": "user", "content": "latest news"}],
            web_search=True,
            require_web_search=True,
        )

    snapshot = manager.budget.snapshot()
    assert snapshot["extras"]["monthly_spent_micros"] == 10_013
    assert snapshot["reserved_micros"] == 0


@pytest.mark.asyncio
async def test_web_cost_above_reservation_locks_the_ledger(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 64_000)
    provider.client.responses.create = AsyncMock(
        return_value=_usage_response(
            input_tokens=320_001,
            output_tokens=20,
            output=[SimpleNamespace(type="web_search_call", action=SimpleNamespace(type="search"))],
        )
    )

    with pytest.raises(ProviderError, match="exceeded its reservation"):
        await provider.chat_completion(
            [{"role": "user", "content": "large research prompt"}], web_search=True
        )

    assert manager.budget.snapshot()["locked"] is True


@pytest.mark.asyncio
async def test_innate_web_capability_stays_quiet_when_no_search_is_needed(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(return_value=_usage_response())
    result = await provider.chat_completion([{"role": "user", "content": "hello"}], web_search=True)
    assert result == "answer"
    request = provider.client.responses.create.await_args.kwargs
    assert "tools" in request
    assert "tool_choice" not in request
    assert request["reasoning"] == {"effort": "none"}
    assert manager.budget.snapshot()["extras"]["monthly_spent_micros"] == 0
    assert manager.budget.snapshot()["reserved_micros"] == 0


@pytest.mark.asyncio
async def test_large_web_context_uses_long_context_rates_within_the_conservative_hold(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 64_000)
    provider.client.responses.create = AsyncMock(
        return_value=_usage_response(
            input_tokens=300_000,
            output_tokens=100,
            output=[SimpleNamespace(type="web_search_call")],
        )
    )

    assert (
        await provider.chat_completion(
            [{"role": "user", "content": "large research prompt"}], web_search=True
        )
        == "answer"
    )
    snapshot = manager.budget.snapshot()
    assert snapshot["luna"]["monthly_spent_micros"] == 16_075
    assert snapshot["extras"]["monthly_spent_micros"] == 69_000


@pytest.mark.asyncio
async def test_failed_generation_keeps_full_budget_reservations(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(side_effect=RuntimeError("upstream failed"))

    with pytest.raises(ProviderError):
        await provider.chat_completion([{"role": "user", "content": "hello"}])
    assert manager.budget.snapshot()["reserved_micros"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        _usage_response(input_tokens=None),
        _usage_response(output_tokens=True),
    ],
)
async def test_invalid_usage_keeps_reservation_and_blocks_future_spend(tmp_path, response):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(return_value=response)

    with pytest.raises(ProviderError):
        await provider.chat_completion([{"role": "user", "content": "hello"}])
    snapshot = manager.budget.snapshot()
    assert snapshot["reserved_micros"] > 0


@pytest.mark.asyncio
async def test_unexpected_model_locks_the_ledger(tmp_path):
    manager = _manager(tmp_path)
    provider = manager.get_provider()
    _count(provider, 10)
    provider.client.responses.create = AsyncMock(return_value=_usage_response(model="gpt-6-astra"))

    with pytest.raises(ProviderError, match="unexpected model"):
        await provider.chat_completion([{"role": "user", "content": "hello"}])
    snapshot = manager.budget.snapshot()
    assert snapshot["locked"] is True
    assert snapshot["blocked_reason"] == "ledger_locked"


@pytest.mark.asyncio
async def test_direct_injected_budget_also_blocks_image_generation(tmp_path):
    ledger = BudgetLedger(BudgetPolicy(), tmp_path / "direct.sqlite3", opening_month_spend_micros=0)
    provider = OpenAIProvider("placeholder-key", enable_image_generation=True, budget=ledger)
    generate = AsyncMock()
    provider.client.images.generate = generate

    with pytest.raises(ProviderError, match="disabled in strict budget mode"):
        await provider.generate_image("draw")
    generate.assert_not_awaited()
