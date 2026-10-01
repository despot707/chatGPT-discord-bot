"""Server-only Discord commands for Steam libraries and party coordination."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

import discord
from discord import app_commands

from src.aclient import BotRequestError

logger = logging.getLogger(__name__)


def _clean_name(value: str, limit: int = 80) -> str:
    """Keep user supplied names readable without allowing markdown or mention syntax."""
    value = re.sub(r"[\x00-\x1f\x7f]", "", str(value)).strip()
    value = discord.utils.escape_markdown(value)
    value = value.replace("@", "＠").replace("<", "‹").replace(">", "›")
    return value[:limit] or "Player"


def _allowed_mentions() -> discord.AllowedMentions:
    return discord.AllowedMentions.none()


async def _reply(interaction: discord.Interaction, content: str, *, ephemeral: bool) -> None:
    from utils.message_utils import split_message

    chunks = split_message(content)
    if not interaction.response.is_done():
        await interaction.response.send_message(
            content=chunks[0], ephemeral=ephemeral, allowed_mentions=_allowed_mentions()
        )
        chunks = chunks[1:]
    for chunk in chunks:
        await interaction.followup.send(
            content=chunk, ephemeral=ephemeral, allowed_mentions=_allowed_mentions()
        )


async def _context(client, interaction: discord.Interaction) -> tuple[int, int, int] | None:
    scope = client._scope(interaction)
    if not client.allowed(scope):
        await _reply(
            interaction, "This bot is not enabled in this server or channel.", ephemeral=True
        )
        return None
    if scope[0] <= 0 or scope[1] <= 0:
        await _reply(interaction, "Gaming commands are available in servers only.", ephemeral=True)
        return None
    return scope


def _store(client):
    if client.gaming_store is None:
        from src.gaming import GamingStore

        client.gaming_store = GamingStore(client.config.gaming_database_path)
    return client.gaming_store


def _steam(client):
    if client.steam_service is None:
        from src.steam import SteamService

        client.steam_service = SteamService(client.config.steam_api_key)
    return client.steam_service


def _stored_name(value: str, limit: int = 80) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", "", str(value)).strip()
    return value[:limit] or "Player"


async def _defer(interaction: discord.Interaction, *, ephemeral: bool) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)


async def _db_call(function, *args):
    return await asyncio.to_thread(function, *args)


async def _ordered_db_call(
    lock: asyncio.Lock,
    function,
    *args,
    state: _SteamMutation | None = None,
    generation: int | None = None,
):
    """Keep per-user write ordering even if the invoking task is cancelled."""
    async with lock:
        if state is not None and state.generation != generation:
            return False, None
        operation = asyncio.create_task(_db_call(function, *args))
        try:
            result = await asyncio.shield(operation)
            return True, result
        except asyncio.CancelledError:
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            raise


async def _handle_gaming_error(
    interaction: discord.Interaction, exc: BaseException, *, ephemeral: bool
) -> None:
    from src.gaming import GamingError
    from src.steam import SteamError

    if isinstance(exc, (GamingError, SteamError, BotRequestError)):
        # Service exceptions are deliberately sanitized at their boundaries.
        message = str(exc)
    else:
        logger.warning("Gaming command failed (%s)", type(exc).__name__)
        message = "That gaming command could not be completed. Please try again later."
    await _reply(interaction, message, ephemeral=ephemeral)


@dataclass
class _SteamMutation:
    generation: int = 0
    active: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _begin_steam_mutation(client, key: tuple[int, int]) -> tuple[_SteamMutation, int]:
    """Keep active link mutations ordered without retaining idle user keys."""
    states = client._steam_mutations
    state = states.get(key)
    if state is None:
        if len(states) >= client.config.max_sessions:
            inactive = next(
                (item_key for item_key, item in states.items() if item.active == 0), None
            )
            if inactive is not None:
                del states[inactive]
            else:
                raise BotRequestError(
                    "Too many Steam profile updates are in progress; try again shortly."
                )
        state = _SteamMutation()
        states[key] = state
    state.generation += 1
    state.active += 1
    return state, state.generation


def _finish_steam_mutation(client, key: tuple[int, int], state: _SteamMutation) -> None:
    state.active -= 1
    if state.active == 0 and client._steam_mutations.get(key) is state:
        del client._steam_mutations[key]


def register_gaming_commands(client) -> None:
    """Attach gaming command groups to the client's existing tree."""
    steam_group = app_commands.Group(
        name="steam", description="Link and inspect your Steam profile"
    )
    party_group = app_commands.Group(name="party", description="Manage this channel's gaming party")

    @steam_group.command(name="link", description="Link your Steam profile for party game matching")
    @app_commands.guild_only()
    @app_commands.describe(profile="Your Steam profile URL, vanity URL, or SteamID64")
    async def steam_link(interaction: discord.Interaction, profile: str):
        scope = await _context(client, interaction)
        if scope is None:
            return
        if not client.config.steam_api_key:
            await _reply(
                interaction,
                "Steam linking is unavailable because the bot administrator has not configured a Steam API key.",
                ephemeral=True,
            )
            return
        profile = profile.strip()
        if not profile or len(profile) > 200:
            await _reply(
                interaction,
                "Enter a Steam profile URL, vanity URL, or SteamID64 (max 200 characters).",
                ephemeral=True,
            )
            return
        mutation_key = (scope[0], scope[2])
        try:
            mutation_state, mutation_generation = _begin_steam_mutation(client, mutation_key)
        except BotRequestError as exc:
            await _reply(interaction, str(exc), ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            async with client._request_slot(scope):
                profile_data = await _steam(client).resolve_profile(profile.strip())
            # The profile is public and resolvable; Steam does not prove that the
            # Discord user owns it. The profile write is always scoped to invoker.
            applied, _ = await _ordered_db_call(
                mutation_state.lock,
                _store(client).link_steam,
                scope[0],
                scope[2],
                profile_data.steam_id,
                _stored_name(profile_data.display_name, 80),
                state=mutation_state,
                generation=mutation_generation,
            )
            if not applied:
                await _reply(
                    interaction,
                    "This Steam link was superseded by a later link or unlink request.",
                    ephemeral=True,
                )
                return
            await _reply(
                interaction,
                f"Linked Steam profile {_clean_name(profile_data.display_name)}. Steam profile ownership is not verified. Linking lets this server compare your game library with its channel party. Use /games → Steam → Unlink Steam to remove it.",
                ephemeral=True,
            )
        except Exception as exc:
            await _handle_gaming_error(interaction, exc, ephemeral=True)
        finally:
            _finish_steam_mutation(client, mutation_key, mutation_state)

    @steam_group.command(name="unlink", description="Remove your linked Steam profile")
    @app_commands.guild_only()
    async def steam_unlink(interaction: discord.Interaction):
        scope = await _context(client, interaction)
        if scope is None:
            return
        mutation_key = (scope[0], scope[2])
        try:
            mutation_state, mutation_generation = _begin_steam_mutation(client, mutation_key)
        except BotRequestError as exc:
            await _reply(interaction, str(exc), ephemeral=True)
            return
        try:
            await _defer(interaction, ephemeral=True)
            applied, _ = await _ordered_db_call(
                mutation_state.lock,
                _store(client).unlink_steam,
                scope[0],
                scope[2],
                state=mutation_state,
                generation=mutation_generation,
            )
            message = (
                "Your Steam profile link was removed."
                if applied
                else "This unlink was superseded by a later Steam link or unlink request."
            )
            await _reply(interaction, message, ephemeral=True)
        except Exception as exc:
            await _handle_gaming_error(interaction, exc, ephemeral=True)
        finally:
            _finish_steam_mutation(client, mutation_key, mutation_state)

    @steam_group.command(name="status", description="Check whether you linked a Steam profile")
    @app_commands.guild_only()
    async def steam_status(interaction: discord.Interaction):
        scope = await _context(client, interaction)
        if scope is None:
            return
        try:
            await _defer(interaction, ephemeral=True)
            link = await _db_call(_store(client).get_steam, scope[0], scope[2])
            message = (
                f"Your Steam profile is linked as {_clean_name(link.display_name)}."
                if link
                else "You have not linked a Steam profile. Use /games → Steam → Link Steam."
            )
            await _reply(interaction, message, ephemeral=True)
        except Exception as exc:
            await _handle_gaming_error(interaction, exc, ephemeral=True)

    @party_group.command(name="join", description="Join this channel's gaming party")
    @app_commands.guild_only()
    async def party_join(
        interaction: discord.Interaction,
        skill: app_commands.Range[int, 1, 10] = 5,
        role: str = "any",
    ):
        scope = await _context(client, interaction)
        if scope is None:
            return
        try:
            from src.gaming import PartyPlayer

            role = role.strip()
            if not role or len(role) > 24:
                raise BotRequestError("Role must contain 1 to 24 characters.")
            await _defer(interaction, ephemeral=False)
            player = PartyPlayer(scope[2], _stored_name(interaction.user.display_name), skill, role)
            await _db_call(_store(client).join_party, scope[0], scope[1], player)
            await _reply(
                interaction,
                f"{_clean_name(interaction.user.display_name)} joined this channel's party (skill {skill}, role {_clean_name(role, 24)}).",
                ephemeral=False,
            )
        except Exception as exc:
            await _handle_gaming_error(interaction, exc, ephemeral=False)

    @party_group.command(name="leave", description="Leave this channel's gaming party")
    @app_commands.guild_only()
    async def party_leave(interaction: discord.Interaction):
        scope = await _context(client, interaction)
        if scope is None:
            return
        try:
            await _defer(interaction, ephemeral=False)
            removed = await _db_call(_store(client).leave_party, scope[0], scope[1], scope[2])
            message = (
                "You left this channel's party."
                if removed
                else "You are not in this channel's party."
            )
            await _reply(interaction, message, ephemeral=False)
        except Exception as exc:
            await _handle_gaming_error(interaction, exc, ephemeral=False)

    @party_group.command(name="show", description="Show this channel's current party")
    @app_commands.guild_only()
    async def party_show(interaction: discord.Interaction):
        scope = await _context(client, interaction)
        if scope is None:
            return
        try:
            await _defer(interaction, ephemeral=False)
            players = await _db_call(_store(client).party, scope[0], scope[1])
            if not players:
                message = "This channel's party is empty. Use /games → Party & teams → Join party."
            else:
                lines = ["**Current channel party**"]
                lines.extend(
                    f"• {_clean_name(player.display_name)} — skill {player.skill}, role {_clean_name(player.role, 24)}"
                    for player in players[:20]
                )
                message = "\n".join(lines)
            await _reply(interaction, message, ephemeral=False)
        except Exception as exc:
            await _handle_gaming_error(interaction, exc, ephemeral=False)

    @party_group.command(name="clear", description="Clear this channel's party")
    @app_commands.guild_only()
    async def party_clear(interaction: discord.Interaction):
        scope = await _context(client, interaction)
        if scope is None:
            return
        if not client.is_admin(interaction.user.id, interaction):
            await _reply(
                interaction,
                "Manage Channels permission or bot-admin access is required.",
                ephemeral=True,
            )
            return
        try:
            await _defer(interaction, ephemeral=False)
            await _db_call(_store(client).clear_party, scope[0], scope[1])
            await _reply(interaction, "This channel's party was cleared.", ephemeral=False)
        except Exception as exc:
            await _handle_gaming_error(interaction, exc, ephemeral=False)

    games_group = app_commands.Group(
        name="games", description="Find games for this channel's party"
    )
    teams_group = app_commands.Group(
        name="teams", description="Split this channel's party into teams"
    )

    @games_group.command(name="together", description="Find games shared by this channel's party")
    @app_commands.guild_only()
    @app_commands.choices(
        mode=[app_commands.Choice(name=value.title(), value=value) for value in ("all", "most")]
    )
    async def games_together(
        interaction: discord.Interaction,
        mode: str = "all",
        multiplayer_only: bool = True,
        limit: app_commands.Range[int, 1, 10] = 5,
    ):
        scope = await _context(client, interaction)
        if scope is None:
            return
        if not client.config.steam_api_key:
            await _reply(
                interaction,
                "Game matching is unavailable because the bot administrator has not configured a Steam API key.",
                ephemeral=True,
            )
            return
        await _defer(interaction, ephemeral=False)
        try:
            players = await _db_call(_store(client).party, scope[0], scope[1])
            if not 2 <= len(players) <= 20:
                raise BotRequestError(
                    "Game matching requires 2 to 20 people in this channel's party."
                )
            links = await asyncio.gather(
                *(
                    _db_call(_store(client).get_steam, scope[0], player.user_id)
                    for player in players
                )
            )
            missing = [player.display_name for player, link in zip(players, links) if link is None]
            if missing:
                names = ", ".join(_clean_name(name) for name in missing[:8])
                raise BotRequestError(
                    f"Every party member must link Steam first. Missing: {names}."
                )
            steam_ids = [link.steam_id for link in links if link is not None]
            if len(set(steam_ids)) != len(steam_ids):
                raise BotRequestError(
                    "This party has duplicate linked Steam profiles. Each person must link a distinct Steam profile before matching."
                )
            async with client._request_slot(scope):
                service = _steam(client)
                suggestions = await service.suggest(
                    steam_ids, mode=mode, multiplayer_only=multiplayer_only, limit=limit
                )
            # Do not publish a result for a party that changed while Steam was
            # being queried (for example, a member left or unlinked).
            current_players = await _db_call(_store(client).party, scope[0], scope[1])
            current_links = await asyncio.gather(
                *(
                    _db_call(_store(client).get_steam, scope[0], player.user_id)
                    for player in players
                )
            )
            if [player.user_id for player in current_players] != [
                player.user_id for player in players
            ] or [link.steam_id if link else None for link in current_links] != steam_ids:
                raise BotRequestError(
                    "The party or a Steam link changed during matching. Open /games → Party & teams and try again."
                )
            if not suggestions:
                reason = (
                    "The group has no matching shared games, or Steam's multiplayer filter removed the top candidates."
                    if multiplayer_only
                    else "The group has no matching games in its Steam libraries."
                )
                message = f"{reason} Choose Find shared Steam games in /games → Party & teams and set Multiplayer only to no."
            else:
                lines = ["**Games to play together**"]
                for suggestion in suggestions[:limit]:
                    owners = int(suggestion.owner_count)
                    players_text = "all" if owners >= len(players) else f"{owners}/{len(players)}"
                    details = []
                    if suggestion.multiplayer is True:
                        details.append("multiplayer")
                    if suggestion.co_op is True:
                        details.append("co-op")
                    if suggestion.multiplayer is None or suggestion.co_op is None:
                        details.append("multiplayer/co-op unknown")
                    metadata = f"; {', '.join(details)}" if details else "; single-player"
                    playtime = (
                        f"; shared playtime {suggestion.total_playtime_minutes // 60}h"
                        if suggestion.total_playtime_minutes >= 60
                        else f"; shared playtime {suggestion.total_playtime_minutes}m"
                    )
                    app_id = int(suggestion.app_id)
                    title = _clean_name(suggestion.name, 100)
                    store_link = f"https://store.steampowered.com/app/{app_id}/"
                    lines.append(
                        f"• [{title}]({store_link}) — owned by {players_text}{metadata}{playtime}"
                    )
                lines.extend(
                    [
                        "Ranked by ownership and combined playtime; Steam metadata was checked for at most 20 top candidates.",
                        "Check each game's online support and lobby size before choosing.",
                    ]
                )
                message = "\n".join(lines)
            await _reply(interaction, message, ephemeral=False)
        except Exception as exc:
            await _handle_gaming_error(interaction, exc, ephemeral=False)

    @teams_group.command(name="make", description="Split this channel's party into teams")
    @app_commands.guild_only()
    async def teams_make(
        interaction: discord.Interaction,
        team_count: app_commands.Range[int, 2, 4] = 2,
        balanced: bool = True,
    ):
        scope = await _context(client, interaction)
        if scope is None:
            return
        try:
            from src.gaming import make_teams

            await _defer(interaction, ephemeral=False)
            players = await _db_call(_store(client).party, scope[0], scope[1])
            if len(players) < team_count or len(players) > 20:
                raise BotRequestError(
                    "Making teams requires at least one person per team and no more than 20 people."
                )
            teams_result = make_teams(players, team_count, balanced=balanced)
            lines = ["**Balanced teams**" if balanced else "**Teams**"]
            for index, team in enumerate(teams_result, 1):
                total_skill = sum(player.skill for player in team)
                members = ", ".join(
                    f"{_clean_name(player.display_name)} (skill {player.skill}, {_clean_name(player.role, 24)})"
                    for player in team
                )
                lines.append(f"Team {index} — skill total {total_skill}: {members}")
            lines.append(
                "Skill totals use self-reported 1–10 values; roles are supplied preferences."
            )
            await _reply(interaction, "\n".join(lines), ephemeral=False)
        except Exception as exc:
            await _handle_gaming_error(interaction, exc, ephemeral=False)

    client.tree.add_command(steam_group)
    client.tree.add_command(party_group)
    client.tree.add_command(games_group)
    client.tree.add_command(teams_group)
