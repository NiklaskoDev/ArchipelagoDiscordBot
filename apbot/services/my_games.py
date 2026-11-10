"""The games a linked person's own manager account can see, kept for a minute.

The bot's token only sees games with visibility "all". A person's token also sees the ones they play in, are offered a
slot in or organize (an admin's sees all). Those are shown to that person only, so the list is asked with their token
and never shared. Discord asks for suggestions on every keystroke and waits 3 seconds, hence the cache and the short
timeout of `quick` calls.
"""

import time
from collections.abc import Callable, Iterable

from apbot.manager.client import ManagerClient
from apbot.manager.views import GameSummary, parse_games
from apbot.services.ttl_cache import TtlCache

MY_GAMES_TTL = 60.0  # seconds
MAX_CACHED = 500


class MyGames:
    def __init__(self, client: ManagerClient, *, now: Callable[[], float] = time.monotonic):
        self._client = client
        self._games: TtlCache[tuple[GameSummary, ...]] = TtlCache(MY_GAMES_TTL, MAX_CACHED, now=now)

    async def games(self, owner: int, token: str, *, quick: bool = False) -> tuple[GameSummary, ...]:
        """Every game the token sees. `owner` (a Discord id) keys the cache. Nothing is cached when the call fails."""
        cached = self._games.get(owner)
        if cached is not None:
            return cached
        games = tuple(parse_games(await self._client.list_games(token, quick=quick)))
        self._games.set(owner, games)
        return games

    def cached(self, owner: int) -> tuple[GameSummary, ...] | None:
        """What is kept for the person, without asking the manager."""
        return self._games.get(owner)

    def forget(self, owner: int) -> None:
        """The link changed, so the list may not be theirs any more."""
        self._games.discard(owner)


def private_games(mine: Iterable[GameSummary], public: Iterable[GameSummary]) -> list[GameSummary]:
    """The games only the person's token revealed: those whose id the bot's own view does not have. `visibility` is not
    consulted; a game made public a moment ago counts as private until the bot sees it (the safe direction)."""
    public_ids = {game.id for game in public}
    return [game for game in mine if game.id not in public_ids]
