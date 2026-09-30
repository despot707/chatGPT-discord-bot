"""Small console-only logging setup for the bot."""

from __future__ import annotations

import logging
import sys


class CustomFormatter(logging.Formatter):
    """Readable console format without logging prompts or provider payloads."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")


def setup_logger(module_name: str) -> logging.Logger:
    """Return an INFO logger with one console handler and no import-time file I/O."""
    name = module_name.removesuffix(".py")
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(getattr(handler, "_chatgpt_bot_handler", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        handler.setFormatter(CustomFormatter())
        handler._chatgpt_bot_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


logger = setup_logger(__name__)
