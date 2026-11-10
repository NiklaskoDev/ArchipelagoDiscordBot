"""The pinned overview message of each server that has an overview channel, and the bot's presence.

The message is edited only when its content changed, so a quiet server causes no traffic. If someone deletes it, it is
posted and pinned again. Everything on it is what the bot account sees, never more.
"""

import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import discord

from apbot.db.guilds import GuildSettingsStore
from apbot.db.live import OverviewStore
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError
from apbot.manager.views import SignupSummary, listed_games, open_signups, parse_signups
from apbot.services.game_cache import GameCache
from apbot.ui.embeds import overview_embed

log = logging.getLogger(__name__)

OVERVIEW_INTERVAL = 60  # seconds, see Periodic in bot.py
VERIFY_INTERVAL = 600  # how often an unchanged message is checked to still exist


class Overview:
    def __init__(
        self,
        discord_client: discord.Client,
        store: OverviewStore,
        guild_settings: GuildSettingsStore,
        cache: GameCache,
        manager: ManagerClient,
        bot_token: str,
        *,
        now: Callable[[], float] = time.monotonic,
    ):
        self._client = discord_client
        self._store = store
        self._settings = guild_settings
        self._cache = cache
        self._manager = manager
        self._bot_token = bot_token
        self._now = now
        self._presence: str | None = None
        self._verified_at: dict[int, float] = {}

    async def run(self) -> None:
        if not self._client.is_ready() or not self._cache.loaded:
            return
        games = [game for game in self._cache.games if game.visibility == "all"]
        await self._update_presence(sum(1 for game in games if game.state == "running"))
        signups = await self._signups()
        if signups is None:
            return  # better an old overview than one that says no sign-up round is open
        shown_games, shown_signups = listed_games(games), open_signups(signups)
        for guild in self._client.guilds:
            settings = self._settings.get(guild.id)
            channel = guild.get_channel(settings.overview_channel_id) if settings.overview_channel_id else None
            if channel is None:
                continue
            try:
                await self._update_guild(guild, channel, overview_embed(shown_games, shown_signups, settings.language))
            except discord.HTTPException as exc:
                log.warning("Could not update the overview of a server: %s", exc.text or exc.status)

    async def _signups(self) -> list[SignupSummary] | None:
        try:
            return parse_signups(await self._manager.list_signups(self._bot_token))
        except (ManagerError, TypeError) as exc:
            log.debug("Could not read the sign-ups for the overview: %s", getattr(exc, "detail", exc))
            return None

    async def _update_presence(self, running: int) -> None:
        text = f"{running} game online" if running == 1 else f"{running} games online"
        if text == self._presence:
            return
        await self._client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=text))
        self._presence = text

    async def _update_guild(self, guild: Any, channel: Any, embed: discord.Embed) -> None:
        digest = hashlib.sha256(json.dumps(embed.to_dict(), sort_keys=True).encode()).hexdigest()
        stored = self._store.get(guild.id)
        if stored is not None and stored.channel_id == channel.id:
            message = channel.get_partial_message(stored.message_id)
            try:
                if stored.content_hash != digest:
                    await message.edit(embed=embed)
                    self._store.save(guild.id, channel.id, stored.message_id, digest)
                    self._verified_at[guild.id] = self._now()
                    return
                if self._now() - self._verified_at.get(guild.id, float("-inf")) < VERIFY_INTERVAL:
                    return
                await message.fetch()
                self._verified_at[guild.id] = self._now()
                return
            except discord.NotFound:
                pass  # deleted by hand: posted again below
        elif stored is not None:
            await self._remove_old(guild, stored.channel_id, stored.message_id)
        await self._post(guild, channel, embed, digest)

    async def _post(self, guild: Any, channel: Any, embed: discord.Embed, digest: str) -> None:
        message = await channel.send(embed=embed)
        self._store.save(guild.id, channel.id, message.id, digest)
        self._verified_at[guild.id] = self._now()
        try:
            await message.pin()
        except discord.HTTPException as exc:
            log.warning("Could not pin the overview message: %s", exc.text or exc.status)

    async def _remove_old(self, guild: Any, channel_id: int, message_id: int) -> None:
        """The overview moved to another channel: the message in the old one is deleted if the bot still can."""
        old = guild.get_channel(channel_id)
        if old is not None:
            with contextlib.suppress(discord.HTTPException):
                await old.get_partial_message(message_id).delete()
