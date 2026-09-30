"""Temporary launch switch for all model-backed bot features."""

from __future__ import annotations

import logging
from typing import Mapping

logger = logging.getLogger(__name__)


def parse_ai_access_mode(environ: Mapping[str, str]) -> str:
    mode = environ.get("AI_ACCESS_MODE", "personal").strip().lower()
    if mode not in {"personal", "disabled"}:
        raise ValueError("AI_ACCESS_MODE must be personal or disabled")
    return mode


def ai_disabled(mode: str, operation: str) -> bool:
    """Log an attempted AI operation without recording prompts or credentials."""
    if mode == "disabled":
        logger.warning("AI_ACCESS_MODE=disabled blocked %s", operation)
        return True
    return False
