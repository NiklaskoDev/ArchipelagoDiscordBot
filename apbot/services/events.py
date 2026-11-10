"""Discord scheduled events for the start of a game and the deadline of a sign-up round, in servers that turned them on.

An event follows its source: created when a start or deadline lies in the future, moved when that time changes, deleted
when the source is gone, past, or the server turned events off. Only what the bot account sees is announced.
"""

import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import discord

from apbot.db.guilds import GuildSettings, GuildSettingsStore
from apbot.db.live import ScheduledEventRow, ScheduledEventStore
from apbot.i18n import t
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError
from apbot.manager.views import SignupSummary, parse_signups
from apbot.services.game_cache import GameCache
from apbot.ui.format import clip
from apbot.ui.style import DISCORD_NAME_LIMIT

log = logging.getLogger(__name__)

EVENTS_INTERVAL = 300  # seconds, see Periodic in bot.py
GAME_EVENT_LENGTH = timedelta(hours=3)
DEADLINE_EVENT_LENGTH = timedelta(minutes=15)
FALLBACK_LOCATION = "Archipelago"
MOVED_BY = 1  # seconds a start may differ from the stored one before the event is moved (rounding)

GAME_START = "game_start"
SIGNUP_DEADLINE = "signup_deadline"


@dataclass(frozen=True)
class WantedEvent:
    name: str
    starts_at: float
    ends_at: float
    location: str


def _when(unix: float) -> datetime:
    return datetime.fromtimestamp(unix, tz=UTC)


class Events:
    def __init__(
        self,
        discord_client: discord.Client,
        store: ScheduledEventStore,
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

    async def sync(self) -> None:
        if not self._client.is_ready() or not self._cache.loaded:
            return
        try:
            signups = parse_signups(await self._manager.list_signups(self._bot_token))
        except (ManagerError, TypeError) as exc:
            log.debug("Could not read the sign-ups for the events: %s", getattr(exc, "detail", exc))
            return  # a list that failed to load must not delete every deadline event
        for guild in self._client.guilds:
            settings = self._settings.get(guild.id)
            wanted = self._wanted(settings, signups) if settings.scheduled_events else {}
            await self._sync_guild(guild, wanted)

    def _wanted(self, settings: GuildSettings, signups: list[SignupSummary]) -> dict[tuple[str, int], WantedEvent]:
        lang = settings.language
        now = self._now()
        site = self._cache.site_url
        wanted: dict[tuple[str, int], WantedEvent] = {}
        for game in self._cache.games:
            if game.visibility != "all" or game.finished_at is not None or not game.start_at or game.start_at <= now:
                continue
            wanted[(GAME_START, game.id)] = WantedEvent(
                name=clip(t("Start: {game}", lang, game=game.name), DISCORD_NAME_LIMIT),
                starts_at=game.start_at,
                ends_at=(_when(game.start_at) + GAME_EVENT_LENGTH).timestamp(),
                location=clip(game.address or site or FALLBACK_LOCATION, DISCORD_NAME_LIMIT),
            )
        for signup in signups:
            if not signup.open or not signup.deadline or signup.deadline <= now:
                continue
            wanted[(SIGNUP_DEADLINE, signup.id)] = WantedEvent(
                name=clip(t("Sign-ups close: {name}", lang, name=signup.name), DISCORD_NAME_LIMIT),
                starts_at=signup.deadline,
                ends_at=(_when(signup.deadline) + DEADLINE_EVENT_LENGTH).timestamp(),
                location=clip(site or FALLBACK_LOCATION, DISCORD_NAME_LIMIT),
            )
        return wanted

    async def _sync_guild(self, guild: Any, wanted: dict[tuple[str, int], WantedEvent]) -> None:
        """One event that Discord refuses must not keep the others of the server from syncing."""
        rows = {(row.source, row.source_id): row for row in self._store.for_guild(guild.id)}
        for key, row in rows.items():
            if key not in wanted:
                await self._attempt(key, self._delete(guild, row))
        for key, event in wanted.items():
            row = rows.get(key)
            if row is None:
                await self._attempt(key, self._create(guild, key, event))
            elif abs(row.starts_at - event.starts_at) > MOVED_BY:
                await self._attempt(key, self._move(guild, key, row, event))

    @staticmethod
    async def _attempt(key: tuple[str, int], action: Awaitable[None]) -> None:
        try:
            await action
        except discord.HTTPException as exc:
            log.warning("Could not update the scheduled event for %s %s: %s", key[0], key[1], exc.text or exc.status)

    async def _create(self, guild: Any, key: tuple[str, int], event: WantedEvent) -> None:
        created = await guild.create_scheduled_event(
            name=event.name,
            start_time=_when(event.starts_at),
            end_time=_when(event.ends_at),
            entity_type=discord.EntityType.external,
            privacy_level=discord.PrivacyLevel.guild_only,
            location=event.location,
        )
        self._store.save(guild.id, key[0], key[1], created.id, event.starts_at)

    async def _move(self, guild: Any, key: tuple[str, int], row: ScheduledEventRow, event: WantedEvent) -> None:
        try:
            existing = await guild.fetch_scheduled_event(row.event_id)
            await existing.edit(name=event.name, start_time=_when(event.starts_at), end_time=_when(event.ends_at))
        except discord.NotFound:
            await self._create(guild, key, event)  # deleted by hand
            return
        self._store.save(guild.id, key[0], key[1], row.event_id, event.starts_at)

    async def _delete(self, guild: Any, row: ScheduledEventRow) -> None:
        with contextlib.suppress(discord.NotFound):
            existing = await guild.fetch_scheduled_event(row.event_id)
            await existing.delete()
        self._store.delete(guild.id, row.source, row.source_id)
