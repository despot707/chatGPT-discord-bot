"""Keep unit tests independent of developer credentials and live services."""

from __future__ import annotations

import aiohttp
import httpx
import pytest
import requests

BOT_ENVIRONMENT = (
    "DISCORD_BOT_TOKEN",
    "DEFAULT_PROVIDER",
    "DEFAULT_MODEL",
    "ENABLE_MESSAGE_CONTENT",
    "ENABLE_IMAGE_GENERATION",
    "ALLOW_PAID_PROVIDERS",
    "ALLOWED_GUILD_IDS",
    "ALLOWED_CHANNEL_IDS",
    "BOT_ADMIN_IDS",
    "ADMIN_USER_IDS",
    "REPLYALL_CHANNEL_IDS",
    "MAX_INPUT_CHARS",
    "MAX_OUTPUT_TOKENS",
    "HISTORY_MESSAGES",
    "HISTORY_CHARS",
    "MAX_SESSIONS",
    "IDLE_TTL_SECONDS",
    "MAX_CONCURRENT_REQUESTS",
    "COOLDOWN_SECONDS",
    "REQUEST_TIMEOUT_SECONDS",
    "SYSTEM_PROMPT",
    "GEMINI_API_KEY",
    "GEMINI_KEY",
    "GEMINI_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_KEY",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "CLAUDE_KEY",
    "CLAUDE_MODEL",
    "XAI_API_KEY",
    "GROK_KEY",
    "GROK_MODEL",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
)


@pytest.fixture(autouse=True)
def isolate_bot_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent ambient tokens and provider settings from changing test behavior."""
    for name in BOT_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block common HTTP clients while leaving local event-loop sockets intact."""

    def denied(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Live network access is disabled in tests; use a mock transport.")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", denied)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", denied)
    monkeypatch.setattr(requests.Session, "request", denied)
