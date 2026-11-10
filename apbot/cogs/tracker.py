"""/tracker and /hints: what one of your slots has received and checked, and its open hints.

Only the player of a slot sees its tracker (the manager decides; the answer is private to them as well). Hints that
are waiting for you can be marked right there."""

from collections.abc import Collection
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.cogs.common import SlotText, begin_slot_command, call_manager, slot_options
from apbot.i18n import language_of
from apbot.manager.views import parse_tracker
from apbot.ui.respond import reply
from apbot.ui.tracker import SECTIONS, tracker_embed
from apbot.ui.views import hint_picker

if TYPE_CHECKING:
    from apbot.bot import ApBot


class TrackerCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    @app_commands.command(
        name=locale_str("tracker"), description=locale_str("Show what one of your slots has received and checked")
    )
    @slot_options
    async def tracker(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20], slot: SlotText | None = None) -> None:
        await self._show(interaction, game, slot, SECTIONS)

    @app_commands.command(name=locale_str("hints"), description=locale_str("Show the open hints of one of your slots"))
    @slot_options
    async def hints(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20], slot: SlotText | None = None) -> None:
        await self._show(interaction, game, slot, ("hints",))

    async def _show(self, interaction: discord.Interaction, game: str, slot: str | None, sections: Collection[str]) -> None:
        lang = language_of(interaction.locale)
        target = await begin_slot_command(self.bot, interaction, game, slot)
        raw = await call_manager(
            self.bot, interaction, target.token, lambda token: self.bot.manager.tracker(token, target.game_id, target.slot)
        )
        tracker = parse_tracker(raw)
        picker = hint_picker(interaction.user.id, lang, target.game_id, target.slot, tracker.hints)
        await reply(interaction, embed=tracker_embed(tracker, lang, sections=sections), view=picker, ephemeral=True)
