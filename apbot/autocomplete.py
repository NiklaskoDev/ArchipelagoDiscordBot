"""Suggestions for command options, answered from memory (or a short-lived cache) so they stay inside Discord's 3
seconds. A suggestion that cannot be made is left out, never an error: the command itself tells the person what is
wrong once they send it."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from apbot.errors import NotLinkedError, UserFacingError
from apbot.i18n import language_of, t
from apbot.manager.errors import ManagerError
from apbot.manager.views import GameSummary, listed_games
from apbot.services.slots import number_or_none
from apbot.services.text_client import Names
from apbot.ui.format import clip
from apbot.ui.style import AUTOCOMPLETE, CHOICE_NAME

if TYPE_CHECKING:
    from apbot.bot import ApBot

log = logging.getLogger(__name__)

# The only choice while the names load. Picking it is refused by /hint and /hint_location (see `is_loading_choice`).
LOADING_VALUE = "…"


def game_choices(games: list[GameSummary], current: str) -> list[app_commands.Choice[str]]:
    """Games whose name contains what was typed: the listed ones first, finished ones after. The value is the id."""
    listed = listed_games(games)
    finished = sorted((g for g in games if g.finished_at is not None), key=lambda g: -g.created_at)
    needle = current.strip().casefold()
    return [
        app_commands.Choice(name=clip(game.name, CHOICE_NAME), value=str(game.id))
        for game in [*listed, *finished]
        if needle in game.name.casefold()
    ][:AUTOCOMPLETE]


async def game_choices_public(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for a `game` option that works with the bot's own view of the games."""
    return game_choices(interaction.client.cache.games, current)


def _matches(name: str, current: str) -> bool:
    return current.strip().casefold() in name.casefold()


def _typed_number(interaction: discord.Interaction, option: str) -> int | None:
    """The game or slot number the person has already chosen in this command."""
    value = getattr(interaction.namespace, option, None)
    return number_or_none(value) if isinstance(value, str) else None


async def game_choices_mine(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The games the person plays in. Until the bot has looked at them once (after /link, or a restart) every game."""
    bot: ApBot = interaction.client
    games = bot.user_sync.games_of(interaction.user.id)
    if not games:
        return game_choices(bot.cache.games, current)
    return [
        app_commands.Choice(name=clip(game.name, CHOICE_NAME), value=str(game.id))
        for game in games
        if _matches(game.name, current)
    ][:AUTOCOMPLETE]


def _my_slots(interaction: discord.Interaction):
    game_id = _typed_number(interaction, "game")
    return () if game_id is None else interaction.client.user_sync.slots_of(interaction.user.id, game_id)


async def slot_choices_mine(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The person's own slots in the game they chose."""
    return [
        app_commands.Choice(name=clip(slot.name, CHOICE_NAME), value=str(slot.slot))
        for slot in _my_slots(interaction)
        if _matches(slot.name, current)
    ][:AUTOCOMPLETE]


async def claimable_slot_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The slots of the chosen game that the person may claim, as the manager shows them to this person."""
    bot: ApBot = interaction.client
    game_id = _typed_number(interaction, "game")
    if game_id is None:
        return []
    try:
        slots = await bot.slots.claimable(interaction.user.id, bot.access.user_token(interaction), game_id)
    except (NotLinkedError, UserFacingError, ManagerError, ValueError) as exc:
        log.debug("No claimable slots to suggest: %s", exc)
        return []
    return [
        app_commands.Choice(name=clip(f"{slot.name} ({slot.game})", CHOICE_NAME), value=str(slot.slot))
        for slot in slots
        if _matches(slot.name, current)
    ][:AUTOCOMPLETE]


def is_loading_choice(value: str) -> bool:
    return value == LOADING_VALUE


async def _name_choices(
    interaction: discord.Interaction, current: str, pick: Callable[[Names], tuple[str, ...]]
) -> list[app_commands.Choice[str]]:
    bot: ApBot = interaction.client
    game_id = _typed_number(interaction, "game")
    typed_slot = _typed_number(interaction, "slot")
    mine = _my_slots(interaction)
    slot = typed_slot if typed_slot is not None else (mine[0].slot if len(mine) == 1 else None)
    if game_id is None or slot is None:
        return []
    try:
        names = await bot.text_client.names(interaction.user.id, bot.access.user_token(interaction), game_id, slot)
    except (NotLinkedError, UserFacingError, ManagerError) as exc:
        log.debug("No names to suggest: %s", exc)
        return []
    if names is None:
        loading = t("Loading names… try again in a moment", language_of(interaction.locale))
        return [app_commands.Choice(name=loading, value=LOADING_VALUE)]
    return [
        app_commands.Choice(name=name, value=name)
        for name in pick(names)
        if len(name) <= CHOICE_NAME and _matches(name, current)
    ][:AUTOCOMPLETE]


async def item_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The names of items in the slot's world, for /hint. The first call starts loading them."""
    return await _name_choices(interaction, current, lambda names: names.items)


async def location_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The names of locations in the slot's world, for /hint_location."""
    return await _name_choices(interaction, current, lambda names: names.locations)


# --- sign-ups ---------------------------------------------------------------------------------------------------------
def _round_choices(rounds, current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=clip(signup.name, CHOICE_NAME), value=str(signup.id))
        for signup in rounds
        if _matches(signup.name, current)
    ][:AUTOCOMPLETE]


async def _rounds_to_pick(interaction: discord.Interaction, current: str, keep) -> list[app_commands.Choice[str]]:
    bot: ApBot = interaction.client
    try:
        rounds = await bot.signup_directory.rounds(interaction.user.id, bot.access.user_token(interaction))
    except (NotLinkedError, UserFacingError, ManagerError, TypeError) as exc:
        log.debug("No rounds to suggest: %s", exc)
        return []
    return _round_choices([signup for signup in rounds if keep(signup)], current)


async def signup_choices_open(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The rounds that take entries now (for joining and withdrawing)."""
    return await _rounds_to_pick(interaction, current, lambda signup: signup.open)


async def signup_choices_managed(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The rounds the person runs that are not generated yet (for closing and testing)."""
    return await _rounds_to_pick(
        interaction, current, lambda signup: signup.can_manage and signup.status in ("open", "closed")
    )


async def signup_choices_visible(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The rounds worth a look: the open ones, and the closed ones the person runs."""
    return await _rounds_to_pick(
        interaction,
        current,
        lambda signup: signup.open or (signup.can_manage and signup.status in ("open", "closed")),
    )


async def config_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The person's saved configs, by name; the value is the config's id."""
    bot: ApBot = interaction.client
    try:
        configs = await bot.signup_directory.configs(interaction.user.id, bot.access.user_token(interaction))
    except (NotLinkedError, UserFacingError, ManagerError, TypeError) as exc:
        log.debug("No configs to suggest: %s", exc)
        return []
    return [
        app_commands.Choice(name=clip(config.name, CHOICE_NAME), value=str(config.id))
        for config in configs
        if _matches(config.name, current)
    ][:AUTOCOMPLETE]


async def my_entry_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The person's own entries in the round they chose; the value is the entry's id."""
    bot: ApBot = interaction.client
    signup_id = _typed_number(interaction, "round")
    if signup_id is None:
        return []
    try:
        entries = await bot.signup_directory.my_entries(interaction.user.id, bot.access.user_token(interaction), signup_id)
    except (NotLinkedError, UserFacingError, ManagerError, ValueError, TypeError) as exc:
        log.debug("No entries to suggest: %s", exc)
        return []
    return [
        app_commands.Choice(name=clip(entry.name or "?", CHOICE_NAME), value=str(entry.id))
        for entry in entries
        if _matches(entry.name or "?", current)
    ][:AUTOCOMPLETE]
