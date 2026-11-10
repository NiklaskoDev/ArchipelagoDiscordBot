"""The manager's REST API. Every call takes the token to use (the bot's or a user's), so one client serves all of them.

The token only ever travels in the Authorization header: it is not part of a URL, a log line or an exception.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from apbot.manager.errors import (
    READ_ONLY_DETAIL,
    BadRequest,
    Conflict,
    Forbidden,
    ManagerError,
    ManagerUnavailable,
    NotFound,
    ReadOnlyToken,
    TokenInvalid,
    TooManyRequests,
)

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10, connect=5)
RETRY_DELAYS = (0.5, 1.0, 2.0)  # seconds before each retry of a read
MAX_DETAIL_LENGTH = 300
UNAVAILABLE_STATUSES = frozenset({502, 503, 504})


def error_detail(response: httpx.Response) -> str:
    """The manager's explanation ({"detail": str}, or a list of messages for 422), else the bare status."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):
        detail = "; ".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
    if not isinstance(detail, str) or not detail:
        return f"HTTP {response.status_code}"
    return detail[:MAX_DETAIL_LENGTH]


def error_for(response: httpx.Response) -> ManagerError:
    status, detail = response.status_code, error_detail(response)
    if status == 401:
        return TokenInvalid(status, detail)
    if status == 403:
        return ReadOnlyToken(status, detail) if detail == READ_ONLY_DETAIL else Forbidden(status, detail)
    if status == 404:
        return NotFound(status, detail)
    if status == 409:
        return Conflict(status, detail)
    if status == 429:
        return TooManyRequests(status, detail)
    if status in (400, 413, 422):
        return BadRequest(status, detail)
    if status in UNAVAILABLE_STATUSES or status >= 500:
        return ManagerUnavailable(status, detail)
    return ManagerError(status, detail)


class ManagerClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT, transport=transport)
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, token: str | None) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = await self._http.request(method, path, headers=headers)
        except httpx.HTTPError as exc:
            # str(exc) can carry the request URL; the class name is enough to tell timeouts from refused connections.
            raise ManagerUnavailable(None, f"The manager could not be reached ({type(exc).__name__})") from None
        if response.is_error:
            raise error_for(response)
        try:
            return response.json()
        except ValueError:
            raise ManagerUnavailable(response.status_code, "The manager sent an answer that is not JSON") from None

    async def _get(self, path: str, token: str | None) -> Any:
        """A read: retried when the manager is busy or restarting. Changes are never retried (they might repeat)."""
        for delay in RETRY_DELAYS:
            try:
                return await self._send("GET", path, token)
            except ManagerUnavailable as exc:
                log.debug("GET %s failed (%s), retrying in %ss", path, exc.status or "no answer", delay)
                await self._sleep(delay)
        return await self._send("GET", path, token)

    async def health(self) -> dict:
        return await self._get("/api/health", None)

    async def auth_status(self, token: str) -> dict:
        return await self._get("/api/auth/status", token)

    async def list_games(self, token: str) -> list[dict]:
        return await self._get("/api/games", token)

    async def game(self, token: str, game_id: int) -> dict:
        return await self._get(f"/api/games/{int(game_id)}", token)

    async def list_signups(self, token: str) -> list[dict]:
        return await self._get("/api/signups", token)
