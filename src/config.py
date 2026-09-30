"""Validated, side-effect-free bot configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

OPENAI_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})


def _openai_reasoning_effort(env: Mapping[str, str]) -> Optional[str]:
    value = env.get("OPENAI_REASONING_EFFORT", "").strip().lower()
    if not value:
        return None
    if value not in OPENAI_REASONING_EFFORTS:
        raise ValueError(
            "OPENAI_REASONING_EFFORT must be none, minimal, low, medium, high, xhigh, or max"
        )
    return value


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
    hard_budget_enabled: bool = False
    allowed_guild_ids: frozenset[int] = frozenset()
    allowed_channel_ids: frozenset[int] = frozenset()
    bot_admin_ids: frozenset[int] = frozenset()
    replyall_channel_ids: frozenset[int] = frozenset()
    interaction_channel_ids: frozenset[int] = frozenset()
    automatic_context_count: int = 0
    enable_web_search: bool = False
    enable_openai_web_search: bool = False
    enable_web_browsing: bool = True
    web_max_bytes: int = 1_000_000
    web_max_chars: int = 12_000
    max_input_chars: int = 2000
    max_output_tokens: int = 1024
    openai_reasoning_effort: Optional[str] = None
    history_messages: int = 20
    history_chars: int = 24000
    max_sessions: int = 1000
    idle_ttl_seconds: int = 3600
    chat_database_path: Optional[str] = None
    chat_retention_days: int = 30
    max_concurrent_requests: int = 3
    cooldown_seconds: int = 5
    request_timeout_seconds: int = 60
    system_prompt: str = "You are a helpful assistant."
    steam_api_key: Optional[str] = field(default=None, repr=False)
    gaming_database_path: str = "data/gaming.sqlite3"
    enable_long_term_memory: bool = False
    memory_database_path: str = "data/memory.sqlite3"
    memory_context_items: int = 12
    discord_purchase_mode: str = "off"
    discord_application_id: int = 0
    discord_sku_map: str = ""

    @classmethod
    def from_env(
        cls, environ: Optional[Mapping[str, str]] = None, require_discord_token: bool = True
    ) -> "BotConfig":
        env = os.environ if environ is None else environ
        token = env.get("DISCORD_BOT_TOKEN", "").strip() or None
        if require_discord_token and not token:
            raise ValueError("Missing required environment variable: DISCORD_BOT_TOKEN")
        hard_budget_enabled = _bool(env, "HARD_BUDGET_ENABLED")
        from src.discord_purchases import purchase_mode, sku_map

        purchase = purchase_mode(env.get("DISCORD_PURCHASE_MODE", "off").strip().lower())
        mapped_skus = env.get("DISCORD_SKU_MAP", "").strip()
        sku_map(mapped_skus)
        app_id = _int(env, "DISCORD_APPLICATION_ID", 0, minimum=0)
        if purchase != "off" and (not mapped_skus or not app_id):
            raise ValueError("Discord purchase observation requires application ID and SKU map")
        default_provider = "openai" if hard_budget_enabled else "gemini"
        provider = env.get("DEFAULT_PROVIDER", default_provider).strip().lower() or default_provider
        if provider == "free":
            raise ValueError(
                "DEFAULT_PROVIDER=free is no longer supported; migrate to gemini, groq, openrouter, openai, claude, grok, or ollama"
            )
        if provider not in {"gemini", "groq", "openrouter", "openai", "claude", "grok", "ollama"}:
            raise ValueError(
                "DEFAULT_PROVIDER must be gemini, groq, openrouter, openai, claude, grok, or ollama"
            )
        default_model = env.get("DEFAULT_MODEL", "auto").strip() or "auto"
        if hard_budget_enabled:
            if provider != "openai":
                raise ValueError("Hard budget mode requires DEFAULT_PROVIDER=openai")
            if default_model not in {"auto", "gpt-6-luna"}:
                raise ValueError("Hard budget mode requires DEFAULT_MODEL=auto or gpt-6-luna")
            openai_model = env.get("OPENAI_MODEL", "gpt-6-luna").strip() or "gpt-6-luna"
            if openai_model != "gpt-6-luna":
                raise ValueError("Hard budget mode requires OPENAI_MODEL=gpt-6-luna")
        try:
            automatic_context_count = _int(env, "AUTOMATIC_CONTEXT_COUNT", 10, minimum=0)
            web_max_bytes = _int(env, "WEB_MAX_BYTES", 1_000_000)
            web_max_chars = _int(env, "WEB_MAX_CHARS", 12_000)
            if automatic_context_count > 20:
                raise ValueError("AUTOMATIC_CONTEXT_COUNT must be at most 20")
            if web_max_bytes > 2_000_000:
                raise ValueError("WEB_MAX_BYTES must be at most 2000000")
            if web_max_chars > 20_000:
                raise ValueError("WEB_MAX_CHARS must be at most 20000")
            return cls(
                discord_bot_token=token,
                default_provider=provider,
                default_model=default_model,
                enable_message_content=_bool(env, "ENABLE_MESSAGE_CONTENT"),
                enable_image_generation=_bool(env, "ENABLE_IMAGE_GENERATION"),
                allow_paid_providers=_bool(env, "ALLOW_PAID_PROVIDERS"),
                hard_budget_enabled=hard_budget_enabled,
                allowed_guild_ids=_ids(env, "ALLOWED_GUILD_IDS"),
                allowed_channel_ids=_ids(env, "ALLOWED_CHANNEL_IDS"),
                bot_admin_ids=_ids(env, "BOT_ADMIN_IDS")
                if env.get("BOT_ADMIN_IDS") is not None
                else _ids(env, "ADMIN_USER_IDS"),
                replyall_channel_ids=_ids(env, "REPLYALL_CHANNEL_IDS"),
                interaction_channel_ids=_ids(env, "INTERACTION_CHANNEL_IDS"),
                automatic_context_count=automatic_context_count,
                enable_web_search=not hard_budget_enabled
                and _bool(
                    env,
                    "ENABLE_WEB_SEARCH",
                    default=bool(env.get("TAVILY_API_KEY", "").strip()),
                ),
                enable_openai_web_search=_bool(env, "ENABLE_OPENAI_WEB_SEARCH"),
                enable_web_browsing=_bool(env, "ENABLE_WEB_BROWSING", default=True),
                web_max_bytes=web_max_bytes,
                web_max_chars=web_max_chars,
                max_input_chars=_int(env, "MAX_INPUT_CHARS", 2000),
                max_output_tokens=_int(env, "MAX_OUTPUT_TOKENS", 1024),
                openai_reasoning_effort=_openai_reasoning_effort(env),
                history_messages=_int(env, "HISTORY_MESSAGES", 20),
                history_chars=_int(env, "HISTORY_CHARS", 24000),
                max_sessions=_int(env, "MAX_SESSIONS", 1000),
                idle_ttl_seconds=_int(env, "IDLE_TTL_SECONDS", 3600),
                chat_database_path=env.get("CHAT_DATABASE_PATH", "data/chat.sqlite3").strip()
                or None,
                chat_retention_days=_int(env, "CHAT_RETENTION_DAYS", 30),
                max_concurrent_requests=_int(env, "MAX_CONCURRENT_REQUESTS", 3),
                cooldown_seconds=_int(env, "COOLDOWN_SECONDS", 5, minimum=0),
                request_timeout_seconds=_int(env, "REQUEST_TIMEOUT_SECONDS", 60),
                system_prompt=env.get("SYSTEM_PROMPT", "You are a helpful assistant."),
                steam_api_key=env.get("STEAM_API_KEY", "").strip() or None,
                gaming_database_path=env.get("GAMING_DATABASE_PATH", "").strip()
                or "data/gaming.sqlite3",
                enable_long_term_memory=_bool(env, "ENABLE_LONG_TERM_MEMORY", default=False),
                memory_database_path=env.get("MEMORY_DATABASE_PATH", "").strip()
                or "data/memory.sqlite3",
                memory_context_items=_int(env, "MEMORY_CONTEXT_ITEMS", 12, minimum=1),
                discord_purchase_mode=purchase,
                discord_application_id=app_id,
                discord_sku_map=mapped_skus,
            )
        except ValueError:
            raise
