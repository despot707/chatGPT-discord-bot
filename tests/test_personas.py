"""Persona behavior after explicitly removing obsolete jailbreak presets."""

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
    assert {"standard", "creative", "technical", "casual"} == set(get_available_personas())
    assert not any(is_jailbreak_persona(n) for n in get_available_personas())


def test_admin_ids_are_loaded_dynamically_and_trimmed(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_IDS", " 123, 456 ")
    assert is_admin_user("123") and is_admin_user("456") and is_admin_user(" 123 ")
    assert get_available_personas("123") == get_available_personas("999")
    monkeypatch.setenv("BOT_ADMIN_IDS", "789")
    assert not is_admin_user("123") and is_admin_user("789")


def test_legacy_admin_environment_name_remains_supported(monkeypatch):
    monkeypatch.delenv("BOT_ADMIN_IDS", raising=False)
    monkeypatch.setenv("ADMIN_USER_IDS", "987")
    assert is_admin_user("987")
    monkeypatch.setenv("BOT_ADMIN_IDS", "")
    assert not is_admin_user("987")


def test_removed_jailbreak_names_fall_back_for_everyone(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_IDS", "123")
    for uid in ("123", "456"):
        for name in ("jailbreak-v1", "jailbreak-v2", "jailbreak-v3"):
            assert name not in get_available_personas(uid)
            assert get_persona_prompt(name, uid) == PERSONAS["standard"]


def test_unknown_persona_falls_back_to_standard():
    assert get_persona_prompt("not-a-persona") == PERSONAS["standard"]
