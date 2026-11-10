"""Reads each game's activity feed and hands what is new to whoever wants it (the thread relay, the direct messages).

The manager numbers the entries of a game. The bot remembers the last number it handled (`FeedCursorStore`), so after a
restart nothing is posted twice, and the first look at a game only finds out where the feed stands: the past is not
replayed into a thread.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apbot.access import Access
from apbot.db.links import LinkStore
from apbot.db.live import FeedCursorStore
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError, TokenInvalid
from apbot.manager.views import FeedEntry, parse_activity
from apbot.services.game_cache import GameCache
from apbot.services.user_sync import UserSync

log = logging.getLogger(__name__)

TICK = 5  # seconds, see Periodic in bot.py: the shortest time between two looks at a game
ACTIVE_INTERVAL = 5  # for a game whose server is up or starting
IDLE_INTERVAL = 60  # for the others: something may still be said, but nobody waits for it
ACTIVE_STATES = ("starting", "running")


@dataclass(frozen=True)
class FeedGame:
    id: int
    name: str
    state: str
    public: bool  # the bot account itself sees it, so it may be shown in a server (private games never are)


Sink = Callable[[FeedGame, tuple[FeedEntry, ...]], Awaitable[None]]


class FeedPoller:
    def __init__(
        self,
        client: ManagerClient,
        bot_token: str,
        cache: GameCache,
        user_sync: UserSync,
        links: LinkStore,
        access: Access,
        cursors: FeedCursorStore,
        *,
        now: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._bot_token = bot_token
        self._cache = cache
        self._user_sync = user_sync
        self._links = links
        self._access = access
        self._cursors = cursors
        self._now = now
        self._sinks: list[tuple[Sink, bool]] = []
        self._due: dict[int, float] = {}

    def add_sink(self, sink: Sink, *, public_only: bool = False) -> None:
        """`sink` gets the new entries of every game; with `public_only` only those of games shown publicly."""
        self._sinks.append((sink, public_only))

    def games(self) -> list[FeedGame]:
        """The games to follow: the ones the bot account sees, and the ones only a linked person plays in."""
        found = {
            game.id: FeedGame(game.id, game.name, game.state, game.visibility == "all")
            for game in self._cache.games
            if game.finished_at is None
        }
        for game in self._user_sync.known_games().values():
            if game.id not in found and game.finished_at is None:
                found[game.id] = FeedGame(game.id, game.name, game.state, public=False)
        return list(found.values())

    def _reader(self, game: FeedGame) -> tuple[str, int | None] | None:
        """The token to read a game with, and whose it is (None for the bot's): the bot's own when the bot account may
        see the game, else the first linked person who plays in it."""
        if any(cached.id == game.id for cached in self._cache.games):
            return self._bot_token, None
        for discord_id in self._user_sync.participants(game.id):
            link = self._links.get(discord_id)
            token = self._access.stored_token(link) if link else None
            if token:
                return token, discord_id
        return None

    async def run_once(self) -> None:
        games = self.games()
        self._due = {game_id: due for game_id, due in self._due.items() if game_id in {g.id for g in games}}
        now = self._now()
        for game in games:
            if self._due.get(game.id, 0) > now:
                continue
            self._due[game.id] = now + (ACTIVE_INTERVAL if game.state in ACTIVE_STATES else IDLE_INTERVAL)
            await self.poll(game)

    async def poll(self, game: FeedGame) -> None:
        """Looks at one game's feed and passes on what is new."""
        reader = self._reader(game)
        if reader is None:
            return
        token, owner = reader
        cursor = self._cursors.get(game.id)
        try:
            page = parse_activity(await self._client.activity(token, game.id, cursor or 0))
        except TokenInvalid:
            if owner is not None:
                self._access.on_token_invalid(owner)
            return
        except ManagerError as exc:
            log.debug("Could not read the activity of game %s: %s", game.id, exc.detail)
            return
        except ValueError as exc:
            log.warning("Skipping the activity of game %s: %s", game.id, exc)
            return
        if cursor is None or page.last < cursor:
            # Never read before (the past stays in the manager), or the manager's numbering started over.
            self._cursors.set(game.id, page.last)
            return
        if page.last > cursor:
            self._cursors.set(game.id, page.last)  # before handing on: a failing sink must not repeat the entries
        entries = tuple(entry for entry in page.entries if entry.seq > cursor)
        if entries:
            await self._hand_on(game, entries)

    async def _hand_on(self, game: FeedGame, entries: tuple[FeedEntry, ...]) -> None:
        for sink, public_only in self._sinks:
            if public_only and not game.public:
                continue
            try:
                await sink(game, entries)
            except Exception:
                log.exception("Handling the activity of game %s failed", game.id)
