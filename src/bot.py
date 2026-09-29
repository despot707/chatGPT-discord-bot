"""Discord bot entry point; importing this module does not create clients."""
from __future__ import annotations

import logging
from src.aclient import DiscordClient as BaseDiscordClient
from src.config import BotConfig
from src.profile_client import ProfileClientMixin
from src.providers import ProviderManager
from src.runtime_storage import prepare_runtime_storage

logger = logging.getLogger(__name__)

class DiscordClient(ProfileClientMixin, BaseDiscordClient):
    """Chat/gaming bot with private, member-entered settings instead of passive profiles."""

def run_discord_bot(config: BotConfig | None = None, provider_manager=None) -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    prepare_runtime_storage()
    config = config or BotConfig.from_env()
    manager = provider_manager or ProviderManager()
    client = DiscordClient(config, provider_manager=manager)
    if not config.discord_bot_token:
        raise ValueError('Missing required environment variable: DISCORD_BOT_TOKEN')
    logger.info('Starting Discord bot')
    client.run(config.discord_bot_token, log_handler=None)
