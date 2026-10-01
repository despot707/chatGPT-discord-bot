"""Small, owner-bound menus over existing Discord handlers.

Only the four free entry points are published. Retired handlers remain private application
objects so every panel action uses the same validation, privacy and paid gateway.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands, ui

from src import personas
from src.aclient import public_error_message
from src.profile_ui import private_notice

logger = logging.getLogger(__name__)
SESSION_SECONDS = 600
PUBLIC_COMMANDS = frozenset({"settings", "games", "profile", "plans"})
OPERATOR_ACTIONS = frozenset({"budget", "status"})
ADMIN_ACTIONS = frozenset({"reset_shared", "party clear"})
PAID_ACTIONS = frozenset({"chat", "draw", "search", "browse", "remember", "image"})


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    default: str = ""
    required: bool = True
    kind: str = "text"
    minimum: int | None = None
    maximum: int = 1000
    choices: tuple[str, ...] = ()

    def parse(self, value: str) -> Any:
        value = value.strip()
        if not value:
            value = self.default
        if self.required and not value:
            raise ValueError(f"Enter {self.label.lower()}.")
        if self.kind == "integer":
            try:
                number = int(value)
            except ValueError:
                raise ValueError(f"{self.label}: enter a whole number.") from None
            if not (self.minimum or 0) <= number <= self.maximum:
                raise ValueError(f"{self.label}: use {self.minimum}–{self.maximum}.")
            return number
        if self.kind == "boolean":
            if value.lower() not in ("yes", "no"):
                raise ValueError(f"{self.label}: enter yes or no.")
            return value.lower() == "yes"
        if self.kind == "context":
            if value.lower() == "auto":
                return None
            try:
                number = int(value)
            except ValueError:
                raise ValueError("Choose auto or a number from 0 to 20.") from None
            if not 0 <= number <= 20:
                raise ValueError("Choose auto or a number from 0 to 20.")
            return number
        if len(value) > self.maximum:
            raise ValueError(f"{self.label}: use at most {self.maximum} characters.")
        if self.choices and value.lower() not in self.choices:
            raise ValueError(f"{self.label}: choose {' or '.join(self.choices)}.")
        return value.lower() if self.choices else value


@dataclass(frozen=True)
class Action:
    label: str
    fields: tuple[Field, ...] = ()


ACTIONS = {
    "context": Action(
        "Recent channel context",
        (Field("context_messages", "Recent messages: 0–20, or auto", "auto", kind="context"),),
    ),
    "reset": Action("Clear private chat"),
    "switchpersona": Action("Chat style"),
    "budget": Action("API budget"),
    "status": Action("Technical status"),
    "reset_shared": Action("Clear shared chat"),
    "usage": Action("Plan & usage"),
    "birthdays": Action("Shared birthdays"),
    "steam link": Action("Link Steam", (Field("profile", "Steam profile URL or SteamID64"),)),
    "steam unlink": Action("Unlink Steam"),
    "steam status": Action("Steam status"),
    "party join": Action(
        "Join party",
        (
            Field("skill", "Your skill: 1–10", "5", kind="integer", minimum=1, maximum=10),
            Field("role", "Your role", "any", maximum=24),
        ),
    ),
    "party leave": Action("Leave party"),
    "party show": Action("Show party"),
    "party clear": Action("Clear party"),
    "games together": Action(
        "Find shared Steam games",
        (
            Field("mode", "Owned by: all or most", "all", choices=("all", "most")),
            Field("multiplayer_only", "Multiplayer only? yes or no", "yes", kind="boolean"),
            Field("limit", "Number of games: 1–10", "5", kind="integer", minimum=1, maximum=10),
        ),
    ),
    "teams make": Action(
        "Make teams",
        (
            Field("team_count", "Number of teams: 2–4", "2", kind="integer", minimum=2, maximum=4),
            Field("balanced", "Balance by skill? yes or no", "yes", kind="boolean"),
        ),
    ),
    "findplayers": Action("Find players", (Field("query", "Game name", maximum=100),)),
    "profile": Action("Your profile"),
}
SCREENS = {"settings", "tools", "styles", "games", "party", "steam", "operator"}


class _PanelResponse:
    """Preserve slash-response visibility when reusing a handler from a component."""

    def __init__(self, response):
        self._response = response

    def __getattr__(self, name):
        return getattr(self._response, name)

    async def defer(self, **kwargs):
        # Components otherwise default to a message update and ignore ephemeral.
        kwargs["thinking"] = True
        return await self._response.defer(**kwargs)


class _PanelInteraction:
    def __init__(self, interaction):
        self._interaction = interaction
        self.response = _PanelResponse(interaction.response)

    def __getattr__(self, name):
        return getattr(self._interaction, name)


class _Button(ui.Button):
    def __init__(self, panel, label, action, *, value=None, row=0):
        super().__init__(label=label, row=row, style=discord.ButtonStyle.secondary)
        self.panel, self.action, self.value = panel, action, value
        self.revision = panel.revision

    async def callback(self, interaction):
        await self.panel.dispatch(interaction, self.action, self.revision, value=self.value)


class _Choice(ui.Select):
    def __init__(self, panel, action, options, placeholder):
        super().__init__(placeholder=placeholder, options=options, row=0)
        self.panel, self.action, self.revision = panel, action, panel.revision
        self.allowed_values = {option.value for option in options}

    async def callback(self, interaction):
        if len(self.values) != 1 or self.values[0] not in self.allowed_values:
            await private_notice(interaction, "Choose one of the available options.")
            return
        await self.panel.dispatch(interaction, self.action, self.revision, value=self.values[0])


class ActionModal(ui.Modal):
    def __init__(self, panel, action):
        spec = ACTIONS[action]
        super().__init__(title=spec.label, timeout=SESSION_SECONDS)
        self.panel, self.action, self.revision = panel, action, panel.revision
        panel.modal_sequence += 1
        self.sequence = panel.modal_sequence
        self.used = False
        self.inputs: dict[str, ui.TextInput] = {}
        for field in spec.fields:
            default = field.default
            if action == "context":
                current = panel.client.get_settings(panel.scope).context_messages
                default = "auto" if current is None else str(current)
            item: ui.TextInput = ui.TextInput(
                label=field.label,
                default=default or None,
                required=field.required,
                max_length=min(field.maximum if field.kind == "text" else 20, 4000),
                style=discord.TextStyle.paragraph
                if field.key in ("prompt", "query")
                else discord.TextStyle.short,
            )
            self.inputs[field.key] = item
            self.add_item(item)

    async def on_submit(self, interaction):
        if self.used or self.sequence != self.panel.modal_sequence:
            await private_notice(
                interaction, "This form was already used or replaced. Open it again."
            )
            return
        if not await self.panel.authorized(interaction, self.revision, modal=True):
            return
        self.used = True
        try:
            values = {f.key: f.parse(self.inputs[f.key].value) for f in ACTIONS[self.action].fields}
        except ValueError as exc:
            await private_notice(interaction, str(exc))
            return
        await self.panel.submit(interaction, self.action, values, self.revision)

    async def on_error(self, interaction, error, item=None):
        logger.warning("Customer form failed (%s)", type(error).__name__)
        await private_notice(
            interaction, "That action could not be completed. Open the menu and try again."
        )


class CustomerPanel(ui.View):
    def __init__(self, client, interaction, screen="settings"):
        super().__init__(timeout=SESSION_SECONDS)
        self.client, self.scope = client, client._scope(interaction)
        self.owner_id = interaction.user.id
        self.screen = screen
        self.operator = self.owner_id in client.config.bot_admin_ids
        self.server_manager = client.is_admin(self.owner_id, interaction)
        self.expires_at = time.monotonic() + SESSION_SECONDS
        self.revision = self.modal_sequence = 0
        self.busy = self.closed = False
        self.origin = interaction
        self.results: list[dict] = []
        self.render()

    def add_buttons(self, pairs, *, start_row=0):
        for index, (label, action, value) in enumerate(pairs):
            self.add_item(_Button(self, label, action, value=value, row=start_row + index // 3))

    def render(self):
        self.clear_items()
        s = self.client.get_settings(self.scope)
        pairs = []
        if self.screen == "settings":
            pairs = [
                (f"More effort: {'On' if s.more_effort else 'Off'}", "effort", not s.more_effort),
                (f"Images: {'On' if s.images_enabled else 'Off'}", "images", not s.images_enabled),
                ("Chat style", "styles", None),
                ("Chat & data", "tools", None),
                ("Plan & usage", "usage", None),
            ]
            if self.operator:
                pairs.append(("Operator controls", "operator", None))
        elif self.screen == "tools":
            pairs = [(ACTIONS[a].label, a, None) for a in ("context", "reset")]
            if self.server_manager:
                pairs.append((ACTIONS["reset_shared"].label, "reset_shared", None))
            pairs.append(("Back", "settings", None))
        elif self.screen == "styles":
            self.add_item(
                _Choice(
                    self,
                    "switchpersona",
                    [
                        discord.SelectOption(label=n.title(), value=n, default=s.persona == n)
                        for n in personas.get_available_personas(self.owner_id)
                    ],
                    "Choose your chat style",
                )
            )
            pairs = [("Back", "settings", None)]
        elif self.screen == "games":
            pairs = [
                ("Party & teams", "party", None),
                ("Find players", "findplayers", None),
                ("Steam", "steam", None),
                ("Shared birthdays", "birthdays", None),
                ("Your profile", "profile", None),
            ]
        elif self.screen == "party":
            pairs = [
                (ACTIONS[a].label, a, None)
                for a in ("party join", "party leave", "party show", "teams make")
            ]
            if self.client.config.steam_api_key:
                pairs.append((ACTIONS["games together"].label, "games together", None))
            if self.server_manager:
                pairs.append((ACTIONS["party clear"].label, "party clear", None))
            pairs.append(("Back", "games", None))
        elif self.screen == "steam":
            pairs = [(ACTIONS[a].label, a, None) for a in ("steam status", "steam unlink")]
            if self.client.config.steam_api_key:
                pairs.insert(0, (ACTIONS["steam link"].label, "steam link", None))
            pairs.append(("Back", "games", None))
        elif self.screen == "operator":
            pairs = [(ACTIONS[a].label, a, None) for a in ("budget", "status")]
            pairs.append(("Back", "settings", None))
        elif self.screen.startswith("confirm:"):
            action = self.screen.removeprefix("confirm:")
            pairs = [
                ("Clear", "confirmed:" + action, None),
                ("Cancel", "party" if action == "party clear" else "tools", None),
            ]
        elif self.screen == "players":
            if self.results:
                self.add_item(
                    _Choice(
                        self,
                        "players_result",
                        [
                            discord.SelectOption(
                                label=g["name"][:100],
                                value=g["id"],
                                description=g["description"][:100] or None,
                            )
                            for g in self.results[:25]
                        ],
                        "Choose the game to find players",
                    )
                )
            pairs = [("Search again", "findplayers", None), ("Back", "games", None)]
        pairs.append(("Close", "close", None))
        self.add_buttons(pairs, start_row=1 if self.screen in ("styles", "players") else 0)

    def embed(self):
        titles = {
            "settings": "Your chat",
            "tools": "Chat tools",
            "styles": "Chat style",
            "games": "Play together",
            "party": "Party & teams",
            "steam": "Steam",
            "operator": "Server controls",
            "players": "Find players",
        }
        descriptions = {
            "settings": "Free controls. @mention me or reply to my message for AI help; those requests use your plan’s allowances. Replies are public in the channel.\nTry ‘think harder’, ‘search the web’, or ‘make an image’.",
            "tools": "Free settings and saved-chat controls. Clear private chat removes your older private conversation here.",
            "styles": "Choose how your replies sound.",
            "games": "Free game, party and team tools. Saved games and sharing are in /profile.",
            "party": "Party and team results are posted in this channel.",
            "steam": "Steam ownership isn’t verified. Linking shares your library for party matching.",
            "operator": "Private diagnostics for configured bot operators only.",
            "players": "Choose a matching title. Only members who shared that game appear."
            if self.results
            else "No matching games. Try a shorter game name.",
        }
        if self.screen.startswith("confirm:"):
            action = self.screen.removeprefix("confirm:")
            return discord.Embed(
                title=ACTIONS[action].label + "?",
                description="This clears "
                + (
                    "your private conversation in this channel."
                    if action == "reset"
                    else "the shared conversation in this channel."
                    if action == "reset_shared"
                    else "this channel’s party."
                ),
                color=0x818CF8,
            )
        return discord.Embed(
            title=titles[self.screen], description=descriptions[self.screen], color=0x818CF8
        )

    async def authorized(self, interaction, revision=None, *, modal=False):
        if interaction.user.id != self.owner_id or self.client._scope(interaction) != self.scope:
            await private_notice(
                interaction, "This menu belongs to another session. Open your own menu."
            )
            return False
        if self.closed or time.monotonic() >= self.expires_at:
            await private_notice(interaction, "This menu expired. Open /settings or /games again.")
            return False
        if revision is not None and revision != self.revision:
            await private_notice(interaction, "This menu changed. Use its current buttons.")
            return False
        if not self.client.allowed(self.scope):
            await private_notice(interaction, "This bot is not enabled in this server or channel.")
            return False
        if not modal and not getattr(
            getattr(getattr(interaction, "message", None), "flags", None), "ephemeral", False
        ):
            await private_notice(interaction, "Open /settings or /games for a private menu.")
            return False
        return True

    async def interaction_check(self, interaction):
        return await self.authorized(interaction)

    async def gate(self, interaction, action):
        if action in OPERATOR_ACTIONS and self.owner_id not in self.client.config.bot_admin_ids:
            await private_notice(interaction, "This control is for configured bot operators only.")
            return False
        if action in ADMIN_ACTIONS and not self.client.is_admin(self.owner_id, interaction):
            await private_notice(
                interaction, "Manage Channels permission or bot-admin access is required."
            )
            return False
        root = "reset" if action == "reset_shared" else action.split()[0]
        return await self.client.customer_action_allowed(interaction, root)

    async def refresh(self, interaction, screen):
        self.screen = screen
        self.revision += 1
        self.operator = self.owner_id in self.client.config.bot_admin_ids
        self.server_manager = self.client.is_admin(self.owner_id, interaction)
        self.render()
        await interaction.response.edit_message(
            embed=self.embed(), view=self, allowed_mentions=discord.AllowedMentions.none()
        )

    async def dispatch(self, interaction, action, revision, *, value=None):
        if not await self.authorized(interaction, revision):
            return
        if self.busy:
            await private_notice(interaction, "An action is in progress. Please wait.")
            return
        self.busy = True
        try:
            if action in SCREENS or action in ("effort", "images", "close"):
                if not await self.gate(interaction, "settings"):
                    return
                if action == "operator" and self.owner_id not in self.client.config.bot_admin_ids:
                    await private_notice(
                        interaction, "This control is for configured bot operators only."
                    )
                    return
                if action == "close":
                    self.closed = True
                    for item in self.children:
                        if isinstance(item, (ui.Button, ui.Select)):
                            item.disabled = True
                    self.stop()
                    await interaction.response.edit_message(
                        content="Menu closed.", embed=None, view=self
                    )
                elif action in ("effort", "images"):
                    if not isinstance(value, bool):
                        return
                    s = self.client.get_settings(self.scope)
                    setattr(s, "more_effort" if action == "effort" else "images_enabled", value)
                    await self.refresh(interaction, "settings")
                else:
                    await self.refresh(interaction, action)
                return
            confirmed = action.startswith("confirmed:")
            action = action.removeprefix("confirmed:")
            if action == "players_result":
                if value not in {g["id"] for g in self.results} or not await self.gate(
                    interaction, "findplayers"
                ):
                    return
                await self.invoke(interaction, "findplayers", {"game": value})
                return
            if action not in ACTIONS:
                await private_notice(interaction, "That option is unavailable. Reopen the menu.")
                return
            if not await self.gate(interaction, "settings" if action == "context" else action):
                return
            if action in ("reset", "reset_shared", "party clear") and not confirmed:
                await self.refresh(interaction, "confirm:" + action)
            elif action == "switchpersona":
                if value not in personas.get_available_personas(self.owner_id):
                    return
                await self.invoke(interaction, action, {"persona": value})
            elif ACTIONS[action].fields:
                await interaction.response.send_modal(ActionModal(self, action))
            else:
                await self.invoke(interaction, action, {})
                # A successful confirmation cannot be replayed through its old button.
                if confirmed:
                    self.screen = "party" if action == "party clear" else "tools"
                    self.revision += 1
                    self.render()
                    await self.origin.edit_original_response(
                        embed=self.embed(),
                        view=self,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
        except Exception as exc:
            logger.warning("Customer menu action failed (%s)", type(exc).__name__)
            await private_notice(
                interaction,
                public_error_message(exc)
                if isinstance(exc, (ValueError, RuntimeError))
                else "That action could not be completed. Try again.",
            )
        finally:
            self.busy = False

    async def submit(self, interaction, action, values, revision):
        if not await self.authorized(interaction, revision, modal=True):
            return
        if self.busy:
            await private_notice(interaction, "An action is in progress. Please wait.")
            return
        self.busy = True
        try:
            if not await self.gate(interaction, "settings" if action == "context" else action):
                return
            if action == "context":
                self.client.get_settings(self.scope).context_messages = values["context_messages"]
                await private_notice(
                    interaction,
                    "Recent-message context updated for your AI requests in this channel.",
                )
            elif action == "findplayers":
                await interaction.response.defer(ephemeral=True)
                catalog = await self.client._catalog()
                self.results = await asyncio.to_thread(catalog.search, values["query"])
                self.screen = "players"
                self.revision += 1
                self.render()
                await interaction.edit_original_response(
                    embed=self.embed(), view=self, allowed_mentions=discord.AllowedMentions.none()
                )
            else:
                await self.invoke(interaction, action, values)
        except Exception as exc:
            logger.warning("Customer form action failed (%s)", type(exc).__name__)
            await private_notice(
                interaction,
                public_error_message(exc)
                if isinstance(exc, (ValueError, RuntimeError))
                else "That action could not be completed. Try again.",
            )
        finally:
            self.busy = False

    async def invoke(self, interaction, action, values):
        if action == "reset_shared":
            action, values = "reset", {"channel": True}
        command = self.client._customer_actions.get(action)
        if command is None:
            await private_notice(interaction, "That option is unavailable in this bot.")
            return
        if command.guild_only and not self.scope[0]:
            await private_notice(interaction, "Use this option in a server.")
            return
        # Preserve any explicit callback checks in addition to the shared perimeter.
        if not await command._check_can_run(interaction):
            await private_notice(interaction, "This option is unavailable here.")
            return
        await command.callback(_PanelInteraction(interaction), **values)

    async def on_timeout(self):
        self.closed = True
        for item in self.children:
            if isinstance(item, (ui.Button, ui.Select)):
                item.disabled = True
        try:
            await self.origin.edit_original_response(
                content="Menu expired. Open /settings or /games again.", embed=None, view=self
            )
        except discord.HTTPException:
            pass

    async def on_error(self, interaction, error, item):
        logger.warning("Customer control failed (%s)", type(error).__name__)
        await private_notice(
            interaction, "That action could not be completed. Reopen the menu and try again."
        )


async def open_panel(client, interaction, screen):
    panel = CustomerPanel(client, interaction, screen)
    await interaction.response.send_message(
        embed=panel.embed(),
        view=panel,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def configure_customer_commands(client):
    """Replace the global command manifest before its single bulk sync."""
    if getattr(client, "_customer_actions", None) is not None:
        return
    actions: dict[str, Any] = {}
    for command in client.tree.get_commands():
        if isinstance(command, app_commands.Group):
            for child in command.walk_commands():
                if isinstance(child, app_commands.Command) and child.qualified_name in ACTIONS:
                    actions[child.qualified_name] = child
        elif command.name in ACTIONS.keys() | PUBLIC_COMMANDS and command.name not in PAID_ACTIONS:
            actions[command.name] = command
    client._customer_actions = actions
    for command in list(client.tree.get_commands()):
        if command.name not in PUBLIC_COMMANDS or command.name == "games":
            client.tree.remove_command(command.name)

    @app_commands.command(
        name="settings", description="Free controls for AI preferences, saved chats and plan usage"
    )
    async def settings(interaction: discord.Interaction):
        await open_panel(client, interaction, "settings")

    @app_commands.command(
        name="games", description="Find players and open free party, team and Steam tools"
    )
    @app_commands.guild_only()
    @app_commands.describe(game="Optional: select a game to find players who shared it")
    async def games(interaction: discord.Interaction, game: str | None = None):
        if game is not None:
            command = actions.get("findplayers")
            if command is not None:
                await command.callback(interaction, game=game)
                return
        await open_panel(client, interaction, "games")

    @games.autocomplete("game")
    async def game_autocomplete(interaction: discord.Interaction, current: str):
        command = actions.get("findplayers")
        if command is None:
            return []
        # Reuse the catalog's local, privacy-scoped and rate-limited lookup.
        autocomplete = command._params["game"].autocomplete
        return await autocomplete(interaction, current) if autocomplete is not None else []

    client.tree.add_command(settings)
    client.tree.add_command(games)
