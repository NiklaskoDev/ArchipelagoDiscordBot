"""Buttons that keep working after a restart: the answers to a slot offered in a direct message, and the marks for a hint.

The `custom_id` carries game, slot and hint numbers only; who clicked is always the person who pressed the button, and
their own linked token is used (never one named in the id).
"""

import contextlib
import re
from typing import TYPE_CHECKING

import discord

from apbot.i18n import language_of, t, translatable
from apbot.manager.views import parse_game_summary
from apbot.ui.embeds import notice_embed
from apbot.ui.format import safe_text
from apbot.ui.respond import reply
from apbot.ui.style import NAME_LIMIT, SUCCESS
from apbot.ui.tracker import hint_status_label

if TYPE_CHECKING:
    from apbot.bot import ApBot

ACCEPT_ID = "apbot:ho:accept:{game}:{slot}"
DECLINE_ID = "apbot:ho:decline:{game}:{slot}"
HINT_ID = "apbot:hint:{game}:{slot}:{finder}:{location}:{status}"
_IDS = r"(?P<game>[0-9]{1,9}):(?P<slot>[0-9]{1,9})"  # [0-9], not \d: that would take any script's digits
_HINT_IDS = rf"{_IDS}:(?P<finder>[0-9]{{1,9}}):(?P<location>[0-9]{{1,16}}):(?P<status>10|20|30)"

# What a hint can be marked as, in the order of the buttons: (status the manager takes, label, style)
HINT_STATUS_BUTTONS = (
    (30, translatable("Mark as priority"), discord.ButtonStyle.primary),
    (10, translatable("No priority"), discord.ButtonStyle.secondary),
    (20, translatable("Avoid"), discord.ButtonStyle.secondary),
)


async def _remove_buttons(interaction: discord.Interaction) -> None:
    """The offer is answered: the message no longer shows its buttons."""
    if interaction.message is not None:
        with contextlib.suppress(discord.HTTPException):
            await interaction.message.edit(view=None)


class HandoverAcceptButton(discord.ui.DynamicItem[discord.ui.Button], template=rf"apbot:ho:accept:{_IDS}"):
    def __init__(self, game_id: int, slot: int, lang: str = "en"):
        super().__init__(
            discord.ui.Button(
                label=t("Take over slot", lang),
                style=discord.ButtonStyle.primary,
                custom_id=ACCEPT_ID.format(game=game_id, slot=slot),
            )
        )
        self._game_id, self._slot = game_id, slot

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        return cls(int(match["game"]), int(match["slot"]), language_of(interaction.locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ApBot = interaction.client
        lang = language_of(interaction.locale)
        token = bot.access.user_token(interaction)  # not linked: say so before anything shows as "thinking"
        await interaction.response.defer(ephemeral=True, thinking=True)
        raw = await bot.access.call_as(
            interaction, token, lambda user_token: bot.manager.accept_handover(user_token, self._game_id, self._slot)
        )
        await _remove_buttons(interaction)
        game = safe_text(parse_game_summary(raw).name, NAME_LIMIT)
        await reply(interaction, embed=notice_embed(t("You took over a slot in **{game}**", lang, game=game), SUCCESS), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.client.on_app_command_error(interaction, error)


class HandoverDeclineButton(discord.ui.DynamicItem[discord.ui.Button], template=rf"apbot:ho:decline:{_IDS}"):
    def __init__(self, game_id: int, slot: int, lang: str = "en"):
        super().__init__(
            discord.ui.Button(
                label=t("Decline", lang),
                style=discord.ButtonStyle.secondary,
                custom_id=DECLINE_ID.format(game=game_id, slot=slot),
            )
        )
        self._game_id, self._slot = game_id, slot

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        return cls(int(match["game"]), int(match["slot"]), language_of(interaction.locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ApBot = interaction.client
        lang = language_of(interaction.locale)
        token = bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await bot.access.call_as(
            interaction, token, lambda user_token: bot.manager.cancel_handover(user_token, self._game_id, self._slot)
        )
        await _remove_buttons(interaction)
        await reply(interaction, embed=notice_embed(t("You declined the offer", lang)), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.client.on_app_command_error(interaction, error)


def handover_view(game_id: int, slot: int, lang: str) -> discord.ui.View:
    """The two buttons under the direct message that offers a slot."""
    view = discord.ui.View(timeout=None)
    view.add_item(HandoverAcceptButton(game_id, slot, lang))
    view.add_item(HandoverDeclineButton(game_id, slot, lang))
    return view


class HintStatusButton(discord.ui.DynamicItem[discord.ui.Button], template=rf"apbot:hint:{_HINT_IDS}"):
    """Marks one hint for an item the person waits for as priority, no priority or avoid."""

    def __init__(self, game_id: int, slot: int, finder: int, location: int, status: int, lang: str = "en"):
        _, label, style = next(button for button in HINT_STATUS_BUTTONS if button[0] == status)
        super().__init__(
            discord.ui.Button(
                label=t(label, lang),
                style=style,
                custom_id=HINT_ID.format(game=game_id, slot=slot, finder=finder, location=location, status=status),
            )
        )
        self._ids = (game_id, slot, finder, location)
        self._status = status

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        ids = (int(match[name]) for name in ("game", "slot", "finder", "location"))
        return cls(*ids, int(match["status"]), language_of(interaction.locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ApBot = interaction.client
        lang = language_of(interaction.locale)
        token = bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        game_id, slot, finder, location = self._ids
        await bot.access.call_as(
            interaction,
            token,
            lambda user_token: bot.manager.set_hint_status(user_token, game_id, slot, finder, location, self._status),
        )
        await _remove_buttons(interaction)
        label = hint_status_label(self._status, lang)
        await reply(interaction, embed=notice_embed(t("The hint is marked: {status}", lang, status=label), SUCCESS), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.client.on_app_command_error(interaction, error)


def hint_status_view(game_id: int, slot: int, finder: int, location: int, lang: str) -> discord.ui.View:
    """The three buttons that mark one hint; they keep working after a restart."""
    view = discord.ui.View(timeout=None)
    for status, _, _ in HINT_STATUS_BUTTONS:
        view.add_item(HintStatusButton(game_id, slot, finder, location, status, lang))
    return view
