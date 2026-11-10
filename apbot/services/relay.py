"""One public thread and one role per game, in every server that has a games channel.

The thread shows what happens in the game (the kinds of activity the server chose with `/setup feed`); the role goes to
the linked people who play in it, so they can be pinged when the game comes online. Only games the bot account itself
sees are shown here: a private game's feed reaches nobody but the players, as direct messages (see alerts.py).
When a game is finished the thread gets a summary, is archived and locked, and the role is deleted.
"""

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

import discord

from apbot.db.guilds import GuildSettings, GuildSettingsStore
from apbot.db.live import DEFAULT_FEED_KINDS, GameSpace, GameSpaceStore
from apbot.i18n import t
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError
from apbot.manager.views import FeedEntry, GameSummary, parse_game_detail
from apbot.services.feeds import FeedGame
from apbot.services.game_cache import GameCache
from apbot.ui.embeds import game_detail_embeds, game_state_embed
from apbot.ui.feed import batch_lines, feed_line
from apbot.ui.format import clip, safe_text
from apbot.ui.style import NAME_LIMIT

log = logging.getLogger(__name__)

MAINTAIN_INTERVAL = 60  # seconds, see Periodic in bot.py
THREAD_NAME_LIMIT = 100  # Discord's limit for the name of a thread and of a role
THREAD_ARCHIVE_MINUTES = 10080  # a quiet thread folds away after a week; posting into it opens it again
MEMBER_RECHECK = 600  # seconds before the bot looks again whether a linked person has joined a server
ROLE_PREFIX = "AP "
_NO_MENTIONS = discord.AllowedMentions.none()


class Relay:
    def __init__(
        self,
        discord_client: discord.Client,
        spaces: GameSpaceStore,
        guild_settings: GuildSettingsStore,
        cache: GameCache,
        manager: ManagerClient,
        bot_token: str,
        memberships: Callable[[], dict[int, frozenset[int]]],
        *,
        now: Callable[[], float] = time.monotonic,
    ):
        self._client = discord_client
        self._spaces = spaces
        self._settings = guild_settings
        self._cache = cache
        self._manager = manager
        self._bot_token = bot_token
        self._memberships = memberships
        self._now = now
        self._states: dict[int, str] = {}  # the state of each game at the last look, to notice one coming online
        self._applied: dict[tuple[int, int], tuple[frozenset[int], frozenset[int], float | None]] = {}

    # --- threads and roles -----------------------------------------------------------------------------------------
    async def maintain(self) -> None:
        """Makes the servers match the games: a thread and a role for each new game, a summary and a clean-up for each
        finished one, and a post when a game comes online."""
        if not self._cache.loaded:
            return
        games = {game.id: game for game in self._cache.games}
        shown = [game for game in games.values() if game.visibility == "all" and game.finished_at is None]
        online = [game for game in shown if game.state == "running" and self._states.get(game.id, "running") != "running"]
        changed = False
        for guild in self._client.guilds:
            try:
                changed |= await self._maintain_guild(guild, games, shown, online)
            except discord.HTTPException as exc:
                log.warning("Could not update the threads of a server: %s", exc.text or exc.status)
            except Exception:
                log.exception("Updating the threads of a server failed")
        self._states = {game.id: game.state for game in games.values()}
        if changed:
            for discord_id, game_ids in self._memberships().items():
                await self.sync_roles(discord_id, game_ids)

    async def _maintain_guild(
        self, guild: discord.Guild, games: dict[int, GameSummary], shown: list[GameSummary], online: list[GameSummary]
    ) -> bool:
        settings = self._settings.get(guild.id)
        changed = False
        for space in self._spaces.open_for_guild(guild.id):
            game = games.get(space.game_id)
            if game is None or game.finished_at is not None:
                await self._close(guild, space, settings)
                changed = True
            elif await self._repair(guild, space, game):
                changed = True
        if settings.games_channel_id is not None:
            channel = guild.get_channel(settings.games_channel_id)
            for game in shown:
                if channel is not None and self._spaces.get(guild.id, game.id) is None:
                    await self._create(guild, channel, game, settings)
                    changed = True
        for game in online:
            space = self._spaces.get(guild.id, game.id)
            if space is not None and space.closed_at is None:
                await self._announce_online(guild, space, game, settings)
        return changed

    async def _create(self, guild: Any, channel: Any, game: GameSummary, settings: GuildSettings) -> None:
        """A thread with the game's facts as its first message, then the role. The space is saved after the thread, so a
        failing role is made later (see `_repair`)."""
        thread = await channel.create_thread(
            name=clip(game.name, THREAD_NAME_LIMIT),
            type=discord.ChannelType.public_thread,
            auto_archive_duration=THREAD_ARCHIVE_MINUTES,
            reason="Archipelago game",
        )
        self._spaces.create(guild.id, game.id, thread.id, None, DEFAULT_FEED_KINDS)
        await thread.send(embed=await self._starter_embed(game, settings.language), allowed_mentions=_NO_MENTIONS)
        await self._make_role(guild, self._spaces.get(guild.id, game.id), game)

    async def _starter_embed(self, game: GameSummary, lang: str) -> discord.Embed:
        try:
            detail = parse_game_detail(await self._manager.game(self._bot_token, game.id))
        except (ManagerError, ValueError):
            return game_state_embed(game, lang)
        return game_detail_embeds(detail, lang)[0]  # the facts; the slots stay in /game

    async def _make_role(self, guild: Any, space: GameSpace, game: GameSummary) -> None:
        role = await guild.create_role(
            name=clip(ROLE_PREFIX + game.name, THREAD_NAME_LIMIT),
            permissions=discord.Permissions.none(),
            mentionable=False,
            reason="Archipelago game",
        )
        self._spaces.set_role(space.guild_id, space.game_id, role.id)

    async def _repair(self, guild: Any, space: GameSpace, game: GameSummary) -> bool:
        """Makes the role again when it could not be made or was deleted by hand. True when it did."""
        if space.role_id is not None and guild.get_role(space.role_id) is not None:
            return False
        await self._make_role(guild, space, game)
        return True

    async def _thread(self, guild: Any, space: GameSpace) -> Any | None:
        if space.thread_id is None:
            return None
        thread = guild.get_thread(space.thread_id)
        if thread is not None:
            return thread
        try:
            return await guild.fetch_channel(space.thread_id)  # an archived thread is not in the cache
        except discord.NotFound:
            return None

    async def _close(self, guild: Any, space: GameSpace, settings: GuildSettings) -> None:
        """The game is over: a summary, then the thread is archived and locked and the role deleted."""
        thread = await self._thread(guild, space)
        if thread is not None:
            try:
                await self._post_summary(thread, space.game_id, settings.language)
                await thread.edit(archived=True, locked=True, reason="The game is finished")
            except discord.HTTPException as exc:
                log.warning("Could not archive the thread of game %s: %s", space.game_id, exc.text or exc.status)
        role = guild.get_role(space.role_id) if space.role_id else None
        if role is not None:
            with contextlib.suppress(discord.HTTPException):
                await role.delete(reason="The game is finished")
        self._spaces.close(space.guild_id, space.game_id)

    async def _post_summary(self, thread: Any, game_id: int, lang: str) -> None:
        try:
            detail = parse_game_detail(await self._manager.game(self._bot_token, game_id))
        except (ManagerError, ValueError):
            return  # deleted from the manager, or it does not answer: the thread is archived without a summary
        await thread.send(embeds=game_detail_embeds(detail, lang), allowed_mentions=_NO_MENTIONS)

    async def _announce_online(self, guild: Any, space: GameSpace, game: GameSummary, settings: GuildSettings) -> None:
        thread = await self._thread(guild, space)
        if thread is None:
            return
        lang = settings.language
        text = t("**{game}** is online", lang, game=safe_text(game.name, NAME_LIMIT))
        if game.address:
            text += "\n" + t("Connect to {address}", lang, address=safe_text(game.address, NAME_LIMIT))
        role = guild.get_role(space.role_id) if space.role_id else None
        if role is None:
            await thread.send(text, allowed_mentions=_NO_MENTIONS)
            return
        # The role is not mentionable, so that nobody else can ping it; it is for the length of this post.
        await role.edit(mentionable=True, reason="Game online")
        try:
            await thread.send(f"{role.mention} {text}", allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role]))
        finally:
            await role.edit(mentionable=False, reason="Game online")

    # --- the feed --------------------------------------------------------------------------------------------------
    async def publish(self, game: FeedGame, entries: tuple[FeedEntry, ...]) -> None:
        """Posts new activity into the game's thread in every server that has one, as the server chose."""
        for space in self._spaces.open_for_game(game.id):
            guild = self._client.get_guild(space.guild_id)
            if guild is None:
                continue
            lang = self._settings.get(space.guild_id).language
            lines = [feed_line(entry, lang) for entry in entries if entry.kind in space.kinds]
            if not lines:
                continue
            try:
                thread = await self._thread(guild, space)
                if thread is None:
                    continue
                if getattr(thread, "archived", False):
                    await thread.edit(archived=False)
                for message in batch_lines(lines, lang):
                    await thread.send(message, allowed_mentions=_NO_MENTIONS)
            except discord.HTTPException as exc:
                log.warning("Could not post into the thread of game %s: %s", game.id, exc.text or exc.status)

    # --- roles -----------------------------------------------------------------------------------------------------
    async def sync_roles(self, discord_id: int, game_ids: frozenset[int]) -> None:
        """Gives a person the roles of the games they play in, in every server, and takes the others away."""
        for guild in self._client.guilds:
            spaces = [space for space in self._spaces.open_for_guild(guild.id) if space.role_id is not None]
            managed = frozenset(space.role_id for space in spaces)
            wanted = frozenset(space.role_id for space in spaces if space.game_id in game_ids)
            known = self._applied.get((guild.id, discord_id))
            if known and known[:2] == (wanted, managed) and (known[2] is None or self._now() < known[2]):
                continue
            try:
                member = await self._member(guild, discord_id)
                if member is not None:
                    await self._apply_roles(guild, member, wanted, managed)
            except discord.HTTPException as exc:
                log.warning("Could not change a member's game roles: %s", exc.text or exc.status)
                continue
            # a person who is not in the server yet is looked for again later; a member stays as set
            recheck_at = self._now() + MEMBER_RECHECK if member is None else None
            self._applied[(guild.id, discord_id)] = (wanted, managed, recheck_at)

    async def _member(self, guild: Any, discord_id: int) -> Any | None:
        try:
            return await guild.fetch_member(discord_id)
        except discord.NotFound:
            return None

    async def _apply_roles(self, guild: Any, member: Any, wanted: frozenset[int], managed: frozenset[int]) -> None:
        missing = [guild.get_role(role_id) for role_id in wanted if member.get_role(role_id) is None]
        surplus = [guild.get_role(role_id) for role_id in managed - wanted if member.get_role(role_id) is not None]
        if any(missing):
            await member.add_roles(*filter(None, missing), reason="Plays in this game")
        if any(surplus):
            await member.remove_roles(*filter(None, surplus), reason="Does not play in this game")
