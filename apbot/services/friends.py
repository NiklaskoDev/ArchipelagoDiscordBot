"""A person's friends on the manager, asked with their own token and kept for a minute.

Friends are an own, recent feature of the manager: an older one has no `/api/friends` route and answers 404, which this
module turns into one clear message. The commands that write friend data ask for the overview first, so an older
manager stops them before anything is sent.
"""

import time
from collections.abc import Callable

from apbot.errors import UserFacingError
from apbot.i18n import translatable
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerUnavailable, NotFound
from apbot.manager.views import FriendsOverview, parse_friends
from apbot.services.ttl_cache import TtlCache

FRIENDS_TTL = 60.0  # seconds
MAX_CACHED = 500


class FriendsUnsupported(UserFacingError):
    """The manager is too old to have friends."""

    def __init__(self):
        super().__init__(translatable("This manager has no friends feature yet. Ask an admin to update the Archipelago Manager."))


class FriendDirectory:
    def __init__(self, client: ManagerClient, *, now: Callable[[], float] = time.monotonic):
        self._client = client
        self._overviews: TtlCache[FriendsOverview] = TtlCache(FRIENDS_TTL, MAX_CACHED, now=now)

    async def overview(self, owner: int, token: str, *, quick: bool = False) -> FriendsOverview:
        """The person's friends and requests. `owner` (a Discord id) keys the cache; nothing is cached when the call
        fails. Raises FriendsUnsupported on a manager without friends."""
        cached = self._overviews.get(owner)
        if cached is not None:
            return cached
        try:
            raw = await self._client.friends(token, quick=quick)
        except NotFound:
            raise FriendsUnsupported from None
        try:
            overview = parse_friends(raw)
        except ValueError:
            raise ManagerUnavailable(None, "The manager sent a friend list the bot does not know") from None
        self._overviews.set(owner, overview)
        return overview

    def forget(self, owner: int) -> None:
        """The person's friends changed (or their link did), so the list must be asked again."""
        self._overviews.discard(owner)
