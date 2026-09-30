"""Optional commercial perimeter plus private, non-purchasable plan previews."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from src.aclient import BotRequestError
from src.discord_purchases import DiscordPurchases, sku_map
from src.prepaid import PRODUCTS, Denied
from src.prepaid_runtime import SCOPE, PaidManager, PaidWeb, enforcing, runtime
from src.profile_ui import private_notice

if TYPE_CHECKING:
    from src.catalog_client import CatalogClientMixin as _PaidBase
else:
    _PaidBase = object

FREE_COMMANDS = {
    "plans",
    "usage",
    "profile",
    "help",
    "status",
    "reset",
    "birthday_forget",
    "birthday_scan_status",
    "private",
    "budget",
}
NETWORK_COMMANDS = {"chat", "search", "browse", "draw", "remember"}
CORE_COMMANDS = {
    "addgame",
    "findplayers",
    "birthdays",
    "party",
    "games",
    "teams",
    "provider",
    "switchpersona",
    "replyall",
    "steam",
}


def product_text(product) -> str:
    names = {
        "chat": "chat attempts",
        "reasoning": "reasoning attempts",
        "search": "web searches",
        "images": "image attempts",
    }
    rows = [f"{n:,} {names[k]}" for k, n in product.allowances.items() if k != "core"]
    if product.storage_bytes:
        rows.append(f"{product.storage_bytes // (1024**2):,} MiB saved data")
    return " · ".join(rows)


def plan_embed() -> discord.Embed:
    e = discord.Embed(
        title="Free tools & AI plans",
        description="**Preparation preview. Purchases are not enabled.**\nAI allowances are shared by each server. No automatic overages.",
    )
    e.add_field(
        name="Free for every server",
        value="Profiles and code-based game/team tools, with 1 MiB shared saved data.",
        inline=False,
    )
    for p in PRODUCTS.values():
        if p.kind != "subscription":
            continue
        e.add_field(
            name=f"{p.name} · ${p.price_cents // 100}.{p.price_cents % 100:02d}/month",
            value=product_text(p),
            inline=False,
        )
    e.set_footer(
        text="AI chat: 8,000 input / 500 output tokens. Reasoning: 2,000 total output. One search or image per attempt. Tool availability must pass launch review."
    )
    return e


class PaidOperationError(BotRequestError, Denied):
    """A reviewed allowance message safe to show through legacy handlers."""


class PrepaidClientMixin(_PaidBase):
    def __init__(self, config, *args, **kwargs):
        paid = enforcing()
        if paid and config.discord_purchase_mode != "enforce":
            raise Denied("Paid mode requires authenticated Discord purchase reconciliation.")
        if paid:
            config = replace(
                config,
                default_provider="openai",
                default_model="auto",
                enable_web_search=False,
                enable_openai_web_search=True,
                max_concurrent_requests=min(config.max_concurrent_requests, 2),
                max_input_chars=min(config.max_input_chars, 2000),
                max_output_tokens=500,
                steam_api_key=None,
            )
        super().__init__(config, *args, **kwargs)
        self._discord_purchases = None
        self._discord_purchase_task = None
        self._discord_purchase_trigger = asyncio.Event()
        if config.discord_purchase_mode != "off":
            self._discord_purchases = DiscordPurchases(
                runtime().ledger,
                sku_map(config.discord_sku_map),
                application_id=config.discord_application_id,
            )
        if paid:
            runtime().acquire_process()
            self.provider_manager = PaidManager(self.provider_manager, runtime())
            self.web_service = PaidWeb(self.web_service)
            # Intentionally no third-party account/library API in commercial mode.
            # A new paid API needs its own bounded price contract and adapter.
            self.steam_service = None
            original = self.tree.interaction_check

            async def check(interaction):
                from src.prepaid_runtime import free_interaction_allowed

                if not free_interaction_allowed(
                    interaction.user.id, getattr(interaction, "id", None)
                ):
                    return False
                if not await original(interaction):
                    return False
                name = getattr(getattr(interaction, "command", None), "root_parent", None)
                name = getattr(name or getattr(interaction, "command", None), "name", "")
                try:
                    if name not in FREE_COMMANDS | CORE_COMMANDS | NETWORK_COMMANDS:
                        raise Denied("This feature is not approved for metered plans.")
                    if name in CORE_COMMANDS:
                        runtime().core(interaction.guild_id or 0, interaction.user.id)
                    elif name in NETWORK_COMMANDS:
                        runtime().ready()
                        runtime().ledger.assert_active(interaction.guild_id or 0)
                    return True
                except Denied as exc:
                    logging.getLogger(__name__).info("Commercial command denied: %s", exc)
                    await private_notice(interaction, "I can't do that right now.")
                    return False

            setattr(self.tree, "interaction_check", check)

    async def on_ready(self):
        await super().on_ready()
        if self._discord_purchases is not None and self._discord_purchase_task is None:
            self._discord_purchase_task = asyncio.create_task(self._maintain_discord_purchases())
        self._discord_purchase_trigger.set()
        if enforcing() and getattr(self, "_prepaid_maintenance", None) is None:
            self._prepaid_maintenance = asyncio.create_task(self._maintain_prepaid_storage())

    async def _maintain_discord_purchases(self):
        while True:
            try:
                purchases = self._discord_purchases
                if purchases is None:
                    return
                self._discord_purchase_trigger.clear()
                result = await purchases.reconcile(
                    self, credit=self.config.discord_purchase_mode == "enforce"
                )
                logging.getLogger(__name__).info("Discord purchase reconciliation: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Discord purchase reconciliation failed")
            try:
                await asyncio.wait_for(self._discord_purchase_trigger.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def on_entitlement_create(self, entitlement):
        self._discord_purchase_changed()

    async def on_entitlement_update(self, entitlement):
        self._discord_purchase_changed()

    async def on_entitlement_delete(self, entitlement):
        self._discord_purchase_changed()

    async def on_subscription_create(self, subscription):
        self._discord_purchase_changed()

    async def on_subscription_update(self, subscription):
        self._discord_purchase_changed()

    async def on_subscription_delete(self, subscription):
        self._discord_purchase_changed()

    def _discord_purchase_changed(self):
        if self._discord_purchases is not None:
            self._discord_purchases.invalidate()
        self._discord_purchase_trigger.set()

    async def _maintain_prepaid_storage(self):
        from src.prepaid_lifecycle import maintain_storage

        while True:
            try:
                evidence = runtime().evidence()
                if (
                    evidence.get("approved") is True
                    and evidence.get("storage_lifecycle_verified") is True
                ):
                    paths = {
                        "profiles": os.getenv("PROFILE_DATABASE_PATH", "data/profiles.sqlite3"),
                        "chat": self.config.chat_database_path or "",
                        "gaming": self.config.gaming_database_path,
                    }
                    result = await asyncio.to_thread(
                        maintain_storage, runtime().ledger, paths, now=int(time.time())
                    )
                    if result["records_removed"]:
                        self.conversations.clear()
                    logging.getLogger(__name__).info("Prepaid storage maintenance: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception:
                runtime().ledger.lock("storage_maintenance_failed")
                logging.getLogger(__name__).warning(
                    "Prepaid storage maintenance needs operator review"
                )
            await asyncio.sleep(3600)

    async def close(self):
        purchase_task = self._discord_purchase_task
        if purchase_task is not None:
            purchase_task.cancel()
            try:
                await purchase_task
            except asyncio.CancelledError:
                pass
        task = getattr(self, "_prepaid_maintenance", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().close()

    async def setup_hook(self):
        self._register_prepaid_commands()
        await super().setup_hook()

    def _register_prepaid_commands(self):
        if self.tree.get_command("plans"):
            return

        @app_commands.command(
            name="plans",
            description="Privately view free tools and preview paid AI plans. No purchase occurs.",
        )
        async def plans(interaction: discord.Interaction):
            await interaction.response.send_message(
                embed=plan_embed(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )

        @app_commands.command(
            name="usage",
            description="Privately view this server’s remaining prepaid allowances and saved-data limit.",
        )
        @app_commands.guild_only()
        async def usage(interaction: discord.Interaction):
            if not enforcing():
                await private_notice(
                    interaction,
                    "Paid plans are not active. /plans shows the preparation preview; no subscription has been created.",
                )
                return
            await interaction.response.defer(ephemeral=True)
            try:
                summary = await asyncio.to_thread(
                    runtime().ledger.summary, interaction.guild_id or 0
                )
                e = discord.Embed(
                    title="Plan & usage",
                    description=" · ".join(summary["plans"]) or "No active paid plan",
                )
                for f, label in [
                    ("chat", "Chat"),
                    ("reasoning", "Advanced reasoning"),
                    ("search", "Web searches"),
                    ("images", "Image generation"),
                ]:
                    e.add_field(
                        name=label,
                        value=f"{summary['remaining'][f]:,} / {summary['included'][f]:,} remaining",
                        inline=True,
                    )
                e.add_field(
                    name="Saved data",
                    value=f"{summary['storage_used']:,} / {summary['storage_limit']:,} bytes",
                    inline=False,
                )
                e.set_footer(
                    text="No automatic charges. Failed provider attempts can consume allowance. Privacy controls remain available after expiry."
                )
                await interaction.followup.send(
                    embed=e, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
                )
            except Exception:
                await private_notice(
                    interaction,
                    "Usage is unavailable. Paid operations fail closed when accounting cannot be read.",
                )

        self.tree.add_command(plans)
        self.tree.add_command(usage)

    @asynccontextmanager
    async def _request_slot(self, scope, conv=None):
        async with super()._request_slot(scope, conv):
            token = None
            if enforcing():
                token = SCOPE.set(scope)
            try:
                yield
            finally:
                if token is not None:
                    SCOPE.reset(token)

    async def generate_image(self, prompt, scope, settings_snapshot=None):
        if not enforcing():
            return await super().generate_image(prompt, scope, settings_snapshot)
        async with self._request_slot(scope):
            if runtime().evidence().get("image_contract_verified") is not True:
                raise PaidOperationError(
                    "Image generation is not enabled until its price and availability are verified."
                )
            try:
                return await runtime().model_gateway().image(scope[0], scope[2], prompt)
            except Denied as exc:
                raise PaidOperationError(str(exc)) from None
