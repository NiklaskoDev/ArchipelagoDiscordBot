"""/link, /unlink, /mydata: connecting a Discord user to their account on the manager.

The token is typed into a modal, which only the bot receives. It is checked against the manager, stored encrypted and
never shown, logged or sent back.
"""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.config import MANAGER_TOKEN_PATTERN
from apbot.errors import UserFacingError
from apbot.i18n import language_of, t
from apbot.manager.errors import TokenInvalid
from apbot.ui.embeds import link_status_embed, linked_embed, notice_embed
from apbot.ui.respond import reply
from apbot.ui.style import SUCCESS
from apbot.ui.views import ConfirmView

if TYPE_CHECKING:
    from apbot.bot import ApBot

TOKEN_MIN_LENGTH = 10
TOKEN_MAX_LENGTH = 200
TOKEN_HINT_LENGTH = 4  # how many characters of the end of a token are kept, so /mydata can tell tokens apart


class LinkModal(discord.ui.Modal):
    """One field for the token, and a short explanation of where to get it."""

    def __init__(self, cog: "LinkingCog", lang: str):
        super().__init__(title=t("Link manager account", lang))
        self._cog = cog
        self.token = discord.ui.TextInput(
            style=discord.TextStyle.short, min_length=TOKEN_MIN_LENGTH, max_length=TOKEN_MAX_LENGTH, required=True
        )
        self.add_item(
            discord.ui.TextDisplay(
                t(
                    "Create a token in the manager under API tokens: access *Read and change*, name it *Discord bot*."
                    " Then paste it here.",
                    lang,
                )
            )
        )
        self.add_item(discord.ui.Label(text=t("API token", lang), component=self.token))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._cog.link_with_token(interaction, self.token.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # Modals do not reach the command tree's error handler.
        await interaction.client.on_app_command_error(interaction, error)


def _manager_user(status: object) -> tuple[int, str]:
    """(id, username) of the account a manager answer is about. Raises UserFacingError when there is none or it is
    disabled."""
    user = status.get("user") if isinstance(status, dict) else None
    if (
        isinstance(user, dict)
        and isinstance(user.get("id"), int)
        and not isinstance(user.get("id"), bool)
        and isinstance(user.get("username"), str)
        and user["username"]
        and not user.get("disabled")
    ):
        return user["id"], user["username"]
    raise UserFacingError("This token is not valid")


class LinkingCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    @app_commands.command(name=locale_str("link"), description=locale_str("Link your manager account"))
    async def link(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(LinkModal(self, language_of(interaction.locale)))

    async def link_with_token(self, interaction: discord.Interaction, raw_token: str) -> None:
        """What happens when the modal is submitted."""
        lang = language_of(interaction.locale)
        await interaction.response.defer(ephemeral=True)
        token = raw_token.strip()
        if not MANAGER_TOKEN_PATTERN.match(token):
            raise UserFacingError("This token is not valid")
        try:
            status = await self.bot.manager.auth_status(token)
        except TokenInvalid:
            raise UserFacingError("This token is not valid") from None
        manager_user_id, username = _manager_user(status)
        owner = self.bot.links.by_manager_user(manager_user_id)
        if owner is not None and owner.discord_user_id != interaction.user.id:
            raise UserFacingError(
                "This manager account is already linked to another Discord account. Unlink it there first"
            )
        self.bot.links.save(
            interaction.user.id,
            manager_user_id,
            username,
            self.bot.cipher.encrypt(token),
            token[-TOKEN_HINT_LENGTH:],
            language_of(interaction.locale),
        )
        await reply(interaction, embed=linked_embed(username, lang), ephemeral=True)

    @app_commands.command(name=locale_str("unlink"), description=locale_str("Remove the link to your manager account"))
    async def unlink(self, interaction: discord.Interaction) -> None:
        lang = language_of(interaction.locale)
        if self.bot.links.get(interaction.user.id) is None:
            raise UserFacingError("No manager account is linked to your Discord account")
        view = ConfirmView(interaction.user.id, lang, confirm_label=t("Unlink", lang), danger=True)
        question = notice_embed(t("Unlink your manager account? The bot forgets your token and your settings.", lang))
        if not await view.ask(interaction, question):
            return
        self.bot.links.delete(interaction.user.id)
        done = notice_embed(t("Unlinked. Revoke the token in the manager under API tokens as well.", lang), SUCCESS)
        await interaction.edit_original_response(embed=done, view=None)

    @app_commands.command(name=locale_str("mydata"), description=locale_str("Show everything the bot stores about you"))
    async def mydata(self, interaction: discord.Interaction) -> None:
        lang = language_of(interaction.locale)
        await reply(interaction, embed=link_status_embed(self.bot.links.get(interaction.user.id), lang), ephemeral=True)
