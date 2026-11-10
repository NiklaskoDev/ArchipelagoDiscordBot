"""Reminders in the sign-ups channel shortly before an open round closes.

Each reminder is posted once per server, round, deadline and lead time (24 h and 1 h before); a deadline that moved gets
its reminders again. Only the most urgent one is posted when several are due at once (the round was opened late, or the
bot was down), and the others count as done. Only what the bot account sees is announced.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import discord

from apbot.db.guilds import GuildSettingsStore
from apbot.db.reminders import ReminderStore
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError
from apbot.manager.views import SignupSummary, open_signups, parse_signups
from apbot.services.game_cache import GameCache
from apbot.ui.signups import signup_reminder_embed
from apbot.ui.views import open_in_manager_view

log = logging.getLogger(__name__)

REMINDER_INTERVAL = 300  # seconds, see Periodic in bot.py
LEAD_TIMES = (24 * 3600, 3600)  # seconds before the deadline, longest first


class SignupReminders:
    def __init__(
        self,
        discord_client: discord.Client,
        store: ReminderStore,
        guild_settings: GuildSettingsStore,
        cache: GameCache,
        manager: ManagerClient,
        bot_token: str,
        *,
        now: Callable[[], float] = time.time,
    ):
        self._client = discord_client
        self._store = store
        self._settings = guild_settings
        self._cache = cache
        self._manager = manager
        self._bot_token = bot_token
        self._now = now

    async def run(self) -> None:
        if not self._client.is_ready():
            return
        try:
            rounds = open_signups(parse_signups(await self._manager.list_signups(self._bot_token)))
        except (ManagerError, TypeError) as exc:
            log.debug("Could not read the sign-ups for the reminders: %s", getattr(exc, "detail", exc))
            return  # nothing is forgotten or posted on a list that failed to load
        with_deadline = [signup for signup in rounds if signup.deadline]
        for guild in self._client.guilds:
            channel_id = self._settings.get(guild.id).signups_channel_id
            if channel_id is None:
                continue
            self._store.forget_other_rounds(guild.id, [signup.id for signup in rounds])
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            for signup in with_deadline:
                await self._remind(guild, channel, signup)

    def _due(self, guild_id: int, signup: SignupSummary) -> list[int]:
        """The lead times whose moment has come (the deadline is closer than that) and that were not posted yet."""
        remaining = (signup.deadline or 0) - self._now()
        return [
            before
            for before in LEAD_TIMES
            if 0 < remaining <= before and not self._store.was_sent(guild_id, signup.id, signup.deadline, before)
        ]

    async def _remind(self, guild: Any, channel: Any, signup: SignupSummary) -> None:
        due = self._due(guild.id, signup)
        if not due:
            return
        settings = self._settings.get(guild.id)
        embed = signup_reminder_embed(signup, settings.language)
        view = open_in_manager_view(self._cache.site_url, f"/signups/{signup.id}", settings.language)
        try:
            await channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.NotFound) as exc:
            # The channel cannot be written to; trying again every few minutes would only repeat the warning.
            log.warning("Could not post a sign-up reminder in a server: %s", exc.text or exc.status)
        except discord.HTTPException as exc:
            log.warning("Could not post a sign-up reminder in a server, trying again: %s", exc.text or exc.status)
            return
        for before in due:
            self._store.mark_sent(guild.id, signup.id, signup.deadline, before)
