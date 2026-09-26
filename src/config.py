"""Validated, side-effect-free bot configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    value = env.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be true or false")


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 1) -> int:
    raw = env.get(name)
    try:
        value = default if raw is None or raw.strip() == "" else int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _ids(env: Mapping[str, str], name: str) -> frozenset[int]:
    raw = env.get(name, "")
    try:
        values = frozenset(int(part.strip()) for part in raw.split(",") if part.strip())
        if any(value <= 0 for value in values):
            raise ValueError
        return values
    except ValueError as exc:
        raise ValueError(f"{name} must be a comma-separated list of numeric IDs") from exc


@dataclass(frozen=True)
class BotConfig:
    discord_bot_token: Optional[str]
    default_provider: str = "gemini"
    default_model: str = "auto"
    enable_message_content: bool = False
    enable_image_generation: bool = False
    allow_paid_providers: bool = False
    allowed_guild_ids: frozenset[int] = frozenset()
    allowed_channel_ids: frozenset[int] = frozenset()
    bot_admin_ids: frozenset[int] = frozenset()
    replyall_channel_ids: frozenset[int] = frozenset()
    max_input_chars: int = 2000
    max_output_tokens: int = 1024
    history_messages: int = 20
    history_chars: int = 24000
    max_sessions: int = 1000
    idle_ttl_seconds: int = 3600
    max_concurrent_requests: int = 3
    cooldown_seconds: int = 5
    request_timeout_seconds: int = 60
    system_prompt: str = "You are a helpful assistant."

    @classmethod
    def from_env(
        cls, environ: Optional[Mapping[str, str]] = None, require_discord_token: bool = True
    ) -> "BotConfig":
        env = os.environ if environ is None else environ
        token = env.get("DISCORD_BOT_TOKEN", "").strip() or None
        if require_discord_token and not token:
            raise ValueError("Missing required environment variable: DISCORD_BOT_TOKEN")
        provider = env.get("DEFAULT_PROVIDER", "gemini").strip().lower() or "gemini"
        if provider == "free":
            raise ValueError(
                "DEFAULT_PROVIDER=free is no longer supported; migrate to gemini, openai, claude, grok, or ollama"
            )
        if provider not in {"gemini", "openai", "claude", "grok", "ollama"}:
            raise ValueError("DEFAULT_PROVIDER must be gemini, openai, claude, grok, or ollama")
        try:
            return cls(
                discord_bot_token=token,
                default_provider=provider,
                default_model=env.get("DEFAULT_MODEL", "auto").strip() or "auto",
                enable_message_content=_bool(env, "ENABLE_MESSAGE_CONTENT"),
                enable_image_generation=_bool(env, "ENABLE_IMAGE_GENERATION"),
                allow_paid_providers=_bool(env, "ALLOW_PAID_PROVIDERS"),
                allowed_guild_ids=_ids(env, "ALLOWED_GUILD_IDS"),
                allowed_channel_ids=_ids(env, "ALLOWED_CHANNEL_IDS"),
                bot_admin_ids=_ids(env, "BOT_ADMIN_IDS")
                if env.get("BOT_ADMIN_IDS") is not None
                else _ids(env, "ADMIN_USER_IDS"),
                replyall_channel_ids=_ids(env, "REPLYALL_CHANNEL_IDS"),
                max_input_chars=_int(env, "MAX_INPUT_CHARS", 2000),
                max_output_tokens=_int(env, "MAX_OUTPUT_TOKENS", 1024),
                history_messages=_int(env, "HISTORY_MESSAGES", 20),
                history_chars=_int(env, "HISTORY_CHARS", 24000),
                max_sessions=_int(env, "MAX_SESSIONS", 1000),
                idle_ttl_seconds=_int(env, "IDLE_TTL_SECONDS", 3600),
                max_concurrent_requests=_int(env, "MAX_CONCURRENT_REQUESTS", 3),
                cooldown_seconds=_int(env, "COOLDOWN_SECONDS", 5, minimum=0),
                request_timeout_seconds=_int(env, "REQUEST_TIMEOUT_SECONDS", 60),
                system_prompt=env.get("SYSTEM_PROMPT", "You are a helpful assistant."),
            )
        except ValueError:
            raise
