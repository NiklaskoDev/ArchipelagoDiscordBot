"""/alerts: which direct messages the bot may send to a linked person. Everything is off until they choose it."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.db.live import ALERT_KINDS
from apbot.errors import NotLinkedError
from apbot.i18n import language_of, t
from apbot.ui.embeds import ALERT_KIND_LABELS, alerts_text, notice_embed
from apbot.ui.style import SUCCESS
from apbot.ui.views import MultiSelectView

if TYPE_CHECKING:
    from apbot.bot import ApBot


class AlertsCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    @app_commands.command(name=locale_str("alerts"), description=locale_str("Choose which direct messages the bot sends you"))
    async def alerts(self, interaction: discord.Interaction) -> None:
        lang = language_of(interaction.locale)
        if self.bot.links.get(interaction.user.id) is None:
            raise NotLinkedError
        view = MultiSelectView(
            interaction.user.id,
            lang,
            [(kind, t(ALERT_KIND_LABELS[kind], lang)) for kind in ALERT_KINDS],
            self.bot.alert_prefs.enabled(interaction.user.id),
            self._save,
            placeholder=t("Choose your alerts", lang),
        )
        embed = notice_embed(
            t("Choose which direct messages you want. Nothing is sent until you pick something. Your Discord settings must allow messages from this server's members.", lang)
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _save(self, interaction: discord.Interaction, kinds: list[str]) -> discord.Embed:
        if self.bot.links.get(interaction.user.id) is None:  # unlinked while the list was open
            raise NotLinkedError
        self.bot.alert_prefs.replace(interaction.user.id, kinds)
        lang = language_of(interaction.locale)
        if not kinds:
            return notice_embed(t("All alerts are off", lang), SUCCESS)
        return notice_embed(t("Saved. You get direct messages when:\n{alerts}", lang, alerts=alerts_text(kinds, lang)), SUCCESS)
