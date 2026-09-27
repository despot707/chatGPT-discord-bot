"""Hermetic command-line checks for startup configuration validation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import main as main_module
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_SCRIPT = REPO_ROOT / "main.py"
SECRET_VALUES = ("test-discord-token", "test-provider-key")
BOT_ENV_KEYS = {
    "DISCORD_BOT_TOKEN",
    "STEAM_API_KEY",
    "GAMING_DATABASE_PATH",
    "GEMINI_API_KEY",
    "GEMINI_KEY",
    "OPENAI_API_KEY",
    "OPENAI_KEY",
    "OPENAI_REASONING_EFFORT",
    "ANTHROPIC_API_KEY",
    "CLAUDE_KEY",
    "XAI_API_KEY",
    "GROK_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "DEFAULT_PROVIDER",
    "ALLOW_PAID_PROVIDERS",
    "OLLAMA_MODEL",
    "OLLAMA_BASE_URL",
    "SYSTEM_PROMPT",
}


def run_check_config(
    overrides: dict[str, str] | None = None, *, env_file: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real CLI with a clean bot environment and no ambient credentials."""
    env = {key: value for key, value in os.environ.items() if key.upper() not in BOT_ENV_KEYS}
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env.update(overrides or {})
    command = [sys.executable, str(MAIN_SCRIPT)]
    if env_file is not None:
        command.extend(["--env-file", str(env_file)])
    command.append("--check-config")
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def test_explicit_environment_does_not_inherit_steam_key_or_database(monkeypatch, tmp_path):
    env_file = tmp_path / "gaming.env"
    env_file.write_text("DISCORD_BOT_TOKEN=test\nGEMINI_API_KEY=test\n", encoding="utf-8")
    monkeypatch.setenv("STEAM_API_KEY", "unrelated-steam-key")
    monkeypatch.setenv("GAMING_DATABASE_PATH", "unrelated-database.sqlite3")
    main_module.load_explicit_environment(env_file)
    config = main_module.validate_environment()
    assert config.steam_api_key is None
    assert config.gaming_database_path == "data/gaming.sqlite3"


def assert_no_secrets(output: str) -> None:
    for secret in SECRET_VALUES:
        assert secret not in output


def test_missing_discord_token_fails_cleanly():
    result = run_check_config({"OPENAI_API_KEY": "ambient-should-be-removed"})

    assert result.returncode == 2
    assert "DISCORD_BOT_TOKEN" in result.stdout
    assert "Traceback" not in combined_output(result)
    assert_no_secrets(combined_output(result))


@pytest.mark.parametrize(
    ("provider", "key"), [("groq", "GROQ_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")]
)
def test_free_fallback_provider_can_be_the_primary(provider, key):
    missing = run_check_config(
        {"DISCORD_BOT_TOKEN": SECRET_VALUES[0], "DEFAULT_PROVIDER": provider}
    )
    assert missing.returncode == 2
    assert key in missing.stdout
    valid = run_check_config(
        {"DISCORD_BOT_TOKEN": SECRET_VALUES[0], "DEFAULT_PROVIDER": provider, key: SECRET_VALUES[1]}
    )
    assert valid.returncode == 0
    assert_no_secrets(combined_output(valid))


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


def test_explicit_env_file_overrides_ambient_config_offline(tmp_path: Path):
    env_file = tmp_path / "bot.env"
    env_file.write_text(
        "DISCORD_BOT_TOKEN=file-token\nDEFAULT_PROVIDER=groq\nGROQ_API_KEY=file-provider-key\n",
        encoding="utf-8",
    )
    result = run_check_config(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "DISCORD_BOT_TOKEN": "ambient-token",
            "DEFAULT_PROVIDER": "openai",
            "ALLOW_PAID_PROVIDERS": "true",
            "OPENAI_API_KEY": "ambient-provider-key",
        },
        env_file=env_file,
    )

    assert result.returncode == 0
    assert "Configuration OK (provider: groq)." in result.stdout
    assert_no_secrets(combined_output(result))
    assert "ambient-token" not in combined_output(result)
    assert "ambient-provider-key" not in combined_output(result)


def test_explicit_env_file_does_not_fall_back_to_ambient_legacy_key(tmp_path: Path):
    env_file = tmp_path / "bot.env"
    env_file.write_text(
        "DISCORD_BOT_TOKEN=file-token\n"
        "DEFAULT_PROVIDER=openai\n"
        "ALLOW_PAID_PROVIDERS=true\n"
        "OPENAI_API_KEY=\n",
        encoding="utf-8",
    )
    result = run_check_config({"OPENAI_KEY": "ambient-legacy-key"}, env_file=env_file)

    assert result.returncode == 2
    assert "Missing API key for openai" in result.stdout
    assert "ambient-legacy-key" not in combined_output(result)


def test_explicit_env_file_does_not_inherit_omitted_provider(tmp_path: Path):
    env_file = tmp_path / "bot.env"
    env_file.write_text(
        "DISCORD_BOT_TOKEN=file-token\nGROQ_API_KEY=file-provider-key\n",
        encoding="utf-8",
    )
    result = run_check_config(
        {
            "DEFAULT_PROVIDER": "openai",
            "ALLOW_PAID_PROVIDERS": "true",
            "OPENAI_API_KEY": "ambient-provider-key",
        },
        env_file=env_file,
    )

    assert result.returncode == 2
    assert "Missing API key for gemini" in result.stdout
    assert "ambient-provider-key" not in combined_output(result)


def test_explicit_env_file_key_wins_and_does_not_interpolate_ambient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env_file = tmp_path / "bot.env"
    env_file.write_text(
        "DISCORD_BOT_TOKEN=file-token\n"
        "DEFAULT_PROVIDER=groq\n"
        "GROQ_API_KEY=file-provider-key\n"
        "OPENAI_API_KEY=${AMBIENT_PROVIDER_KEY}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GROQ_API_KEY", "ambient-provider-key")
    monkeypatch.setenv("AMBIENT_PROVIDER_KEY", "ambient-secret")

    captured: dict[str, str | None] = {}
    real_validate = main_module.validate_environment

    def capture_environment():
        captured["groq"] = os.environ.get("GROQ_API_KEY")
        captured["openai"] = os.environ.get("OPENAI_API_KEY")
        captured["unrelated"] = os.environ.get("AMBIENT_PROVIDER_KEY")
        return real_validate()

    monkeypatch.setattr(main_module, "validate_environment", capture_environment)
    assert main_module.main(["--env-file", str(env_file), "--check-config"]) == 0
    assert captured == {
        "groq": "file-provider-key",
        "openai": "${AMBIENT_PROVIDER_KEY}",
        "unrelated": "ambient-secret",
    }


def test_missing_explicit_env_file_fails_with_clear_sanitized_error(tmp_path: Path):
    result = run_check_config(env_file=tmp_path / "missing.env")

    assert result.returncode == 2
    assert "specified env file does not exist" in result.stdout
    assert "Traceback" not in combined_output(result)
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
