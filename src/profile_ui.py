"""Discord-native private profile cards and owner-bound editing forms."""

from __future__ import annotations

import asyncio
import calendar
import io
import json
import time
from typing import Any

import discord
from discord import ui

from src.member_settings import StaleProfile, visible_profile

ACCENT = 0x818CF8
SESSION_SECONDS = 600


def clean(value: Any, maximum=300):
    text = str(value).replace("@", "＠").replace("<", "‹").replace(">", "›")
    return discord.utils.escape_markdown(text.replace("\x00", "")[:maximum])


def store_for(client):
    return (
        client.profile_store
        if getattr(client, "profile_store", None) is not None
        else client._profile_store()
    )


async def private_notice(interaction, text):
    kwargs = dict(ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    if interaction.response.is_done():
        await interaction.followup.send(text, **kwargs)
    else:
        await interaction.response.send_message(text, **kwargs)


class _Button(ui.Button):
    def __init__(self, label, action, *, style=discord.ButtonStyle.secondary, emoji=None):
        super().__init__(label=label, style=style, emoji=emoji)
        self.action = action

    async def callback(self, interaction):
        await self.action(interaction)


class _GameChoice(ui.Select):
    def __init__(self, panel):
        self.panel = panel
        super().__init__(
            placeholder="Choose a game to edit",
            options=[
                discord.SelectOption(
                    label=g["name"][:80], value=str(n), description=(g["role"] or g["style"])[:100]
                )
                for n, g in enumerate(panel.profile["games"])
            ],
        )

    async def callback(self, interaction):
        index = int(self.values[0])
        await self.panel.switch(interaction, "game", selected=index)


class ProfilePanel(ui.LayoutView):
    def __init__(
        self,
        client,
        guild_id,
        user_id,
        profile,
        name,
        avatar="",
        *,
        screen="home",
        proposal=None,
        selected=None,
        status="",
    ):
        super().__init__(timeout=SESSION_SECONDS)
        self.client, self.guild_id, self.owner_id = client, guild_id, user_id
        self.profile, self.name, self.avatar = profile, name, avatar
        self.screen, self.proposal, self.selected = screen, proposal, selected
        self.expires_at = time.monotonic() + SESSION_SECONDS
        self.message_handle = None
        self.origin = None
        header: ui.TextDisplay[Any] = ui.TextDisplay(
            f"## Your profile\n**{clean(name, 80)}** · Settings for this server"
        )
        children: list[Any] = [
            ui.Section(header, accessory=ui.Thumbnail(avatar)) if avatar else header
        ]
        children.append(ui.Separator())
        if status:
            children.append(ui.TextDisplay(f"✓ {clean(status, 160)}"))
        if proposal is not None:
            operation, data = proposal
            titles = {
                "game": "Save this game?",
                "birthday": "Save your birthday?",
                "preferences": "Save these preferences?",
                "remove_game": "Remove this game?",
                "remove_birthday": "Remove your birthday?",
                "forget": "Delete your saved bot data?",
                "hide_all": "Make everything private?",
            }
            children.append(ui.TextDisplay(f"### {titles.get(operation, 'Review your change')}"))
            if operation == "forget":
                description = (
                    "Deletes your saved profile, linked Steam entry, party entries and older identity/birthday records in this server. "
                    "Also clears shared bot-chat context here to remove unattributed copies. Other members’ private chats are untouched. "
                    "Original Discord messages and provider/backup copies are not deleted by this action."
                )
            else:
                description = (
                    "\n".join(
                        f"**{clean(k.replace('_', ' ').title())}:** {clean(v, 180)}"
                        for k, v in data.items()
                    )
                    or "This changes only your saved settings."
                )
            children.append(ui.TextDisplay(description))
            actions = [
                _Button(
                    "Delete" if operation == "forget" else "Save",
                    self.confirm,
                    style=discord.ButtonStyle.danger
                    if operation == "forget"
                    else discord.ButtonStyle.success,
                )
            ]
            if operation in ("game", "birthday", "preferences"):
                actions.append(_Button("Edit", self.edit_proposal))
            actions.append(_Button("Cancel", self.cancel))
            children.append(ui.ActionRow(*actions))
        elif screen == "home":
            games = profile["games"]
            game_text = " · ".join(clean(g["name"], 45) for g in games[:4]) or "Add games you enjoy"
            if len(games) > 4:
                game_text += f" · +{len(games) - 4} more"
            birthday = profile["birthday"]
            birth_text = (
                f"{calendar.month_name[birthday['month']]} {birthday['day']} · {self.visibility(birthday)}"
                if birthday
                else "Not set · month and day only"
            )
            preferences = profile["preferences"]
            pref_text = (
                " · ".join(preferences.get("availability", [])) or "Choose when and how you play"
            )
            children.extend(
                [
                    ui.TextDisplay(f"### 🎮 Games\n{game_text}"),
                    ui.TextDisplay(f"### 🎂 Birthday\n{birth_text}"),
                    ui.TextDisplay(f"### 🎯 Play preferences\n{clean(pref_text)}"),
                    ui.Separator(),
                    ui.ActionRow(
                        _Button("Games", self.open_games, emoji="🎮"),
                        _Button("Birthday", self.edit_birthday, emoji="🎂"),
                        _Button("Preferences", self.edit_preferences, emoji="🎯"),
                    ),
                    ui.ActionRow(
                        _Button("Privacy & data", self.open_privacy, emoji="🔒"),
                        _Button(
                            "Tell me what to save",
                            self.open_remember,
                            style=discord.ButtonStyle.primary,
                        ),
                    ),
                ]
            )
        elif screen == "games":
            children.append(
                ui.TextDisplay(
                    "### Your games\nSelect a catalog game. For suggestions as you type, use /addgame."
                )
            )
            if profile["games"]:
                children.append(ui.ActionRow(_GameChoice(self)))
            else:
                children.append(
                    ui.TextDisplay("No games saved yet. Start with one you play with friends.")
                )
            children.append(
                ui.ActionRow(
                    _Button("Search games", self.add_game, style=discord.ButtonStyle.primary),
                    _Button("Back", self.home),
                )
            )
        elif screen == "game":
            g = profile["games"][selected]
            children.append(
                ui.TextDisplay(
                    f"### {clean(g['name'])}\n**Role:** {clean(g['role'] or 'Not set')}\n**Style:** {g['style'].title()}\n**Sharing:** {self.visibility(g)}"
                )
            )
            children.append(
                ui.ActionRow(
                    _Button("Edit game", self.edit_game, style=discord.ButtonStyle.primary),
                    _Button("Remove", self.remove_game, style=discord.ButtonStyle.danger),
                    _Button("Back", self.open_games),
                )
            )
        elif screen in ("privacy", "data"):
            if screen == "data":
                shared = visible_profile(profile)
                excerpt = json.dumps(
                    {k: v for k, v in profile.items() if k != "revision"},
                    ensure_ascii=False,
                    indent=2,
                )
                children.append(
                    ui.TextDisplay(
                        "### Your saved settings\n```json\n"
                        + excerpt.replace("```", "｀｀｀")[:2600]
                        + "\n```"
                    )
                )
                children.append(
                    ui.TextDisplay(
                        "**Server-visible fields:** "
                        + str(sum(bool(shared[k]) for k in ("birthday", "games", "preferences")))
                    )
                )
            else:
                children.extend(
                    [
                        ui.TextDisplay(
                            "### Privacy & data\n**You choose what is saved.** Each field starts private; sharing with this server is optional."
                        ),
                        ui.TextDisplay(
                            "**No extra login.** Discord identifies your account. These forms never request passwords or account tokens."
                        ),
                        ui.TextDisplay(
                            "Private means hidden from other server members, not from Discord or the bot operator. Forms save directly to the bot database. "
                            "“Tell me what to save” sends your request to the configured AI provider; nothing is saved until you confirm. "
                            "Saved settings can help AI replies. Only shared fields are supplied for public replies."
                        ),
                        ui.TextDisplay(
                            "Automatic personality profiling and birthday-history scanning are disabled in this client. "
                            "Older stored records are not used by this interface. Delete below also removes attributable older records from live storage."
                        ),
                    ]
                )
            children.extend(
                [
                    ui.Separator(),
                    ui.ActionRow(
                        _Button("View saved data", self.open_data),
                        _Button("Export settings", self.export),
                        _Button("Make all private", self.hide_all),
                    ),
                    ui.ActionRow(
                        _Button(
                            "Delete my data", self.ask_delete, style=discord.ButtonStyle.danger
                        ),
                        _Button("Remove birthday", self.remove_birthday),
                        _Button("Back", self.home),
                    ),
                ]
            )
        children.append(
            ui.TextDisplay("-# Only you can see this setup · Reopen /profile after 10 minutes")
        )
        self.add_item(ui.Container(*children, accent_colour=ACCENT))

    @staticmethod
    def visibility(item):
        return "This server" if item.get("visibility") == "server" else "Only me"

    async def authorized(self, interaction, *, modal=False):
        from src.prepaid_runtime import free_interaction_allowed

        if not free_interaction_allowed(interaction.user.id, getattr(interaction, "id", None)):
            await private_notice(interaction, "Please wait a moment before using another control.")
            return False
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await private_notice(
                interaction,
                "This setup belongs to another account or server. Use /profile for your own.",
            )
            return False
        if self.is_finished() or time.monotonic() >= self.expires_at:
            await private_notice(interaction, "This setup expired. Open /profile again.")
            return False
        message = getattr(interaction, "message", None)
        if not modal and (
            message is None or not getattr(getattr(message, "flags", None), "ephemeral", False)
        ):
            await private_notice(interaction, "Open /profile to start a private setup session.")
            return False
        return True

    async def interaction_check(self, interaction):
        return await self.authorized(interaction)

    async def on_error(self, interaction, error, item):
        await private_notice(
            interaction,
            "That change could not be completed. Reopen /profile and try again. No details were posted publicly.",
        )

    async def on_timeout(self):
        # Ephemeral tokens expire too; never fall back to a public channel send.
        if self.message_handle is not None:
            try:
                await self.message_handle.delete()
            except discord.HTTPException:
                pass

    async def switch(self, interaction, screen="home", *, selected=None, proposal=None, status=""):
        if not await self.authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        current = await asyncio.to_thread(store_for(self.client).get, self.guild_id, self.owner_id)
        if selected is not None and current["revision"] != self.profile["revision"]:
            await private_notice(interaction, "Your games changed. Open Games again.")
            return
        panel = ProfilePanel(
            self.client,
            self.guild_id,
            self.owner_id,
            current,
            self.name,
            self.avatar,
            screen=screen,
            selected=selected,
            proposal=proposal,
            status=status,
        )
        panel.origin = self.origin
        panel.message_handle = await interaction.edit_original_response(
            view=panel, allowed_mentions=discord.AllowedMentions.none()
        )
        self.stop()

    async def save(self, interaction, operation, data, *, modal=False):
        if not await self.authorized(interaction, modal=modal):
            return
        await interaction.response.defer(
            ephemeral=True, thinking=bool(modal and getattr(interaction, "message", None) is None)
        )
        try:
            if operation == "game":
                catalog = await self.client._catalog()
                chosen = await asyncio.to_thread(catalog.get, data.get("catalog_id", ""))
                if chosen is None:
                    raise ValueError(
                        "Select a game from the catalog. Free-typed names are not saved."
                    )
                data = dict(data, name=chosen["name"], catalog_id=chosen["id"])
            if operation == "forget":
                current = await self.client.erase_profile_data(
                    self.guild_id, self.owner_id, self.profile["revision"]
                )
            else:
                current = await asyncio.to_thread(
                    store_for(self.client).apply,
                    self.guild_id,
                    self.owner_id,
                    operation,
                    data,
                    self.profile["revision"],
                )
        except (ValueError, StaleProfile) as exc:
            await private_notice(interaction, str(exc))
            return
        panel = ProfilePanel(
            self.client,
            self.guild_id,
            self.owner_id,
            current,
            self.name,
            self.avatar,
            status="Saved. Your changes were not posted to the channel."
            if operation != "forget"
            else "Removed from live bot storage for this server.",
        )
        panel.origin = self.origin
        if getattr(interaction, "message", None) is not None:
            panel.message_handle = await interaction.edit_original_response(
                view=panel, allowed_mentions=discord.AllowedMentions.none()
            )
        else:
            panel.message_handle = await interaction.followup.send(
                view=panel,
                ephemeral=True,
                wait=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        self.stop()

    async def home(self, i):
        await self.switch(i)

    async def open_games(self, i):
        await self.switch(i, "games")

    async def open_privacy(self, i):
        await self.switch(i, "privacy")

    async def open_data(self, i):
        await self.switch(i, "data")

    async def add_game(self, i):
        await i.response.send_modal(GameSearchModal(self))

    async def edit_game(self, i):
        game = self.profile["games"][self.selected]
        if not game.get("catalog_id"):
            await i.response.send_modal(GameSearchModal(self, query=game["name"]))
        else:
            await i.response.send_modal(GameModal(self, game))

    async def edit_birthday(self, i):
        await i.response.send_modal(BirthdayModal(self))

    async def edit_preferences(self, i):
        await i.response.send_modal(PreferencesModal(self))

    async def open_remember(self, i):
        await i.response.send_modal(RememberModal(self))

    async def remove_game(self, i):
        await self.switch(
            i,
            proposal=(
                "remove_game",
                {
                    k: v
                    for k, v in self.profile["games"][self.selected].items()
                    if k in ("name", "catalog_id")
                },
            ),
        )

    async def remove_birthday(self, i):
        await self.switch(i, proposal=("remove_birthday", {}))

    async def hide_all(self, i):
        await self.save(i, "hide_all", {})

    async def ask_delete(self, i):
        await self.switch(i, proposal=("forget", {}))

    async def confirm(self, i):
        await self.save(i, *self.proposal)

    async def cancel(self, i):
        await self.switch(i, status="Cancelled. Nothing was changed.")

    async def edit_proposal(self, i):
        operation, data = self.proposal
        cls = {"game": GameModal, "birthday": BirthdayModal, "preferences": PreferencesModal}[
            operation
        ]
        await i.response.send_modal(cls(self, data))

    async def export(self, i):
        if not await self.authorized(i):
            return
        await i.response.defer(ephemeral=True, thinking=True)
        p = await asyncio.to_thread(store_for(self.client).get, self.guild_id, self.owner_id)
        payload = dict(
            server_id=str(self.guild_id),
            user_id=str(self.owner_id),
            settings=p,
            scope="Member-entered profile settings only; excludes chat, Steam, legacy stores and provider logs.",
        )
        file = discord.File(
            io.BytesIO(json.dumps(payload, indent=2, ensure_ascii=False).encode()),
            filename="my-profile.json",
        )
        await i.followup.send(
            "Your saved profile settings. Keep this file private.",
            file=file,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def select(options, defaults=(), *, optional=False, multiple=False):
    return ui.Select(
        options=[
            discord.SelectOption(label=label, value=value, default=value in defaults)
            for label, value in options
        ],
        min_values=0 if optional else 1,
        max_values=len(options) if multiple else 1,
        required=not optional,
    )


def sharing(default="private"):
    return select([("Only me", "private"), ("This server", "server")], [default])


class _OwnedModal(ui.Modal):
    def __init__(self, panel, title):
        super().__init__(title=title, timeout=300)
        self.panel = panel

    async def interaction_check(self, interaction):
        return await self.panel.authorized(interaction, modal=True)

    async def on_error(self, interaction, error, item=None):
        if isinstance(error, ValueError):
            await private_notice(interaction, str(error))
        else:
            await private_notice(
                interaction, "This form could not be saved. Reopen /profile and try again."
            )

    def field(self, label, item, description=None):
        self.add_item(ui.Label(text=label, component=item, description=description))
        return item


class GameSearchModal(_OwnedModal):
    def __init__(self, panel, query=""):
        super().__init__(panel, "Find a game")
        self.query = self.field(
            "Search the game catalog",
            ui.TextInput(
                default=query[:100],
                placeholder="League, Zelda, Halo...",
                max_length=100,
                required=False,
            ),
            "Search text is not saved. Live suggestions are also available with /addgame.",
        )

    async def on_submit(self, interaction):
        if await self.panel.authorized(interaction, modal=True):
            await self.panel.client.show_game_search(interaction, str(self.query))


class _CatalogChoice(ui.Select):
    def __init__(self, panel):
        self.panel = panel
        super().__init__(
            placeholder="Select the exact game",
            options=[
                discord.SelectOption(
                    label=g["name"][:100],
                    value=g["id"],
                    description=g["description"][:100] or "Catalog game",
                )
                for g in panel.results
            ],
        )

    async def callback(self, interaction):
        if not await self.panel.authorized(interaction):
            return
        chosen = next((g for g in self.panel.results if g["id"] == self.values[0]), None)
        if chosen is None:
            await private_notice(
                interaction, "That selection is no longer available. Search again."
            )
            return
        data = dict(
            self.panel.defaults, name=chosen["name"], catalog_id=chosen["id"], visibility="private"
        )
        await interaction.response.send_modal(GameModal(self.panel, data))


class CatalogResultsPanel(ProfilePanel):
    def __init__(self, *args, results, query="", defaults=None, **kwargs):
        super().__init__(*args, screen="catalog", **kwargs)
        self.results, self.defaults = results, (defaults or {})
        self.clear_items()
        items: list[Any] = [
            ui.TextDisplay("## 🎮 Choose a game"),
            ui.TextDisplay("Search: **" + clean(query or "Popular games", 100) + "**"),
            ui.Separator(),
        ]
        if results:
            items.append(ui.ActionRow(_CatalogChoice(self)))
        else:
            items.append(
                ui.TextDisplay(
                    "No matches. Try a shorter title or another official name. Nothing was saved."
                )
            )
        items.extend(
            [
                ui.ActionRow(
                    _Button("Search again", self.add_game, emoji="🔎"),
                    _Button("Back to profile", self.home),
                ),
                ui.TextDisplay(
                    "-# Catalog: Wikidata · Games are matched by ID, not spelling · Only you can see this"
                ),
            ]
        )
        self.add_item(ui.Container(*items, accent_colour=ACCENT))


class BirthdayModal(_OwnedModal):
    def __init__(self, panel, data=None):
        super().__init__(panel, "Edit your birthday")
        data = data if data is not None else panel.profile["birthday"] or {}
        self.month_input = self.field(
            "Month",
            select(
                [(calendar.month_name[n], str(n)) for n in range(1, 13)],
                [str(data.get("month", ""))],
            ),
        )
        self.day_input = self.field(
            "Day",
            ui.TextInput(
                default=str(data.get("day", "")), placeholder="1 to 31", min_length=1, max_length=2
            ),
        )
        self.sharing_input = self.field(
            "Who can see this?",
            sharing(data.get("visibility", "private")),
            "Month and day only. This does not schedule automatic announcements.",
        )

    async def on_submit(self, interaction):
        await self.panel.save(
            interaction,
            "birthday",
            dict(
                month=int(self.month_input.values[0]),
                day=int(str(self.day_input)),
                visibility=self.sharing_input.values[0],
            ),
            modal=True,
        )


class GameModal(_OwnedModal):
    def __init__(self, panel, data=None):
        super().__init__(panel, "Save a game")
        data = data or {}
        self.game_data = {k: v for k, v in data.items() if k in ("name", "catalog_id")}
        self.add_item(
            ui.TextDisplay(
                "**" + clean(data.get("name", "Select a catalog game first"), 200) + "**"
            )
        )
        self.role_input = self.field(
            "Preferred role (optional)",
            ui.TextInput(
                default=data.get("role", ""),
                placeholder="Jungle, support, tank...",
                required=False,
                max_length=60,
            ),
        )
        self.style_input = self.field(
            "How do you usually play?",
            select(
                [("Casual", "casual"), ("Competitive", "competitive"), ("Both", "both")],
                [data.get("style", "both")],
            ),
        )
        self.sharing_input = self.field(
            "Who can see this game?", sharing(data.get("visibility", "private"))
        )

    async def on_submit(self, interaction):
        await self.panel.save(
            interaction,
            "game",
            dict(
                **self.game_data,
                role=str(self.role_input),
                style=self.style_input.values[0],
                visibility=self.sharing_input.values[0],
            ),
            modal=True,
        )


class PreferencesModal(_OwnedModal):
    def __init__(self, panel, data=None):
        super().__init__(panel, "Play preferences")
        data = data if data is not None else panel.profile["preferences"]
        self.zone_input = self.field(
            "Timezone (optional)",
            ui.TextInput(
                default=data.get("timezone", ""),
                placeholder="America/Los_Angeles",
                required=False,
                max_length=80,
            ),
        )
        availability = ["Mornings", "Afternoons", "Evenings", "Late nights", "Weekdays", "Weekends"]
        types = ["Co-op", "PvP", "Single-player", "MMO", "Survival", "Party games"]
        self.times_input = self.field(
            "Usually available",
            select(
                [(v, v) for v in availability],
                data.get("availability", []),
                optional=True,
                multiple=True,
            ),
        )
        self.types_input = self.field(
            "Game types",
            select(
                [(v, v) for v in types], data.get("game_types", []), optional=True, multiple=True
            ),
        )
        self.sharing_input = self.field(
            "Who can see these preferences?", sharing(data.get("visibility", "private"))
        )

    async def on_submit(self, interaction):
        await self.panel.save(
            interaction,
            "preferences",
            dict(
                timezone=str(self.zone_input),
                availability=list(self.times_input.values),
                game_types=list(self.types_input.values),
                visibility=self.sharing_input.values[0],
            ),
            modal=True,
        )


class RememberModal(_OwnedModal):
    def __init__(self, panel):
        super().__init__(panel, "Tell me what to save")
        self.request_input = self.field(
            "Your change",
            ui.TextInput(
                style=discord.TextStyle.paragraph,
                max_length=700,
                placeholder="Add League of Legends. My preferred role is jungle.",
            ),
            "Sent to the AI provider for a proposal. Review it before saving.",
        )

    async def on_submit(self, interaction):
        await self.panel.client.propose_profile_change(interaction, str(self.request_input))
