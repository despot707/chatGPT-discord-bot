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
SIDECORD_APPLICATION_ID = 1365724363722068120
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


PLAN_SKUS = {
    "basic": 1554920142532513832,
    "plus": 1554920641088593990,
    "premium": 1554920977488551936,
}


def active_plan_skus(config) -> dict[int, str] | None:
    """Return trusted native checkout SKUs only when paid enforcement is live."""
    if (
        not enforcing()
        or config.ai_access_mode == "disabled"
        or not config.hard_budget_enabled
        or config.discord_purchase_mode != "enforce"
        or os.getenv("DISCORD_PURCHASE_MODE", "off").strip().lower() != "enforce"
        or os.getenv("DISCORD_FUNDING_MODE", "settlement").strip().lower() != "entitlement"
        or config.discord_application_id != SIDECORD_APPLICATION_ID
    ):
        return None
    configured = sku_map(config.discord_sku_map)
    expected = {sku_id: product for product, sku_id in PLAN_SKUS.items()}
    if configured != expected:
        return None
    return expected


def plan_view(skus: dict[int, str] | None) -> discord.ui.View | None:
    if not skus:
        return None
    view = discord.ui.View(timeout=None)
    for product, sku_id in PLAN_SKUS.items():
        if skus.get(sku_id) != product:
            return None
        view.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.premium,
                sku_id=sku_id,
            )
        )
    return view


def plan_embed(*, checkout_available: bool = False) -> discord.Embed:
    e = discord.Embed(
        title="Free tools & AI plans",
        description=(
            "**Monthly Discord guild subscriptions.** AI allowances are shared by the whole server. "
            "No rollover or overage charges. Cancel through Discord; access continues until the "
            "current paid period ends."
            if checkout_available
            else "**Plan preview. Purchases are not enabled here.**\nAI allowances are designed to be shared by each server."
        ),
    )
    e.add_field(
        name="Free for every server",
        value="Profiles and code-based game, party, and team tools. No trial or free AI credits.",
        inline=False,
    )
    for p in PRODUCTS.values():
        if p.kind != "subscription":
            continue
        e.add_field(
            name=f"{p.name} · ${p.price_cents // 100}.{p.price_cents % 100:02d}/month",
            value="\n".join(
                f"{count:,} {label}"
                for key, label in [
                    ("chat", "chat attempts"),
                    ("reasoning", "advanced reasoning attempts"),
                    ("search", "web searches"),
                    ("images", "image generations"),
                ]
                if (count := p.allowances.get(key, 0))
            ),
            inline=False,
        )
    e.set_footer(
        text=(
            "Monthly USD price per server, before any applicable taxes. Allowances do not roll over. "
            "Daily availability limits also apply; temporarily unavailable features resume as capacity resets. "
            "No voice features, image editing, or image hosting are included."
        )
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
            retry_delay = 3600
            try:
                ready = runtime().storage_ready()
            except asyncio.CancelledError:
                raise
            except Denied as exc:
                # Startup purchase reconciliation may not have a complete,
                # fresh entitlement snapshot yet. This is a readiness wait,
                # not a storage failure, so retry soon without locking access.
                logging.getLogger(__name__).debug(
                    "Prepaid storage maintenance waiting for readiness: %s", exc
                )
                ready = False
                retry_delay = 5
            except Exception:
                # No maintenance has run yet. Keep this retryable and fail
                # closed through storage_ready() rather than locking the ledger.
                logging.getLogger(__name__).exception(
                    "Prepaid storage readiness check failed; retrying"
                )
                ready = False
                retry_delay = 5

            if ready:
                try:
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
            await asyncio.sleep(retry_delay)

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
            description="Privately view free tools and AI plans, with Discord checkout when available.",
        )
        async def plans(interaction: discord.Interaction):
            skus = active_plan_skus(self.config)
            embed = plan_embed(checkout_available=skus is not None)
            view = plan_view(skus)
            if view is None:
                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await interaction.response.send_message(
                    embed=embed,
                    view=view,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
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
            if not runtime().image_ready():
                raise PaidOperationError(
                    "Image generation is not enabled until its price and availability are verified."
                )
            try:
                return await runtime().model_gateway().image(scope[0], scope[2], prompt)
            except Denied as exc:
                raise PaidOperationError(str(exc)) from None
