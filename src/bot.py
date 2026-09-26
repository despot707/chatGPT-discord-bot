"""Discord bot entry point; importing this module does not create clients."""

from __future__ import annotations

from src.aclient import DiscordClient
from src.config import BotConfig
from src.providers import ProviderManager


def run_discord_bot(config: BotConfig | None = None, provider_manager=None) -> None:
    config = config or BotConfig.from_env()
    manager = provider_manager or ProviderManager()
    client = DiscordClient(config, provider_manager=manager)
    if not config.discord_bot_token:
        raise ValueError("Missing required environment variable: DISCORD_BOT_TOKEN")
    client.run(config.discord_bot_token, log_handler=None)
