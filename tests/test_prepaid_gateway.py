from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from src.budget import BudgetExceeded, BudgetLedger, BudgetPolicy
from src.prepaid import Denied, Ledger
from src.prepaid_gateway import Gateway

from tests.test_prepaid import NOW, payment


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["chat", "reasoning", "search", "image"])
async def test_launch_pause_blocks_even_funded_gateway_before_network(monkeypatch, operation):
    monkeypatch.setenv("AI_ACCESS_MODE", "disabled")
    ready = Mock()
    ledger = Mock()
    client = Mock()
    budget = Mock()
    gateway = Gateway(ledger, client, ready=ready, budget=budget)
    with pytest.raises(Denied, match="I can't do that right now"):
        if operation == "image":
            await gateway.image(1, 2, "a cat")
        else:
            await gateway.complete(
                1,
                2,
                [{"role": "user", "content": "hello"}],
                reasoning=operation == "reasoning",
                search=operation == "search",
            )
    ready.assert_not_called()
    assert not ledger.mock_calls
    assert not client.mock_calls
    assert not budget.mock_calls


@pytest.fixture
def setup(tmp_path):
    ledger = Ledger(str(tmp_path / "paid.sqlite3"), clock=lambda: NOW)
    ledger.credit(payment(product="premium"))
    client = NS(
        responses=NS(
            input_tokens=NS(count=AsyncMock(return_value=NS(input_tokens=500))),
            create=AsyncMock(
                return_value=NS(
                    output_text="answer",
                    output=[],
                    model="gpt-6-luna",
                    usage=NS(input_tokens=500, output_tokens=100),
                )
            ),
        )
    )
    budget = BudgetLedger(
        BudgetPolicy(),
        str(tmp_path / "budget.sqlite3"),
        opening_month_spend_micros=0,
        clock=lambda: datetime.fromtimestamp(NOW, timezone.utc),
    )
    return Gateway(ledger, client, ready=lambda: None, budget=budget), ledger, client


@pytest.mark.asyncio
async def test_denied_account_never_calls_api(setup):
    gateway, _, client = setup
    with pytest.raises(Denied):
        await gateway.complete(2, 3, [{"role": "user", "content": "hi"}])
    client.responses.create.assert_not_called()
    client.responses.input_tokens.count.assert_not_called()


@pytest.mark.asyncio
async def test_chat_bounded_and_stored_false(setup):
    g, ledger, c = setup
    assert await g.complete(1, 3, [{"role": "user", "content": "hi"}]) == "answer"
    args = c.responses.create.call_args.kwargs
    assert args["max_output_tokens"] == 500 and args["store"] is False
    assert args["reasoning"] == {"effort": "none"} and args["service_tier"] == "default"
    assert "tools" not in args
    assert ledger.summary(1)["remaining"]["chat"] == 999
    assert g.budget.snapshot()["luna"]["monthly_spent_micros"] == 113
    assert g.budget.snapshot()["extras"]["monthly_spent_micros"] == 0


@pytest.mark.asyncio
async def test_excess_input_cancels_before_billable_call(setup):
    g, ledger, c = setup
    c.responses.input_tokens.count.return_value = NS(input_tokens=8001)
    with pytest.raises(Denied):
        await g.complete(1, 3, [{"role": "user", "content": "hi"}])
    c.responses.create.assert_not_called()
    assert ledger.summary(1)["remaining"]["chat"] == 1000
    assert g.budget.snapshot()["reserved_micros"] == 0


@pytest.mark.asyncio
async def test_timeout_does_not_restore_potentially_spent_money(setup):
    g, ledger, c = setup
    c.responses.create.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await g.complete(1, 3, [{"role": "user", "content": "hi"}])
    assert ledger.summary(1)["remaining"]["chat"] == 999
    assert c.responses.create.await_count == 1
    assert g.budget.snapshot()["reserved_micros"] == 1500


@pytest.mark.asyncio
async def test_reasoning_total_output_capped(setup):
    g, ledger, c = setup
    await g.complete(1, 3, [{"role": "user", "content": "hi"}], reasoning=True)
    assert c.responses.create.call_args.kwargs["max_output_tokens"] == 2000
    assert ledger.summary(1)["remaining"]["reasoning"] == 99
    assert g.budget.snapshot()["extras"]["monthly_spent_micros"] == 113


@pytest.mark.asyncio
async def test_unknown_usage_locks_gateway(setup):
    g, ledger, c = setup
    c.responses.create.return_value.usage = None
    with pytest.raises(Denied):
        await g.complete(1, 3, [{"role": "user", "content": "hi"}])
    with pytest.raises(Denied):
        ledger.reserve(1, 3, "chat")
    assert g.budget.snapshot()["locked"]


@pytest.mark.asyncio
async def test_search_is_explicit_single_call_fixed_model(setup):
    g, ledger, c = setup
    c.responses.create.return_value.output = [NS(type="web_search_call")]
    await g.complete(1, 3, [{"role": "user", "content": "search"}], search=True)
    args = c.responses.create.call_args.kwargs
    assert args["model"] == "gpt-6-luna" and args["max_tool_calls"] == 1
    assert args["tool_choice"] == "required"
    assert ledger.summary(1)["remaining"]["search"] == 19
    assert g.budget.snapshot()["extras"]["monthly_spent_micros"] == 40000


@pytest.mark.asyncio
async def test_optional_web_without_tool_use_is_ordinary_chat(setup):
    g, ledger, c = setup
    assert (
        await g.complete(1, 3, [{"role": "user", "content": "hello"}], allow_web=True) == "answer"
    )
    args = c.responses.create.call_args.kwargs
    assert args["model"] == "gpt-6-luna"
    assert args["tool_choice"] == "auto"
    assert args["max_tool_calls"] == 1
    assert ledger.summary(1)["remaining"]["chat"] == 999
    assert ledger.summary(1)["remaining"]["search"] == 20
    assert g.budget.snapshot()["luna"]["monthly_spent_micros"] == 113
    assert g.budget.snapshot()["extras"]["monthly_spent_micros"] == 0
    assert g.budget.snapshot()["reserved_micros"] == 0


@pytest.mark.asyncio
async def test_optional_web_with_tool_use_selects_search(setup):
    g, ledger, c = setup
    c.responses.create.return_value.output = [NS(type="web_search_call")]
    assert (
        await g.complete(1, 3, [{"role": "user", "content": "latest news"}], allow_web=True)
        == "answer"
    )
    assert ledger.summary(1)["remaining"]["chat"] == 1000
    assert ledger.summary(1)["remaining"]["search"] == 19
    assert g.budget.snapshot()["luna"]["monthly_spent_micros"] == 0
    assert g.budget.snapshot()["extras"]["monthly_spent_micros"] == 40000
    assert g.budget.snapshot()["reserved_micros"] == 0


@pytest.mark.asyncio
async def test_optional_web_unavailable_falls_back_to_disclosed_chat(setup):
    g, ledger, c = setup
    remaining = g.budget.snapshot()["extras"]["daily_remaining_micros"]
    g.budget.reserve("extras", remaining)
    assert (
        await g.complete(1, 3, [{"role": "user", "content": "latest news"}], allow_web=True)
        == "answer"
    )
    args = c.responses.create.call_args.kwargs
    assert "tools" not in args
    assert args["input"][0]["role"] == "developer"
    assert "Live web access is unavailable" in args["input"][0]["content"]
    assert ledger.summary(1)["remaining"]["chat"] == 999
    assert ledger.summary(1)["remaining"]["search"] == 20


@pytest.mark.asyncio
async def test_optional_web_without_search_entitlement_is_chat(setup, tmp_path):
    g, _, c = setup
    basic = Ledger(str(tmp_path / "basic.sqlite3"), clock=lambda: NOW)
    basic.credit(payment(product="basic"))
    g.ledger = basic
    await g.complete(1, 3, [{"role": "user", "content": "hello"}], allow_web=True)
    args = c.responses.create.call_args.kwargs
    assert "tools" not in args
    assert "Live web access is unavailable" in args["input"][0]["content"]
    assert basic.summary(1)["remaining"]["chat"] == 399


@pytest.mark.asyncio
async def test_optional_web_timeout_keeps_both_holds(setup):
    g, ledger, c = setup
    c.responses.create.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await g.complete(1, 3, [{"role": "user", "content": "latest news"}], allow_web=True)
    assert ledger.summary(1)["remaining"]["chat"] == 999
    assert ledger.summary(1)["remaining"]["search"] == 19
    assert g.budget.snapshot()["reserved_micros"] == 41500


@pytest.mark.asyncio
async def test_search_max_documented_context_still_uses_full_hold(setup):
    g, _, c = setup
    c.responses.create.return_value.output = [NS(type="web_search_call")]
    c.responses.create.return_value.usage.input_tokens = 136000
    await g.complete(1, 3, [{"role": "user", "content": "search"}], search=True)
    assert g.budget.snapshot()["extras"]["monthly_spent_micros"] == 40000


@pytest.mark.asyncio
async def test_search_contract_overrun_locks_both_ledgers(setup):
    g, ledger, c = setup
    c.responses.create.return_value.output = [NS(type="web_search_call")]
    c.responses.create.return_value.usage.input_tokens = 136001
    with pytest.raises(Denied, match="admitted bound"):
        await g.complete(1, 3, [{"role": "user", "content": "search"}], search=True)
    with pytest.raises(Denied):
        ledger.reserve(1, 3, "chat")
    assert g.budget.snapshot()["locked"]


@pytest.mark.asyncio
async def test_no_custom_tool_or_combination_escape(setup):
    g, _, c = setup
    with pytest.raises(Denied):
        await g.complete(1, 3, [{"role": "user", "content": "hi"}], search=True, reasoning=True)
    c.responses.create.assert_not_called()


@pytest.mark.asyncio
async def test_image_is_one_fixed_priced_image(setup):
    import base64

    g, ledger, c = setup
    c.images = NS(
        generate=AsyncMock(
            return_value=NS(
                usage=NS(input_tokens=100, output_tokens=439),
                data=[NS(b64_json=base64.b64encode(b"jpeg").decode())],
            )
        )
    )
    assert await g.image(1, 3, "A game controller") == b"jpeg"
    args = c.images.generate.call_args.kwargs
    assert args["n"] == 1 and args["size"] == "1024x1024" and args["quality"] == "medium"
    assert args["model"] == "gpt-image-2.5-flare-2026-09-08"
    assert ledger.summary(1)["remaining"]["images"] == 9
    assert g.budget.snapshot()["extras"]["monthly_spent_micros"] == 13670


@pytest.mark.asyncio
async def test_image_without_usage_pauses_paid_features(setup):
    g, ledger, c = setup
    c.images = NS(generate=AsyncMock(return_value=NS(usage=None)))
    with pytest.raises(Denied, match="usage could not be verified"):
        await g.image(1, 3, "A game controller")
    with pytest.raises(Denied):
        ledger.reserve(1, 3, "chat")
    assert g.budget.snapshot()["locked"]


@pytest.mark.asyncio
async def test_unfunded_image_does_not_call_provider(setup):
    g, _, c = setup
    c.images = NS(generate=AsyncMock())
    with pytest.raises(Denied):
        await g.image(2, 3, "A game controller")
    c.images.generate.assert_not_called()


@pytest.mark.asyncio
async def test_shared_daily_budget_blocks_before_any_network_and_restores_units(setup):
    g, ledger, c = setup
    remaining = g.budget.snapshot()["luna"]["daily_remaining_micros"]
    g.budget.reserve("luna", remaining)
    with pytest.raises(BudgetExceeded):
        await g.complete(1, 3, [{"role": "user", "content": "hello"}])
    c.responses.input_tokens.count.assert_not_called()
    c.responses.create.assert_not_called()
    assert ledger.summary(1)["remaining"]["chat"] == 1000


@pytest.mark.asyncio
async def test_search_hold_must_fit_before_token_count_or_dispatch(setup):
    g, ledger, c = setup
    remaining = g.budget.snapshot()["extras"]["daily_remaining_micros"]
    g.budget.reserve("extras", remaining)
    with pytest.raises(BudgetExceeded):
        await g.complete(1, 3, [{"role": "user", "content": "latest news"}], search=True)
    c.responses.input_tokens.count.assert_not_called()
    c.responses.create.assert_not_called()
    assert ledger.summary(1)["remaining"]["search"] == 20


@pytest.mark.asyncio
async def test_failed_image_admission_restores_server_allowance(setup):
    g, ledger, c = setup
    c.images = NS(generate=AsyncMock())
    remaining = g.budget.snapshot()["extras"]["daily_remaining_micros"]
    g.budget.reserve("extras", remaining)
    with pytest.raises(BudgetExceeded):
        await g.image(1, 3, "a cat")
    c.images.generate.assert_not_called()
    assert ledger.summary(1)["remaining"]["images"] == 10
