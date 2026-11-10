"""The bot: connects to Discord, owns the services and the manager client, and answers errors in one place."""

import asyncio
import contextlib
import logging

import discord
from discord import app_commands
from discord.ext import commands

from apbot.access import Access
from apbot.cogs.games import GamesCog
from apbot.cogs.linking import LinkingCog
from apbot.cogs.setup import SetupCog
from apbot.cogs.signups import SignupsCog
from apbot.config import Config
from apbot.crypto import TokenCipher
from apbot.db.database import DATABASE_FILE, Database
from apbot.db.guilds import GuildSettingsStore
from apbot.db.links import LinkStore
from apbot.errors import user_message
from apbot.heartbeat import HEARTBEAT_INTERVAL, Heartbeat
from apbot.i18n import BotTranslator, language_of, t
from apbot.manager.client import ManagerClient
from apbot.services.game_cache import REFRESH_INTERVAL, GameCache
from apbot.services.manager_watch import RETRY_INTERVAL, ManagerWatch, StartupFailure
from apbot.services.scheduler import Periodic, StopPeriodic
from apbot.ui.embeds import error_embed

log = logging.getLogger(__name__)


class ApBot(commands.Bot):
    def __init__(self, config: Config, *, manager: ManagerClient | None = None):
        intents = discord.Intents.none()
        intents.guilds = True  # no privileged intents, and no message content: there are no text commands
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.manager = manager or ManagerClient(config.manager_url)
        self.db = Database(config.data_dir / DATABASE_FILE)
        self.db.migrate()
        self.links = LinkStore(self.db)
        self.guild_settings = GuildSettingsStore(self.db)
        self.cipher = TokenCipher(config.encryption_keys)
        self.access = Access(self.links, self.guild_settings, self.cipher)
        self.cache = GameCache(self.manager, config.manager_api_token)
        self.exit_code = 0
        self._watch = ManagerWatch(self.manager, config.manager_api_token, self.cache)
        self._shutdown: asyncio.Task | None = None
        self._periodic = [
            Periodic("game-cache", REFRESH_INTERVAL, self.cache.run_refresh),
            Periodic("heartbeat", HEARTBEAT_INTERVAL, Heartbeat(config.data_dir, self.is_ready).beat),
        ]
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self) -> None:
        # A manager token the manager refuses ends the start here (StartupFailure); an unreachable manager does not.
        if not await self._watch.check_once():
            self._periodic.append(Periodic("manager-watch", RETRY_INTERVAL, self._retry_manager_check))
        await self.tree.set_translator(BotTranslator())
        await self.add_cog(GamesCog(self))
        await self.add_cog(SignupsCog(self))
        await self.add_cog(LinkingCog(self))
        await self.add_cog(SetupCog(self))
        await self._sync_commands()
        for task in self._periodic:
            task.start()

    async def _retry_manager_check(self) -> None:
        try:
            done = await self._watch.check_once()
        except StartupFailure as exc:
            log.error("%s", exc)
            self.exit_code = exc.exit_code
            self._shutdown = asyncio.create_task(self.close())  # not awaited here: close() stops this very task
            raise StopPeriodic from None
        if done:
            log.info("The manager is reachable now")
            raise StopPeriodic

    async def _sync_commands(self) -> None:
        guild_id = self.config.dev_guild_id
        if guild_id is None:
            await self.tree.sync()
            return
        guild = discord.Object(guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def close(self) -> None:
        for task in self._periodic:
            await task.stop()
        await self.manager.aclose()
        await super().close()
        self.db.close()

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """The bot left (or was removed from) a server: forget what was configured for it."""
        self.guild_settings.delete(guild.id)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        lang = language_of(interaction.locale)
        message = user_message(original, lang)
        if message is None:
            log.error("Unexpected error in a command", exc_info=original)
            message = t("Something went wrong. Try again later.", lang)
        embed = error_embed(message)
        with contextlib.suppress(discord.HTTPException):
            if interaction.response.is_done():
                # A public "thinking" message may be showing; errors are for the person who asked only.
                with contextlib.suppress(discord.HTTPException):
                    await interaction.delete_original_response()
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
