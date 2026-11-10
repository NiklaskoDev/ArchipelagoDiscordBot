"""Checks of the manager and of the bot's account on it, at startup and from `/setup manager`."""

import logging
import re
from dataclasses import dataclass

from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError, ManagerUnavailable, TokenInvalid
from apbot.services.game_cache import GameCache

log = logging.getLogger(__name__)

MIN_MANAGER_VERSION = "1.3.0"
EXIT_BAD_MANAGER_TOKEN = 2
RETRY_INTERVAL = 30  # seconds between checks while the manager is unreachable
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class StartupFailure(Exception):
    """The bot cannot do its job; the process ends with `exit_code`."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class ManagerReport:
    version: str | None
    ready: bool
    username: str | None  # the bot account; None when the manager does not accept the token
    role: str | None
    site_url: str | None


def parse_version(text: object) -> tuple[int, int, int] | None:
    match = _VERSION.match(text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def version_at_least(version: object, minimum: str) -> bool:
    parsed = parse_version(version)
    return parsed is not None and parsed >= parse_version(minimum)


async def check_manager(client: ManagerClient, bot_token: str) -> ManagerReport:
    """Asks the manager about itself and about the bot's token. Raises ManagerUnavailable when it cannot be reached."""
    health = await client.health()
    if not isinstance(health, dict):
        raise ManagerUnavailable(None, "The manager's health check did not answer as expected")
    try:
        status = await client.auth_status(bot_token)
    except TokenInvalid:
        status = {}
    user = status.get("user") if isinstance(status, dict) else None
    user = user if isinstance(user, dict) else {}
    site_url = status.get("site_url") if isinstance(status, dict) else None
    return ManagerReport(
        version=health.get("version") if isinstance(health.get("version"), str) else None,
        ready=bool(health.get("ready")),
        username=user.get("username") if isinstance(user.get("username"), str) else None,
        role=user.get("role") if isinstance(user.get("role"), str) else None,
        site_url=site_url if isinstance(site_url, str) and site_url else None,
    )


class ManagerWatch:
    """The startup checks. The bot goes on without a manager and calls `check_once` again until it answers; a token
    the manager refuses is the one thing that stops it."""

    def __init__(self, client: ManagerClient, bot_token: str, cache: GameCache):
        self._client = client
        self._token = bot_token
        self._cache = cache

    async def check_once(self) -> bool:
        """True when the manager answered and the bot's account is fine; False when it could not be reached or answered
        something unusable.
        Raises StartupFailure when the manager does not accept MANAGER_API_TOKEN."""
        try:
            report = await check_manager(self._client, self._token)
        except ManagerError as exc:
            # Not only a dead connection: a wrong MANAGER_URL (404), a proxy in the way (403) or a busy manager (429)
            # look different but are no reason to crash; the settings may be fixed or the manager may come back.
            log.error("The manager cannot be used yet (%s). Trying again in %s s.", exc.detail, RETRY_INTERVAL)
            return False
        if not version_at_least(report.version, MIN_MANAGER_VERSION):
            log.warning(
                "The manager reports version %s; the bot is written for %s or newer and may not work as expected",
                report.version or "unknown",
                MIN_MANAGER_VERSION,
            )
        if report.username is None:
            raise StartupFailure("MANAGER_API_TOKEN is not accepted by the manager", EXIT_BAD_MANAGER_TOKEN)
        if report.role != "user":
            log.warning("The bot account should be a plain user so public views never include private games")
        self._cache.set_site_url(report.site_url)
        return True
