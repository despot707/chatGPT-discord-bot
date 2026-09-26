from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from src.providers import ImageInput, ProviderError, ProviderManager, ProviderType


def manager_with_three(**overrides: str):
    environ = {
        "GEMINI_API_KEY": "gemini-placeholder",
        "GROQ_API_KEY": "groq-placeholder",
        "OPENROUTER_API_KEY": "router-placeholder",
        "FALLBACK_PROVIDERS": "groq,openrouter",
    }
    environ.update(overrides)
    return ProviderManager(environ)


@pytest.mark.asyncio
@pytest.mark.parametrize("skip_reason", ["vision", "cooldown"])
async def test_skipped_candidates_do_not_exhaust_actual_attempt_budget(skip_reason):
    manager = manager_with_three(
        FALLBACK_PROVIDERS="gemini,groq,openrouter,ollama",
        OLLAMA_MODEL="local-vision-model",
        OLLAMA_SUPPORTS_VISION="true",
    )
    gemini = manager.get_provider(ProviderType.GEMINI)
    groq = manager.get_provider(ProviderType.GROQ)
    router = manager.get_provider(ProviderType.OPENROUTER)
    ollama = manager.get_provider(ProviderType.OLLAMA)
    gemini.chat_completion = AsyncMock(side_effect=ProviderError("busy", retryable=True))
    groq.chat_completion = AsyncMock(return_value="must not run")
    router.chat_completion = AsyncMock(return_value="must not run")
    ollama.chat_completion = AsyncMock(return_value="local answer")
    images = ()
    if skip_reason == "vision":
        images = (ImageInput(b"picture", "image/jpeg"),)
    else:
        manager._cooldown_until[ProviderType.GROQ] = time.monotonic() + 60
        manager._cooldown_until[ProviderType.OPENROUTER] = time.monotonic() + 60
    result = await manager.complete([{"role": "user", "content": "question"}], images=images)
    assert result.text == "local answer"
    assert result.attempted == (ProviderType.GEMINI, ProviderType.OLLAMA)
    groq.chat_completion.assert_not_awaited()
    router.chat_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_failure_uses_configured_fallback_and_reports_attempts():
    manager = manager_with_three()
    primary = manager.get_provider(ProviderType.GEMINI)
    fallback = manager.get_provider(ProviderType.GROQ)
    primary.chat_completion = AsyncMock(
        side_effect=ProviderError("temporarily unavailable", retryable=True)
    )
    fallback.chat_completion = AsyncMock(return_value="answer from backup")

    result = await manager.complete(
        [{"role": "user", "content": "question"}], max_tokens=44, request_timeout=5
    )

    assert result.text == "answer from backup"
    assert result.provider is ProviderType.GROQ
    assert result.model == fallback.default_model
    assert result.attempted == (ProviderType.GEMINI, ProviderType.GROQ)
    assert fallback.chat_completion.await_args.kwargs["model"] == fallback.default_model
    assert fallback.chat_completion.await_args.kwargs["max_tokens"] == 44
    assert fallback.chat_completion.await_args.kwargs["request_timeout"] <= 20


@pytest.mark.asyncio
async def test_numeric_sdk_overload_code_is_retryable():
    class NumericProviderError(Exception):
        code = 503

    manager = manager_with_three()
    primary = manager.get_provider(ProviderType.GEMINI)
    fallback = manager.get_provider(ProviderType.GROQ)
    primary.chat_completion = AsyncMock(side_effect=NumericProviderError("private upstream text"))
    fallback.chat_completion = AsyncMock(return_value="backup")

    result = await manager.complete([{"role": "user", "content": "question"}])

    assert result.text == "backup"
    assert result.attempted == (ProviderType.GEMINI, ProviderType.GROQ)


@pytest.mark.asyncio
async def test_refusal_or_permanent_error_does_not_fail_over():
    manager = manager_with_three()
    primary = manager.get_provider(ProviderType.GEMINI)
    fallback = manager.get_provider(ProviderType.GROQ)
    primary.chat_completion = AsyncMock(side_effect=ProviderError("blocked by safety policy"))
    fallback.chat_completion = AsyncMock(return_value="should not run")

    with pytest.raises(ProviderError, match="safety"):
        await manager.complete([{"role": "user", "content": "question"}])
    fallback.chat_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_authentication_errors_do_not_fail_over():
    manager = manager_with_three()
    primary = manager.get_provider(ProviderType.GEMINI)
    fallback = manager.get_provider(ProviderType.GROQ)
    primary.chat_completion = AsyncMock(side_effect=ProviderError("authentication failed"))
    fallback.chat_completion = AsyncMock(return_value="should not run")

    with pytest.raises(ProviderError, match="authentication"):
        await manager.complete([{"role": "user", "content": "question"}])
    fallback.chat_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_attempts_next_provider_within_total_deadline():
    manager = manager_with_three(PROVIDER_ATTEMPT_TIMEOUT_SECONDS="0.1")
    primary = manager.get_provider(ProviderType.GEMINI)
    fallback = manager.get_provider(ProviderType.GROQ)

    async def slow(*args, **kwargs):  # type: ignore[no-untyped-def]
        await asyncio.sleep(1)
        return "too late"

    primary.chat_completion = slow
    fallback.chat_completion = AsyncMock(return_value="backup")
    result = await manager.complete([{"role": "user", "content": "question"}], request_timeout=1)

    assert result.text == "backup"
    assert result.attempted == (ProviderType.GEMINI, ProviderType.GROQ)


@pytest.mark.asyncio
async def test_cancellation_propagates_without_starting_fallback():
    manager = manager_with_three()
    primary = manager.get_provider(ProviderType.GEMINI)
    fallback = manager.get_provider(ProviderType.GROQ)
    started = asyncio.Event()

    async def wait_forever(*args, **kwargs):  # type: ignore[no-untyped-def]
        started.set()
        await asyncio.Event().wait()

    primary.chat_completion = wait_forever
    fallback.chat_completion = AsyncMock(return_value="should not run")
    task = asyncio.create_task(manager.complete([{"role": "user", "content": "question"}]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fallback.chat_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_can_be_disabled_and_attempt_budget_is_capped():
    disabled = manager_with_three(ENABLE_PROVIDER_FALLBACK="false")
    primary = disabled.get_provider(ProviderType.GEMINI)
    fallback = disabled.get_provider(ProviderType.GROQ)
    primary.chat_completion = AsyncMock(side_effect=ProviderError("busy", retryable=True))
    fallback.chat_completion = AsyncMock(return_value="backup")
    with pytest.raises(ProviderError, match="busy"):
        await disabled.complete([{"role": "user", "content": "question"}])
    fallback.chat_completion.assert_not_awaited()

    budgeted = manager_with_three(MAX_PROVIDER_ATTEMPTS="2")
    gemini = budgeted.get_provider(ProviderType.GEMINI)
    groq = budgeted.get_provider(ProviderType.GROQ)
    router = budgeted.get_provider(ProviderType.OPENROUTER)
    gemini.chat_completion = AsyncMock(side_effect=ProviderError("busy", retryable=True))
    groq.chat_completion = AsyncMock(side_effect=ProviderError("busy", retryable=True))
    router.chat_completion = AsyncMock(return_value="third must not run")
    with pytest.raises(ProviderError, match="busy"):
        await budgeted.complete([{"role": "user", "content": "question"}])
    router.chat_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_image_request_skips_nonvision_provider_and_keeps_image():
    manager = manager_with_three(
        DEFAULT_PROVIDER="groq",
        GEMINI_API_KEY="gemini-placeholder",
        FALLBACK_PROVIDERS="gemini,openrouter",
    )
    groq = manager.get_provider(ProviderType.GROQ)
    gemini = manager.get_provider(ProviderType.GEMINI)
    groq.chat_completion = AsyncMock(return_value="must not run")
    gemini.chat_completion = AsyncMock(return_value="image inspected")
    image = ImageInput(b"picture", "image/jpeg")

    result = await manager.complete([{"role": "user", "content": "What is this?"}], images=(image,))

    assert result.provider is ProviderType.GEMINI
    assert result.attempted == (ProviderType.GEMINI,)
    assert gemini.chat_completion.await_args.kwargs["images"] == (image,)
    groq.chat_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_image_request_fails_clearly_when_no_configured_provider_supports_vision():
    manager = manager_with_three(DEFAULT_PROVIDER="groq", FALLBACK_PROVIDERS="openrouter")
    with pytest.raises(ProviderError, match="can analyze images"):
        await manager.complete(
            [{"role": "user", "content": "What is this?"}],
            images=(ImageInput(b"picture", "image/jpeg"),),
        )


@pytest.mark.asyncio
async def test_openrouter_model_override_must_be_free_without_paid_opt_in():
    manager = manager_with_three(
        DEFAULT_PROVIDER="gemini", FALLBACK_PROVIDERS="openrouter", OPENROUTER_MODEL="paid/model"
    )
    gemini = manager.get_provider(ProviderType.GEMINI)
    gemini.chat_completion = AsyncMock(side_effect=ProviderError("busy", retryable=True))
    with pytest.raises(ProviderError, match="paid models are disabled"):
        await manager.complete([{"role": "user", "content": "question"}])
