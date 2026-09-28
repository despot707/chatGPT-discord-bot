"""Verify integration against the installed Discord library without logging in."""
import asyncio

import pytest


def test_real_client_registers_birthday_commands():
    pytest.importorskip('discord')
    from src.bot import DiscordClient
    from src.config import BotConfig
    from src.providers import ProviderManager

    async def exercise():
        client = DiscordClient(BotConfig(discord_bot_token='offline-test', enable_long_term_memory=False),
                               provider_manager=ProviderManager({}))
        try:
            client._register_commands()
            client._register_birthday_commands()
            assert client.tree.get_command('birthdays') is not None
            assert client.tree.get_command('birthday_scan_status') is not None
            assert client.tree.get_command('birthday_forget') is not None
            assert client.tree.get_command('chat') is not None
        finally:
            await client.close()
    asyncio.run(exercise())
