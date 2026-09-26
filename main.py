#!/usr/bin/env python3
"""Validated Discord bot startup."""

from __future__ import annotations

import argparse
import os
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from src.config import BotConfig

KEY_NAMES = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_KEY"),
    "openai": ("OPENAI_API_KEY", "OPENAI_KEY"),
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_KEY"),
    "grok": ("XAI_API_KEY", "GROK_KEY"),
    "groq": ("GROQ_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
}


def validate_environment(environ=None) -> BotConfig:
    env = os.environ if environ is None else environ
    config = BotConfig.from_env(env)
    if config.default_provider in {"openai", "claude", "grok"} and not config.allow_paid_providers:
        raise ValueError(f"{config.default_provider} requires ALLOW_PAID_PROVIDERS=true")
    key_names = KEY_NAMES.get(config.default_provider, ())
    if key_names and not any(env.get(name, "").strip() for name in key_names):
        alias = f" (legacy alias: {key_names[1]})" if len(key_names) > 1 else ""
        raise ValueError(
            f"Missing API key for {config.default_provider}; set {key_names[0]}{alias}"
        )
    if config.default_provider == "ollama":
        model = env.get("OLLAMA_MODEL", "").strip()
        endpoint = env.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip()
        parsed = urlsplit(endpoint)
        if not model:
            raise ValueError("OLLAMA_MODEL is required when DEFAULT_PROVIDER=ollama")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OLLAMA_BASE_URL must be a valid http(s) URL")
    if not env.get("SYSTEM_PROMPT"):
        prompt_path = Path(__file__).resolve().with_name("system_prompt.txt")
        if prompt_path.is_file():
            config = replace(config, system_prompt=prompt_path.read_text(encoding="utf-8"))
    return config


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="ChatGPT Discord bot")
    parser.add_argument(
        "--check-config", action="store_true", help="validate configuration without connecting"
    )
    args = parser.parse_args(argv)
    try:
        config = validate_environment()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 2
    if args.check_config:
        print(f"Configuration OK (provider: {config.default_provider}).")
        return 0
    from src.bot import run_discord_bot

    run_discord_bot(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
