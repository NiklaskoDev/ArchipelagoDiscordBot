"""What the commands about one slot have in common: reading the game and slot options, acting as the person, and the
words for a game or slot the manager does not show them."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str

from apbot.autocomplete import game_choices_mine, slot_choices_mine
from apbot.cogs.games import parse_game_id
from apbot.errors import UserFacingError
from apbot.manager.errors import NotFound
from apbot.manager.views import parse_game_detail
from apbot.ui.format import safe_text
from apbot.ui.style import NAME_LIMIT
from apbot.services.slots import parse_slot_id

if TYPE_CHECKING:
    from apbot.bot import ApBot

SLOT_OPTION_LENGTH = 20  # digits of a slot number as text; Discord needs a limit on a text option


@dataclass(frozen=True)
class SlotTarget:
    """What a command acts on: the person's own token, the game and the slot."""

    token: str
    game_id: int
    slot: int


def slot_options(command):
    """The `game` and `slot` options every command about one of the person's slots has."""
    app_commands.describe(
        game=locale_str("The game"), slot=locale_str("Your slot, needed only if you have more than one")
    )(command)
    app_commands.rename(game=locale_str("game"), slot=locale_str("slot"))(command)
    app_commands.autocomplete(game=game_choices_mine, slot=slot_choices_mine)(command)
    return command


async def call_manager[T](
    bot: "ApBot", interaction: discord.Interaction, token: str, call: Callable[[str], Awaitable[T]]
) -> T:
    """Asks the manager as the person. A game or slot it does not show them is explained, not shown as "not found"."""
    try:
        return await bot.access.call_as(interaction, token, call)
    except NotFound:
        raise UserFacingError("That game or slot was not found. Pick them from the list.") from None


async def begin_slot_command(
    bot: "ApBot", interaction: discord.Interaction, game: str, slot: str | None
) -> SlotTarget:
    """Checks the options and the link before anything shows as "thinking", then defers (the manager takes a while) and
    finds the slot. The answer to the command is private."""
    game_id = parse_game_id(game)
    slot_number = parse_slot_id(slot) if slot is not None else None
    token = bot.access.user_token(interaction)
    await interaction.response.defer(ephemeral=True)
    resolved = await call_manager(bot, interaction, token, lambda user_token: bot.slots.resolve(user_token, game_id, slot_number))
    return SlotTarget(token, game_id, resolved)


def slot_label(raw: object, slot: int) -> str:
    """The name of a slot in a game the manager sent back (escaped); "#3" when the answer does not say."""
    try:
        detail = parse_game_detail(raw)
    except ValueError:
        return f"#{slot}"
    name = next((s.name for s in detail.slots if s.slot == slot), None)
    return safe_text(name, NAME_LIMIT) if name else f"#{slot}"


SlotText = app_commands.Range[str, 1, SLOT_OPTION_LENGTH]
