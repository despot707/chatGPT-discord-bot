"""Private catalog selection and opt-in player matching, with no account linking."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from src.bot_branding import apply_branding
from src.game_catalog import GameCatalog
from src.profile_ui import CatalogResultsPanel, ProfilePanel, clean, private_notice

if TYPE_CHECKING:
    from src.profile_client import ProfileClientMixin as _CatalogBase
else:
    _CatalogBase = object

logger = logging.getLogger(__name__)


class CatalogClientMixin(_CatalogBase):
    catalog: GameCatalog | None = None
    _catalog_task: asyncio.Task | None = None

    async def _catalog(self):
        if self.catalog is not None:
            return self.catalog
        if self._catalog_task is None or (
            self._catalog_task.done() and self._catalog_task.exception() is not None
        ):
            self._catalog_task = asyncio.create_task(
                asyncio.to_thread(
                    GameCatalog, os.getenv("GAME_CATALOG_DATABASE_PATH", "data/catalog.sqlite3")
                )
            )
        self.catalog = await asyncio.shield(self._catalog_task)
        return self.catalog

    async def setup_hook(self):
        self._register_catalog_commands()
        await super().setup_hook()

    async def on_ready(self):
        await super().on_ready()
        if getattr(self, "_catalog_ready_started", False):
            return
        self._catalog_ready_started = True
        try:
            catalog = await self._catalog()
            logger.info(
                "Game catalog ready: %s canonical titles; local autocomplete enabled", catalog.count
            )
        except Exception as exc:
            logger.warning("Game catalog unavailable (%s)", type(exc).__name__)
        try:
            await apply_branding(self)
        except Exception as exc:
            logger.warning("Branding initialization unavailable (%s)", type(exc).__name__)
        logger.info(
            "Recent context configuration: message_content=%s automatic_messages=%s",
            self.config.enable_message_content,
            self.config.automatic_context_count,
        )

    async def show_game_search(self, interaction, query="", *, defaults=None):
        scope = await self._profile_scope(interaction)
        if scope is None:
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            catalog = await self._catalog()
            results = await asyncio.to_thread(catalog.search, query)
            profile = await asyncio.to_thread(self._profile_store().get, scope[0], scope[2])
            panel = CatalogResultsPanel(
                self,
                scope[0],
                scope[2],
                profile,
                interaction.user.display_name,
                str(interaction.user.display_avatar.url),
                results=results,
                query=query,
                defaults=defaults,
            )
            panel.origin = interaction
            panel.message_handle = await interaction.followup.send(
                view=panel,
                ephemeral=True,
                wait=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            logger.warning("Game catalog search failed (%s)", type(exc).__name__)
            await private_notice(
                interaction,
                "The game catalog is temporarily unavailable. Nothing was saved. Please try again.",
            )

    def _register_catalog_commands(self):
        if self.tree.get_command("addgame"):
            return

        async def game_autocomplete(interaction: discord.Interaction, current: str):
            from src.prepaid_runtime import free_interaction_allowed

            if not free_interaction_allowed(interaction.user.id, getattr(interaction, "id", None)):
                return []
            scope = (interaction.guild_id or 0, interaction.channel_id or 0, interaction.user.id)
            if not interaction.guild_id or not self.allowed(scope):
                return []
            try:
                catalog = await asyncio.wait_for(self._catalog(), timeout=1.8)
                rows = await asyncio.wait_for(
                    asyncio.to_thread(catalog.search, current), timeout=0.5
                )
                return [
                    app_commands.Choice(
                        name=(g["name"] + " · " + g["description"])[:100], value=g["id"]
                    )
                    for g in rows
                ]
            except Exception:
                return []

        @app_commands.command(
            name="addgame",
            description="Search the game catalog as you type, then privately save a selection.",
        )
        @app_commands.describe(
            game="Start typing and select a matching game, rather than submitting free text."
        )
        @app_commands.autocomplete(game=game_autocomplete)
        @app_commands.guild_only()
        async def addgame(interaction: discord.Interaction, game: str):
            scope = await self._profile_scope(interaction)
            if scope is None:
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                catalog = await self._catalog()
                chosen = await asyncio.to_thread(catalog.get, game)
                if chosen is None:
                    await private_notice(
                        interaction,
                        "Select a game from the suggestions. Free-typed game names are not saved.",
                    )
                    return
                profile = await asyncio.to_thread(self._profile_store().get, scope[0], scope[2])
                panel = ProfilePanel(
                    self,
                    scope[0],
                    scope[2],
                    profile,
                    interaction.user.display_name,
                    str(interaction.user.display_avatar.url),
                    proposal=(
                        "game",
                        {
                            "catalog_id": chosen["id"],
                            "name": chosen["name"],
                            "role": "",
                            "style": "both",
                            "visibility": "private",
                        },
                    ),
                )
                panel.origin = interaction
                panel.message_handle = await interaction.followup.send(
                    view=panel,
                    ephemeral=True,
                    wait=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as exc:
                logger.warning("Game selection failed (%s)", type(exc).__name__)
                await private_notice(
                    interaction, "The game could not be prepared. Nothing was saved. Try /profile."
                )

        @app_commands.command(
            name="findplayers",
            description="Find members who explicitly shared the same catalog game in this server.",
        )
        @app_commands.autocomplete(game=game_autocomplete)
        @app_commands.guild_only()
        async def findplayers(interaction: discord.Interaction, game: str):
            scope = await self._profile_scope(interaction)
            if scope is None:
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                catalog = await self._catalog()
                chosen = await asyncio.to_thread(catalog.get, game)
                if chosen is None:
                    await private_notice(interaction, "Select a game from the suggestions first.")
                    return
                rows = await asyncio.to_thread(self._profile_store().players_for, scope[0], game)
                lines = []
                for uid, settings in rows:
                    member = interaction.guild.get_member(uid) if interaction.guild else None
                    if member is None and interaction.guild:
                        try:
                            member = await interaction.guild.fetch_member(uid)
                        except (discord.NotFound, discord.Forbidden):
                            continue
                    if member:
                        lines.append(
                            f"**{clean(member.display_name)}** · {clean(settings['role'] or 'Any role')} · {settings['style'].title()}"
                        )
                await private_notice(
                    interaction,
                    "**"
                    + clean(chosen["name"])
                    + "**\n"
                    + (
                        "\n".join(lines)
                        if lines
                        else "No current members have shared this game yet."
                    )
                    + "\n\nOnly games set to **This server** appear here. Nobody was notified.",
                )
            except Exception as exc:
                logger.warning("Player matching failed (%s)", type(exc).__name__)
                await private_notice(
                    interaction, "Player search is temporarily unavailable. Please try again."
                )

        self.tree.add_command(addgame)
        self.tree.add_command(findplayers)
