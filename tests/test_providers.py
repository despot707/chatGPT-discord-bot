from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.providers import (
    ClaudeProvider,
    GeminiProvider,
    GrokProvider,
    ModelInfo,
    OllamaProvider,
    OpenAIProvider,
    ProviderError,
    ProviderManager,
    ProviderType,
)


def test_model_info_shape():
    info = ModelInfo("example", ProviderType.GEMINI, "desc", True, False)
    assert (info.name, info.provider, info.description) == ("example", ProviderType.GEMINI, "desc")
    assert info.supports_vision is True
    assert info.supports_image_generation is False


@pytest.mark.asyncio
async def test_gemini_sends_entire_conversation_in_one_async_request():
    provider = GeminiProvider("placeholder-key")
    call = AsyncMock(return_value=SimpleNamespace(text="answer", prompt_feedback=None))
    provider.client.aio.models.generate_content = call
    messages = [
        {"role": "system", "content": "Be concise"},
        {"role": "user", "content": "Question one"},
        {"role": "assistant", "content": "Answer one"},
        {"role": "user", "content": "Follow up"},
    ]

    assert (
        await provider.chat_completion(messages, model="exact-gemini-id", max_tokens=123)
        == "answer"
    )
    call.assert_awaited_once()
    args = call.await_args.kwargs
    assert args["model"] == "exact-gemini-id"
    assert [(item.role, item.parts[0].text) for item in args["contents"]] == [
        ("user", "Question one"),
        ("model", "Answer one"),
        ("user", "Follow up"),
    ]
    assert args["config"].system_instruction == "Be concise"
    assert args["config"].max_output_tokens == 123


@pytest.mark.asyncio
async def test_gemini_empty_or_blocked_response_is_clear():
    provider = GeminiProvider("placeholder-key")
    provider.client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(text=None, prompt_feedback={"block_reason": "SAFETY"})
    )
    with pytest.raises(ProviderError, match="blocked"):
        await provider.chat_completion([{"role": "user", "content": "hello"}])
    provider.client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(text=None, prompt_feedback=None)
    )
    with pytest.raises(ProviderError, match="no text"):
        await provider.chat_completion([{"role": "user", "content": "hello"}])
    provider.client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(
            text=None,
            prompt_feedback=None,
            candidates=[SimpleNamespace(finish_reason="SAFETY", content=SimpleNamespace(parts=[]))],
        )
    )
    with pytest.raises(ProviderError, match="blocked"):
        await provider.chat_completion([{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_openai_uses_responses_api_without_storage_and_exact_model():
    provider = OpenAIProvider("placeholder-key")
    create = AsyncMock(return_value=SimpleNamespace(output_text="OpenAI answer", output=[]))
    provider.client.responses.create = create
    messages = [{"role": "user", "content": "hello"}]

    assert (
        await provider.chat_completion(messages, "caller-selected-model", max_tokens=99)
        == "OpenAI answer"
    )
    create.assert_awaited_once_with(
        model="caller-selected-model", input=messages, store=False, max_output_tokens=99
    )


@pytest.mark.asyncio
async def test_openai_empty_and_refusal_are_not_reported_as_success():
    provider = OpenAIProvider("placeholder-key")
    provider.client.responses.create = AsyncMock(
        return_value=SimpleNamespace(output_text="", output=[SimpleNamespace(type="refusal")])
    )
    with pytest.raises(ProviderError, match="declined"):
        await provider.chat_completion([{"role": "user", "content": "hello"}])
    provider.client.responses.create = AsyncMock(
        return_value=SimpleNamespace(output_text="", output=[])
    )
    with pytest.raises(ProviderError, match="no text"):
        await provider.chat_completion([{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_grok_uses_xai_chat_completions_and_passes_model_unchanged():
    provider = GrokProvider("placeholder-key")
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Grok answer"))]
        )
    )
    provider.client.chat.completions.create = create
    messages = [{"role": "user", "content": "hello"}]

    assert await provider.chat_completion(messages, "my-grok-model", max_tokens=80) == "Grok answer"
    create.assert_awaited_once_with(model="my-grok-model", messages=messages, max_tokens=80)
    assert str(provider.client.base_url).rstrip("/") == "https://api.x.ai/v1"


@pytest.mark.asyncio
async def test_claude_keeps_system_and_conversation_messages():
    provider = ClaudeProvider("placeholder-key")
    create = AsyncMock(
        return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="Claude answer")])
    )
    provider.client.messages.create = create
    messages = [
        {"role": "system", "content": "System one"},
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Prior answer"},
    ]

    assert (
        await provider.chat_completion(messages, "my-claude-model", max_tokens=88)
        == "Claude answer"
    )
    create.assert_awaited_once_with(
        model="my-claude-model",
        messages=[
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Prior answer"},
        ],
        max_tokens=88,
        system="System one",
    )


def test_manager_defaults_to_gemini_and_uses_its_free_tier_key_only():
    manager = ProviderManager({"GEMINI_API_KEY": "placeholder", "OPENAI_API_KEY": "never-used"})
    assert manager.current_provider is ProviderType.GEMINI
    assert manager.get_available_providers() == [ProviderType.GEMINI]
    assert manager.get_provider().default_model == "gemini-3.5-flash-lite"
    with pytest.raises(ProviderError, match="charges"):
        manager.get_provider(ProviderType.OPENAI)


def test_manager_reports_missing_gemini_credentials_and_free_migration():
    manager = ProviderManager({})
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        manager.get_provider()
    with pytest.raises(ProviderError, match="g4f scraper was removed"):
        manager.get_provider(ProviderType.FREE)
    with pytest.raises(ProviderError, match="g4f scraper was removed"):
        ProviderManager({"DEFAULT_PROVIDER": "free"}).get_provider()


def test_paid_provider_requires_explicit_allow_flag_and_legacy_key_alias_works():
    no_opt_in = ProviderManager({"OPENAI_KEY": "placeholder"})
    assert ProviderType.OPENAI not in no_opt_in.get_available_providers()
    allowed = ProviderManager(
        {
            "ALLOW_PAID_PROVIDERS": "true",
            "OPENAI_KEY": "placeholder",
            "OPENAI_MODEL": "operator-model",
        }
    )
    assert allowed.get_available_providers() == [ProviderType.OPENAI]
    assert allowed.get_provider(ProviderType.OPENAI).default_model == "operator-model"


def test_manager_captures_openai_image_generation_opt_in():
    disabled = ProviderManager({"ALLOW_PAID_PROVIDERS": "yes", "OPENAI_API_KEY": "placeholder"})
    assert disabled.get_provider(ProviderType.OPENAI).supports_image_generation() is False
    enabled = ProviderManager(
        {
            "ALLOW_PAID_PROVIDERS": "yes",
            "OPENAI_API_KEY": "placeholder",
            "ENABLE_IMAGE_GENERATION": "true",
        }
    )
    assert enabled.get_provider(ProviderType.OPENAI).supports_image_generation() is True


def test_manager_accepts_canonical_claude_and_grok_env_and_default_model_override():
    manager = ProviderManager(
        {
            "ALLOW_PAID_PROVIDERS": "1",
            "ANTHROPIC_API_KEY": "claude-placeholder",
            "XAI_API_KEY": "grok-placeholder",
            "DEFAULT_PROVIDER": "grok",
            "CLAUDE_MODEL": "",
            "GROK_MODEL": "",
        }
    )
    assert manager.get_available_providers() == [ProviderType.CLAUDE, ProviderType.GROK]
    assert isinstance(manager.get_provider(), GrokProvider)
    assert manager.get_provider().default_model == "grok-4.7"
    assert manager.get_provider(ProviderType.CLAUDE).default_model == "claude-haiku-4-5-20251001"


def test_manager_supports_explicit_local_ollama_without_downloading_models():
    manager = ProviderManager(
        {
            "DEFAULT_PROVIDER": "ollama",
            "OLLAMA_MODEL": "already-installed-model",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1",
        }
    )
    provider = manager.get_provider()
    assert isinstance(provider, OllamaProvider)
    assert provider.default_model == "already-installed-model"
    assert str(provider.client.base_url).rstrip("/") == "http://127.0.0.1:11434/v1"
    with pytest.raises(ProviderError, match="set OLLAMA_MODEL"):
        ProviderManager({"DEFAULT_PROVIDER": "ollama"}).get_provider()


@pytest.mark.asyncio
async def test_ollama_uses_openai_compatible_chat_completions():
    provider = OllamaProvider("already-installed-model")
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="local answer"))]
        )
    )
    provider.client.chat.completions.create = create
    messages = [{"role": "user", "content": "hello"}]
    assert await provider.chat_completion(messages, max_tokens=20) == "local answer"
    create.assert_awaited_once_with(
        model="already-installed-model", messages=messages, max_tokens=20
    )
    assert provider.supports_image_generation() is False


@pytest.mark.asyncio
async def test_provider_errors_do_not_expose_sdk_error_text_or_keys():
    provider = OpenAIProvider("secret-placeholder-key")
    provider.client.responses.create = AsyncMock(
        side_effect=RuntimeError("secret-placeholder-key / private prompt / upstream payload")
    )
    with pytest.raises(ProviderError) as caught:
        await provider.chat_completion([{"role": "user", "content": "private prompt"}])
    message = str(caught.value)
    assert "secret-placeholder-key" not in message
    assert "private prompt" not in message
    assert "upstream payload" not in message


@pytest.mark.asyncio
async def test_openai_image_generation_is_disabled_unless_explicitly_enabled(monkeypatch):
    provider = OpenAIProvider("placeholder-key")
    with pytest.raises(ProviderError, match="disabled"):
        await provider.generate_image("image prompt")
    generate = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(b64_json="aGVsbG8=", url=None)])
    )
    provider.client.images.generate = generate
    enabled = OpenAIProvider("placeholder-key", enable_image_generation=True)
    enabled.client.images.generate = generate
    assert enabled.supports_image_generation() is True
    assert await enabled.generate_image("image prompt") == b"hello"
    generate.assert_awaited_once()
    assert generate.await_args.kwargs["model"] == "gpt-image-1"


@pytest.mark.asyncio
async def test_async_clients_can_be_closed():
    manager = ProviderManager({"GEMINI_API_KEY": "placeholder"})
    manager.get_provider().client.aio.aclose = AsyncMock()
    await manager.close()
    manager.get_provider().client.aio.aclose.assert_awaited_once()
