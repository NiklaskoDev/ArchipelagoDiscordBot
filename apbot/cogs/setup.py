"""/setup: what a server admin configures. Needs Manage Server, or the admin role the server has set.

Every command checks the role itself (`Access.require_role`); `default_permissions` only hides the commands from people
who could not use them anyway, and a server may change that in its integration settings.
"""

import functools
import zoneinfo
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.errors import UserFacingError
from apbot.i18n import LANGUAGE_NAMES, LANGUAGES, language_of, t, translatable
from apbot.manager.errors import ManagerError
from apbot.services.manager_watch import ManagerReport, check_manager
from apbot.ui.embeds import manager_check_embed, notice_embed, settings_embed
from apbot.ui.respond import reply
from apbot.ui.style import AUTOCOMPLETE, SUCCESS

if TYPE_CHECKING:
    from apbot.bot import ApBot

TIMEZONE_MAX_LENGTH = 64

# What the bot needs in a channel, by what it will do there: the games channel gets one thread per game, the overview
# message is pinned, sign-up reminders are plain posts.
_BASIC = ("view_channel", "send_messages", "embed_links")
_REQUIRED_PERMISSIONS = {
    "games": (*_BASIC, "send_messages_in_threads", "create_public_threads", "manage_threads", "read_message_history"),
    "signups": _BASIC,
    "overview": (*_BASIC, "read_message_history", "manage_messages"),
}
_PERMISSION_LABELS = {
    "view_channel": translatable("View channel"),
    "send_messages": translatable("Send messages"),
    "embed_links": translatable("Embed links"),
    "send_messages_in_threads": translatable("Send messages in threads"),
    "create_public_threads": translatable("Create public threads"),
    "manage_threads": translatable("Manage threads"),
    "read_message_history": translatable("Read message history"),
    "manage_messages": translatable("Manage messages"),
}
_CHANNEL_SET = {
    "games": translatable("Games channel set to {channel}"),
    "signups": translatable("Sign-ups channel set to {channel}"),
    "overview": translatable("Overview channel set to {channel}"),
}
_ROLE_SET = {
    "organizer": translatable("Organizer role set to {role}"),
    "admin": translatable("Admin role set to {role}"),
}
_ROLE_CLEARED = {
    "organizer": translatable("Organizer role cleared"),
    "admin": translatable("Admin role cleared"),
}


def missing_permissions(permissions: discord.Permissions, kind: str) -> list[str]:
    """The permissions (as discord.py names them) the bot lacks for a channel of this kind."""
    return [name for name in _REQUIRED_PERMISSIONS[kind] if not getattr(permissions, name)]


@functools.cache
def _timezones() -> tuple[str, ...]:
    return tuple(sorted(zoneinfo.available_timezones()))


def timezone_choices(current: str) -> list[app_commands.Choice[str]]:
    needle = current.strip().casefold().replace(" ", "_")
    return [app_commands.Choice(name=name, value=name) for name in _timezones() if needle in name.casefold()][
        :AUTOCOMPLETE
    ]


CHANNEL_KIND_CHOICES = [
    app_commands.Choice(name=locale_str("games"), value="games"),
    app_commands.Choice(name=locale_str("signups"), value="signups"),
    app_commands.Choice(name=locale_str("overview"), value="overview"),
]
ROLE_KIND_CHOICES = [
    app_commands.Choice(name=locale_str("organizer"), value="organizer"),
    app_commands.Choice(name=locale_str("admin"), value="admin"),
]


class SetupCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    setup = app_commands.Group(
        name=locale_str("setup"),
        description=locale_str("Configure the bot for this server"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def _admin_only(self, interaction: discord.Interaction) -> str:
        """Checks the person may configure the server; returns their language."""
        self.bot.access.require_role(interaction, "admin")
        return language_of(interaction.locale)

    async def _check_manager(self) -> ManagerReport | None:
        """Asks the manager now. None when it cannot be reached or answers something unusable."""
        try:
            report = await check_manager(self.bot.manager, self.bot.config.manager_api_token)
        except ManagerError:
            return None
        if report.username is not None:
            self.bot.cache.set_site_url(report.site_url)
        return report

    @setup.command(name=locale_str("show"), description=locale_str("Show this server's settings and the manager check"))
    async def show(self, interaction: discord.Interaction) -> None:
        lang = self._admin_only(interaction)
        await interaction.response.defer(ephemeral=True)
        settings = self.bot.guild_settings.get(interaction.guild.id)
        report = await self._check_manager()
        await reply(
            interaction, embeds=[settings_embed(settings, lang), manager_check_embed(report, lang)], ephemeral=True
        )

    @setup.command(name=locale_str("manager"), description=locale_str("Check the connection to the manager now"))
    async def manager(self, interaction: discord.Interaction) -> None:
        lang = self._admin_only(interaction)
        await interaction.response.defer(ephemeral=True)
        await reply(interaction, embed=manager_check_embed(await self._check_manager(), lang), ephemeral=True)

    @setup.command(name=locale_str("channel"), description=locale_str("Choose the channel for games, sign-ups or the overview"))
    @app_commands.describe(kind=locale_str("What the channel is for"), channel=locale_str("The channel"))
    @app_commands.rename(kind=locale_str("kind"), channel=locale_str("channel"))
    @app_commands.choices(kind=CHANNEL_KIND_CHOICES)
    async def channel(self, interaction: discord.Interaction, kind: str, channel: discord.TextChannel) -> None:
        lang = self._admin_only(interaction)
        missing = missing_permissions(channel.permissions_for(interaction.guild.me), kind)
        if missing:
            labels = ", ".join(t(_PERMISSION_LABELS[name], lang) for name in missing)
            raise UserFacingError(
                "The bot is missing these permissions in {channel}: {permissions}",
                channel=channel.mention,
                permissions=labels,
            )
        self.bot.guild_settings.update(interaction.guild.id, **{f"{kind}_channel_id": channel.id})
        await reply(
            interaction, embed=notice_embed(t(_CHANNEL_SET[kind], lang, channel=channel.mention), SUCCESS), ephemeral=True
        )

    @setup.command(name=locale_str("role"), description=locale_str("Choose the organizer or admin role"))
    @app_commands.describe(kind=locale_str("Which role"), role=locale_str("The role"))
    @app_commands.rename(kind=locale_str("kind"), role=locale_str("role"))
    @app_commands.choices(kind=ROLE_KIND_CHOICES)
    async def role(self, interaction: discord.Interaction, kind: str, role: discord.Role) -> None:
        lang = self._admin_only(interaction)
        if role.is_default():
            # Everyone has it: as the admin role it would let everyone change these settings.
            raise UserFacingError("Pick a role other than @everyone")
        self.bot.guild_settings.update(interaction.guild.id, **{f"{kind}_role_id": role.id})
        await reply(interaction, embed=notice_embed(t(_ROLE_SET[kind], lang, role=role.mention), SUCCESS), ephemeral=True)

    @setup.command(name=locale_str("role_clear"), description=locale_str("Remove the organizer or admin role"))
    @app_commands.describe(kind=locale_str("Which role"))
    @app_commands.rename(kind=locale_str("kind"))
    @app_commands.choices(kind=ROLE_KIND_CHOICES)
    async def role_clear(self, interaction: discord.Interaction, kind: str) -> None:
        lang = self._admin_only(interaction)
        self.bot.guild_settings.update(interaction.guild.id, **{f"{kind}_role_id": None})
        await reply(interaction, embed=notice_embed(t(_ROLE_CLEARED[kind], lang), SUCCESS), ephemeral=True)

    @setup.command(name=locale_str("language"), description=locale_str("Set the language of the bot's posts in this server"))
    @app_commands.describe(language=locale_str("The language"))
    @app_commands.rename(language=locale_str("language"))
    @app_commands.choices(
        language=[app_commands.Choice(name=LANGUAGE_NAMES[code], value=code) for code in LANGUAGES]
    )
    async def language(self, interaction: discord.Interaction, language: str) -> None:
        lang = self._admin_only(interaction)
        if language not in LANGUAGES:
            raise UserFacingError("Pick a language from the list")
        self.bot.guild_settings.update(interaction.guild.id, language=language)
        await reply(
            interaction,
            embed=notice_embed(t("Server language set to {language}", lang, language=LANGUAGE_NAMES[language]), SUCCESS),
            ephemeral=True,
        )

    @setup.command(name=locale_str("timezone"), description=locale_str("Set the time zone for dates you type in this server"))
    @app_commands.describe(timezone=locale_str("The time zone, for example Europe/Berlin"))
    @app_commands.rename(timezone=locale_str("timezone"))
    async def timezone(
        self, interaction: discord.Interaction, timezone: app_commands.Range[str, 1, TIMEZONE_MAX_LENGTH]
    ) -> None:
        lang = self._admin_only(interaction)
        if timezone not in _timezones():
            raise UserFacingError("Pick a time zone from the list")
        self.bot.guild_settings.update(interaction.guild.id, timezone=timezone)
        await reply(
            interaction, embed=notice_embed(t("Time zone set to {timezone}", lang, timezone=timezone), SUCCESS), ephemeral=True
        )

    @timezone.autocomplete("timezone")
    async def timezone_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return timezone_choices(current)
