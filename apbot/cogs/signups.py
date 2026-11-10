"""/signups: the sign-up rounds that take entries. Public: it uses the bot's own view."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.i18n import language_of
from apbot.manager.views import open_signups, parse_signups
from apbot.ui.embeds import signups_embed
from apbot.ui.respond import reply

if TYPE_CHECKING:
    from apbot.bot import ApBot


class SignupsCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    @app_commands.command(name=locale_str("signups"), description=locale_str("Show the open sign-up rounds"))
    async def signups(self, interaction: discord.Interaction) -> None:
        lang = language_of(interaction.locale)
        await interaction.response.defer()
        raw = await self.bot.manager.list_signups(self.bot.config.manager_api_token)
        await reply(interaction, embed=signups_embed(open_signups(parse_signups(raw)), lang))
