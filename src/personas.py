"""Personality presets for the Discord assistant."""

PERSONAS = {
    "standard": (
        "Use the main system instructions as your personality. Be direct, natural, candid, "
        "and responsive to the tone of the conversation."
    ),
    "creative": (
        "Lean into imagination, originality, humor, metaphor, storytelling, and unusual "
        "connections while remaining intellectually honest about factual claims."
    ),
    "technical": (
        "Respond like a strong technical collaborator. Be precise, detailed when useful, "
        "comfortable with code and systems, and explain tradeoffs without unnecessary hand-holding."
    ),
    "casual": (
        "Speak casually and naturally like a knowledgeable friend. Humor, slang, profanity, "
        "and playful disagreement are fine when they fit the conversation."
    ),
}

current_persona = "standard"


def get_persona_prompt(persona_name: str, user_id=None) -> str:
    """Return a personality preset, falling back to standard."""
    return PERSONAS.get(persona_name, PERSONAS["standard"])


def is_jailbreak_persona(persona_name: str) -> bool:
    """Legacy compatibility: jailbreak personas are no longer part of the bot."""
    return False


def is_admin_user(user_id=None) -> bool:
    """Legacy compatibility; authorization is enforced by DiscordClient/config."""
    return False


def get_available_personas(user_id=None) -> list[str]:
    """Return the personality presets available to users."""
    return list(PERSONAS)
