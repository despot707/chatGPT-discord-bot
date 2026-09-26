"""Hermetic command-line checks for startup configuration validation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_SCRIPT = REPO_ROOT / "main.py"
SECRET_VALUES = ("test-discord-token", "test-provider-key")
BOT_ENV_KEYS = {
    "DISCORD_BOT_TOKEN",
    "GEMINI_API_KEY",
    "GEMINI_KEY",
    "OPENAI_API_KEY",
    "OPENAI_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_KEY",
    "XAI_API_KEY",
    "GROK_KEY",
    "DEFAULT_PROVIDER",
    "ALLOW_PAID_PROVIDERS",
    "OLLAMA_MODEL",
    "OLLAMA_BASE_URL",
    "SYSTEM_PROMPT",
}


def run_check_config(overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the real CLI with a clean bot environment and no ambient credentials."""
    env = {key: value for key, value in os.environ.items() if key.upper() not in BOT_ENV_KEYS}
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env.update(overrides or {})
    return subprocess.run(
        [sys.executable, str(MAIN_SCRIPT), "--check-config"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def assert_no_secrets(output: str) -> None:
    for secret in SECRET_VALUES:
        assert secret not in output


def test_missing_discord_token_fails_cleanly():
    result = run_check_config({"OPENAI_API_KEY": "ambient-should-be-removed"})

    assert result.returncode == 2
    assert "DISCORD_BOT_TOKEN" in result.stdout
    assert "Traceback" not in combined_output(result)
    assert_no_secrets(combined_output(result))


def test_discord_token_without_gemini_key_fails_cleanly():
    result = run_check_config({"DISCORD_BOT_TOKEN": SECRET_VALUES[0]})

    assert result.returncode == 2
    assert "GEMINI_API_KEY" in result.stdout
    assert "Traceback" not in combined_output(result)
    assert_no_secrets(combined_output(result))


def test_valid_gemini_config_passes_without_disclosing_credentials():
    result = run_check_config(
        {
            "DISCORD_BOT_TOKEN": SECRET_VALUES[0],
            "GEMINI_API_KEY": SECRET_VALUES[1],
        }
    )

    assert result.returncode == 0
    assert "Configuration OK (provider: gemini)." in result.stdout
    assert_no_secrets(combined_output(result))


def test_legacy_free_provider_fails_with_migration_message():
    result = run_check_config({"DISCORD_BOT_TOKEN": SECRET_VALUES[0], "DEFAULT_PROVIDER": "free"})

    assert result.returncode == 2
    assert "DEFAULT_PROVIDER=free" in result.stdout
    assert "migrate" in result.stdout.lower()
    assert_no_secrets(combined_output(result))


def test_paid_provider_requires_explicit_opt_in():
    result = run_check_config(
        {
            "DISCORD_BOT_TOKEN": SECRET_VALUES[0],
            "DEFAULT_PROVIDER": "openai",
            "OPENAI_API_KEY": SECRET_VALUES[1],
        }
    )

    assert result.returncode == 2
    assert "ALLOW_PAID_PROVIDERS=true" in result.stdout
    assert_no_secrets(combined_output(result))


def test_paid_provider_with_opt_in_passes_offline():
    result = run_check_config(
        {
            "DISCORD_BOT_TOKEN": SECRET_VALUES[0],
            "DEFAULT_PROVIDER": "openai",
            "ALLOW_PAID_PROVIDERS": "true",
            "OPENAI_API_KEY": SECRET_VALUES[1],
        }
    )

    assert result.returncode == 0
    assert "Configuration OK (provider: openai)." in result.stdout
    assert_no_secrets(combined_output(result))


@pytest.mark.parametrize(
    ("overrides", "expected_code", "expected_message"),
    [
        (
            {
                "DISCORD_BOT_TOKEN": SECRET_VALUES[0],
                "DEFAULT_PROVIDER": "ollama",
            },
            2,
            "OLLAMA_MODEL is required",
        ),
        (
            {
                "DISCORD_BOT_TOKEN": SECRET_VALUES[0],
                "DEFAULT_PROVIDER": "ollama",
                "OLLAMA_MODEL": "llama3.2",
            },
            0,
            "Configuration OK (provider: ollama).",
        ),
    ],
)
def test_ollama_model_is_required_and_configured_model_passes(
    overrides: dict[str, str], expected_code: int, expected_message: str
):
    result = run_check_config(overrides)

    assert result.returncode == expected_code
    assert expected_message in result.stdout
    assert "Traceback" not in combined_output(result)
    assert_no_secrets(combined_output(result))
