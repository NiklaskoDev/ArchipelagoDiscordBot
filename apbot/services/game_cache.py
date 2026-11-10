"""The games the bot account may see, kept fresh so public commands answer without waiting for the manager."""

import logging
import time
from collections.abc import Callable

from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError
from apbot.manager.views import GameSummary, parse_games

log = logging.getLogger(__name__)

REFRESH_INTERVAL = 60  # seconds, see Periodic in bot.py
SITE_URL_INTERVAL = 600


class GameCache:
    def __init__(self, client: ManagerClient, bot_token: str, *, now: Callable[[], float] = time.time):
        self._client = client
        self._token = bot_token
        self._now = now
        self._games: list[GameSummary] = []
        self._loaded = False
        self._site_url: str | None = None
        self._site_url_checked_at: float | None = None

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def games(self) -> list[GameSummary]:
        """The last list the manager gave (empty before the first one)."""
        return self._games

    @property
    def site_url(self) -> str | None:
        """Where the manager's web UI lives, as its administrator set it; None when not set or not known yet."""
        return self._site_url

    def set_site_url(self, site_url: str | None) -> None:
        self._site_url = site_url
        self._site_url_checked_at = self._now()

    async def get(self) -> list[GameSummary]:
        """The games; asks the manager once if nothing has been loaded yet (raises ManagerError when that fails)."""
        if not self._loaded:
            await self.refresh()
        return self._games

    async def refresh(self) -> None:
        games = parse_games(await self._client.list_games(self._token))
        self._games, self._loaded = games, True
        if self._site_url_checked_at is None or self._now() - self._site_url_checked_at >= SITE_URL_INTERVAL:
            await self._refresh_site_url()

    async def _refresh_site_url(self) -> None:
        try:
            status = await self._client.auth_status(self._token)
        except ManagerError as exc:
            log.warning("Could not read the manager's web address: %s", exc.detail)
            self._site_url_checked_at = self._now()  # try again at the next interval, not on every refresh
            return
        site_url = status.get("site_url") if isinstance(status, dict) else None
        self.set_site_url(site_url if isinstance(site_url, str) and site_url else None)

    async def run_refresh(self) -> None:
        """What the background loop calls: a manager that is down is no reason for a traceback every minute."""
        try:
            await self.refresh()
        except ManagerError as exc:
            log.warning("Could not refresh the list of games: %s", exc.detail)
