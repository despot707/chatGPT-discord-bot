"""Discord bot entry point; importing this module does not create clients."""

from __future__ import annotations

import logging
import os

from src.aclient import DiscordClient as BaseDiscordClient
from src.catalog_client import CatalogClientMixin
from src.config import BotConfig
from src.prepaid_client import PrepaidClientMixin
from src.profile_client import ProfileClientMixin
from src.providers import ProviderManager
from src.runtime_storage import prepare_runtime_storage

logger = logging.getLogger(__name__)


class DiscordClient(PrepaidClientMixin, CatalogClientMixin, ProfileClientMixin, BaseDiscordClient):
    """Chat/gaming bot with private, member-entered settings instead of passive profiles."""


def run_discord_bot(config: BotConfig | None = None, provider_manager=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    prepare_runtime_storage()
    config = config or BotConfig.from_env()
    manager = provider_manager or ProviderManager(
        environ={**os.environ, "AI_ACCESS_MODE": config.ai_access_mode}
    )
    client = DiscordClient(config, provider_manager=manager)
    if not config.discord_bot_token:
        raise ValueError("Missing required environment variable: DISCORD_BOT_TOKEN")
    logger.info("Starting Discord bot")
    if config.ai_access_mode == "disabled":
        logger.warning("AI_ACCESS_MODE=disabled: AI features blocked; Discord and gaming active")
    client.run(config.discord_bot_token, log_handler=None)
