import pytest
from src.personas import (
    PERSONAS,
    get_available_personas,
    get_persona_prompt,
    is_admin_user,
    is_jailbreak_persona,
)


def test_safe_personas_are_available_without_admin(monkeypatch):
    monkeypatch.delenv("BOT_ADMIN_IDS", raising=False)
    monkeypatch.delenv("ADMIN_USER_IDS", raising=False)

    available = get_available_personas()

    assert {"standard", "creative", "technical", "casual"} <= set(available)
    assert not any(is_jailbreak_persona(name) for name in available)


def test_admin_ids_are_loaded_dynamically_and_trimmed(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_IDS", " 123, 456 ")

    assert is_admin_user("123")
    assert is_admin_user("456")
    assert is_admin_user(" 123 ")  # user IDs are normalized
    assert "jailbreak-v1" in get_available_personas("123")

    monkeypatch.setenv("BOT_ADMIN_IDS", "789")
    assert not is_admin_user("123")
    assert is_admin_user("789")


def test_legacy_admin_environment_name_remains_supported(monkeypatch):
    monkeypatch.delenv("BOT_ADMIN_IDS", raising=False)
    monkeypatch.setenv("ADMIN_USER_IDS", "987")

    assert is_admin_user("987")


def test_jailbreak_prompt_is_denied_to_non_admin(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_IDS", "123")

    with pytest.raises(PermissionError):
        get_persona_prompt("jailbreak-v1", "456")
    assert get_persona_prompt("jailbreak-v1", "123") == PERSONAS["jailbreak-v1"]


def test_unknown_persona_falls_back_to_standard():
    assert get_persona_prompt("not-a-persona") == PERSONAS["standard"]
