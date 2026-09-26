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


def test_discord_context_and_web_limits_are_bounded():
    config = BotConfig.from_env(
        {
            "DISCORD_BOT_TOKEN": "test",
            "ENABLE_MESSAGE_CONTENT": "true",
            "INTERACTION_CHANNEL_IDS": "21,22",
            "AUTOMATIC_CONTEXT_COUNT": "5",
            "ENABLE_WEB_SEARCH": "true",
            "TAVILY_API_KEY": "configured",
        }
    )
    assert config.interaction_channel_ids == frozenset({21, 22})
    assert config.automatic_context_count == 5
    assert config.enable_web_search
    with pytest.raises(ValueError, match="AUTOMATIC_CONTEXT_COUNT"):
        BotConfig.from_env({"DISCORD_BOT_TOKEN": "test", "AUTOMATIC_CONTEXT_COUNT": "21"})
    with pytest.raises(ValueError, match="WEB_MAX_BYTES"):
        BotConfig.from_env({"DISCORD_BOT_TOKEN": "test", "WEB_MAX_BYTES": "2000001"})


def test_openai_reasoning_effort_is_optional_and_validated_without_echoing_value():
    assert BotConfig.from_env({"DISCORD_BOT_TOKEN": "test"}).openai_reasoning_effort is None
    assert (
        BotConfig.from_env(
            {"DISCORD_BOT_TOKEN": "test", "OPENAI_REASONING_EFFORT": "low"}
        ).openai_reasoning_effort
        == "low"
    )
    invalid_value = "private-invalid-value"
    with pytest.raises(ValueError) as caught:
        BotConfig.from_env({"DISCORD_BOT_TOKEN": "test", "OPENAI_REASONING_EFFORT": invalid_value})
    assert "OPENAI_REASONING_EFFORT" in str(caught.value)
    assert invalid_value not in str(caught.value)
