"""/friends shows the person's friends and friend requests and lets them answer; /friend add asks someone to be a
friend. Both act as the person, with the manager token they linked. Friends are the manager's own feature: on a manager
that does not have it the person is told so, and nothing is sent."""

from collections.abc import Mapping
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.cogs.common import resolve_username
from apbot.errors import UserFacingError
from apbot.i18n import language_of, t
from apbot.ui.embeds import notice_embed
from apbot.ui.format import safe_text
from apbot.ui.friends import FriendsView, friends_embed
from apbot.ui.respond import reply
from apbot.ui.style import NAME_LIMIT, SUCCESS

if TYPE_CHECKING:
    from apbot.bot import ApBot

USERNAME_LENGTH = 64  # the manager's limit for the username of a friend request
ASKED = "asked"
ACCEPTED = "accepted"  # the other person had asked already, so the request became a friendship


def _outcome(raw: object, fallback_name: str) -> tuple[str, str]:
    """(asked | accepted, the other person's manager username) from the manager's answer to a friend request."""
    if not isinstance(raw, Mapping):
        return ASKED, fallback_name
    user = raw.get("user")
    name = user.get("username") if isinstance(user, Mapping) else None
    return (ACCEPTED if raw.get("status") == ACCEPTED else ASKED), name if isinstance(name, str) and name else fallback_name


class FriendsCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    friend = app_commands.Group(name=locale_str("friend"), description=locale_str("Ask someone to be your friend"))

    @app_commands.command(
        name=locale_str("friends"), description=locale_str("Show your friends and friend requests on the manager")
    )
    async def friends(self, interaction: discord.Interaction) -> None:
        lang = language_of(interaction.locale)
        token = self.bot.access.user_token(interaction)  # not linked: say so before anything shows as "thinking"
        await interaction.response.defer(ephemeral=True)
        user = interaction.user.id
        self.bot.friend_directory.forget(user)  # what is shown here must be current: it decides what is answered
        overview = await self.bot.access.call_as(
            interaction, token, lambda user_token: self.bot.friend_directory.overview(user, user_token)
        )
        view = None if overview.is_empty else FriendsView(user, lang, overview)
        await reply(interaction, embed=friends_embed(overview, lang), view=view, ephemeral=True)

    @friend.command(name=locale_str("add"), description=locale_str("Ask a member or a manager user to be your friend"))
    @app_commands.describe(
        member=locale_str("A Discord member who linked their manager account"),
        username=locale_str("A manager username"),
    )
    @app_commands.rename(member=locale_str("member"), username=locale_str("username"))
    async def add(
        self,
        interaction: discord.Interaction,
        member: discord.User | discord.Member | None = None,
        username: app_commands.Range[str, 1, USERNAME_LENGTH] | None = None,
    ) -> None:
        lang = language_of(interaction.locale)
        wanted = resolve_username(self.bot, member, username)
        if wanted is None:
            raise UserFacingError("Choose a member or type a manager username")
        token = self.bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True)
        user = interaction.user.id
        # Asking first stops an older manager (no friends) before anything is sent to a route it does not have.
        await self.bot.access.call_as(
            interaction, token, lambda user_token: self.bot.friend_directory.overview(user, user_token)
        )
        raw = await self.bot.access.call_as(
            interaction, token, lambda user_token: self.bot.manager.ask_friend(user_token, wanted)
        )
        self.bot.friend_directory.forget(user)
        outcome, name = _outcome(raw, wanted)
        shown = f"<@{member.id}>" if member is not None else safe_text(name, NAME_LIMIT)
        if outcome == ACCEPTED:
            text = t("You and **{user}** are friends now: they had asked you already.", lang, user=shown)
        else:
            text = t("You asked **{user}** to be your friend. They see it in the manager.", lang, user=shown)
        await reply(interaction, embed=notice_embed(text, SUCCESS), ephemeral=True)
