import pytest
from src.config import BotConfig


def test_defaults_and_message_content_are_safe():
    config = BotConfig.from_env({"DISCORD_BOT_TOKEN": "test"})
    assert config.default_provider == "gemini"
    assert config.enable_message_content is False
    assert (config.max_input_chars, config.max_output_tokens, config.max_sessions) == (
        2000,
        1024,
        1000,
    )


@pytest.mark.parametrize("value", ["", "no", "0", "false"])
def test_false_boolean_values(value):
    assert not BotConfig.from_env(
        {"DISCORD_BOT_TOKEN": "test", "ENABLE_MESSAGE_CONTENT": value}
    ).enable_message_content


def test_free_provider_migration_and_bad_boolean_are_explicit():
    with pytest.raises(ValueError, match="migrate"):
        BotConfig.from_env({"DISCORD_BOT_TOKEN": "test", "DEFAULT_PROVIDER": "free"})
    with pytest.raises(ValueError, match="ENABLE_MESSAGE_CONTENT"):
        BotConfig.from_env({"DISCORD_BOT_TOKEN": "test", "ENABLE_MESSAGE_CONTENT": "sometimes"})


def test_allowlists_parse_positive_ids_and_invalid_values_fail():
    config = BotConfig.from_env({"DISCORD_BOT_TOKEN": "test", "ALLOWED_CHANNEL_IDS": "1, 2"})
    assert config.allowed_channel_ids == frozenset({1, 2})
    with pytest.raises(ValueError, match="ALLOWED_CHANNEL_IDS"):
        BotConfig.from_env({"DISCORD_BOT_TOKEN": "test", "ALLOWED_CHANNEL_IDS": "0"})
