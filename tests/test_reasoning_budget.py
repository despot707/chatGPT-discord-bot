"""Reasoning remains inside the existing output reservation and spend ceiling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.providers import ProviderError, ProviderManager


@pytest.mark.asyncio
@pytest.mark.parametrize("output_tokens", [500, 501])
async def test_reasoning_is_accounted_inside_total_output_and_cannot_bypass_cap(
    tmp_path, output_tokens
):
    manager = ProviderManager(
        {
            "HARD_BUDGET_ENABLED": "true",
            "OPENAI_API_KEY": "test-only",
            "OPENAI_REASONING_EFFORT": "high",
            "BUDGET_DATABASE_PATH": str(tmp_path / "budget.sqlite3"),
            "BUDGET_OPENING_MONTH_SPEND_USD": "0",
        }
    )
    provider = manager.get_provider()
    provider.client.responses.input_tokens.count = AsyncMock(
        return_value=SimpleNamespace(input_tokens=1000)
    )

    async def generated_response(**request):
        assert request["reasoning"] == {"effort": "none"}
        assert request["max_output_tokens"] == 500
        assert manager.budget.snapshot()["reserved_micros"] == 375
        return SimpleNamespace(
            model="gpt-6-luna",
            service_tier="default",
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=output_tokens,
                output_tokens_details=SimpleNamespace(reasoning_tokens=480),
            ),
            output_text="Short visible answer.",
            output=[],
        )

    provider.client.responses.create = AsyncMock(side_effect=generated_response)
    try:
        if output_tokens == 500:
            assert await manager.complete([{"role": "user", "content": "hello"}])
            snapshot = manager.budget.snapshot()
            # Charge all 500 output tokens, including the 480 hidden reasoning
            # tokens; never charge only visible output or count reasoning twice.
            assert snapshot["monthly_spent_micros"] == 375
            assert snapshot["reserved_micros"] == 0
        else:
            with pytest.raises(ProviderError, match="output-token cap"):
                await manager.complete([{"role": "user", "content": "hello"}])
            snapshot = manager.budget.snapshot()
            assert snapshot["locked"] is True
            assert snapshot["reserved_micros"] == 375
        assert provider.client.responses.input_tokens.count.await_args.kwargs["reasoning"] == {
            "effort": "none"
        }
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_explicit_reasoning_is_bounded_and_does_not_change_the_next_request(tmp_path):
    manager = ProviderManager(
        {
            "HARD_BUDGET_ENABLED": "true",
            "OPENAI_API_KEY": "test-only",
            "OPENAI_REASONING_EFFORT": "high",
            "BUDGET_DATABASE_PATH": str(tmp_path / "budget.sqlite3"),
            "BUDGET_OPENING_MONTH_SPEND_USD": "0",
        }
    )
    provider = manager.get_provider()
    provider.client.responses.input_tokens.count = AsyncMock(
        return_value=SimpleNamespace(input_tokens=1000)
    )
    calls = []

    async def generated_response(**request):
        calls.append(request)
        expected_hold = 1125 if len(calls) == 1 else 375
        assert manager.budget.snapshot()["reserved_micros"] == expected_hold
        return SimpleNamespace(
            model="gpt-6-luna",
            service_tier="default",
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=1500 if len(calls) == 1 else 20,
                output_tokens_details=SimpleNamespace(
                    reasoning_tokens=1400 if len(calls) == 1 else 0
                ),
            ),
            output_text="Answer.",
            output=[],
        )

    provider.client.responses.create = AsyncMock(side_effect=generated_response)
    try:
        await manager.complete(
            [{"role": "user", "content": "Think carefully about this."}],
            reasoning_requested=True,
            reasoning_max_tokens=999999,
        )
        assert manager.budget.snapshot()["monthly_spent_micros"] == 875
        await manager.complete([{"role": "user", "content": "Hi again."}])
        assert calls[0]["reasoning"] == {"effort": "low"}
        assert calls[0]["max_output_tokens"] == 2000
        assert calls[1]["reasoning"] == {"effort": "none"}
        assert calls[1]["max_output_tokens"] == 500
        assert all("tools" not in call for call in calls)
        assert provider.client.responses.input_tokens.count.await_args_list[0].kwargs[
            "reasoning"
        ] == {"effort": "low"}
        assert manager.budget.snapshot()["reserved_micros"] == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_invalid_reasoning_flag_cannot_enable_paid_thinking(tmp_path):
    manager = ProviderManager(
        {
            "HARD_BUDGET_ENABLED": "true",
            "OPENAI_API_KEY": "test-only",
            "BUDGET_DATABASE_PATH": str(tmp_path / "budget.sqlite3"),
            "BUDGET_OPENING_MONTH_SPEND_USD": "0",
        }
    )
    provider = manager.get_provider()
    provider.client.responses.input_tokens.count = AsyncMock()
    provider.client.responses.create = AsyncMock()
    try:
        with pytest.raises(ProviderError, match="boolean"):
            await manager.complete([{"role": "user", "content": "Hi"}], reasoning_requested="true")
        provider.client.responses.input_tokens.count.assert_not_awaited()
        provider.client.responses.create.assert_not_awaited()
    finally:
        await manager.close()
