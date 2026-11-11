"""The manager's REST API. Every call takes the token to use (the bot's or a user's), so one client serves all of them.

The token only ever travels in the Authorization header: it is not part of a URL, a log line or an exception.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
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
CONTROL_TIMEOUT = httpx.Timeout(60, connect=5)  # stopping a game waits for its server to save and exit (up to ~25 s)
TRACKER_TIMEOUT = httpx.Timeout(30, connect=5)  # the manager reads the game's save to answer
QUICK_TIMEOUT = httpx.Timeout(2, connect=1)  # for suggestions: Discord waits 3 seconds for an autocomplete
ACTION_TIMEOUT = httpx.Timeout(30, connect=5)  # release, collect and hint changes wait up to 20 s for the game's server
CHECK_TIMEOUT = httpx.Timeout(200, connect=5)  # Archipelago reads the whole config (the manager gives it 180 s)
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

    async def _send(
        self, method: str, path: str, token: str | None, *, timeout: httpx.Timeout | None = None, body: Any = None
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = await self._http.request(
                method,
                path,
                headers=headers,
                json=body,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            # str(exc) can carry the request URL; the class name is enough to tell timeouts from refused connections.
            raise ManagerUnavailable(None, f"The manager could not be reached ({type(exc).__name__})") from None
        if response.is_error:
            raise error_for(response)
        try:
            return response.json()
        except ValueError:
            raise ManagerUnavailable(response.status_code, "The manager sent an answer that is not JSON") from None

    async def _get(
        self, path: str, token: str | None, *, timeout: httpx.Timeout | None = None, retry: bool = True
    ) -> Any:
        """A read: retried when the manager is busy or restarting (unless `retry` is off). Changes are never retried
        (they might repeat)."""
        if not retry:
            return await self._send("GET", path, token, timeout=timeout)
        for delay in RETRY_DELAYS:
            try:
                return await self._send("GET", path, token, timeout=timeout)
            except ManagerUnavailable as exc:
                log.debug("GET %s failed (%s), retrying in %ss", path, exc.status or "no answer", delay)
                await self._sleep(delay)
        return await self._send("GET", path, token, timeout=timeout)

    async def _post(self, path: str, token: str, *, timeout: httpx.Timeout | None = None, body: Any = None) -> Any:
        """A change. Never retried: the first try may have worked even when no answer came back."""
        return await self._send("POST", path, token, timeout=timeout, body=body)

    async def _put(self, path: str, token: str, body: Any, *, timeout: httpx.Timeout | None = None) -> Any:
        return await self._send("PUT", path, token, timeout=timeout, body=body)

    async def health(self) -> dict:
        return await self._get("/api/health", None)

    async def auth_status(self, token: str) -> dict:
        return await self._get("/api/auth/status", token)

    async def list_games(self, token: str, *, quick: bool = False) -> list[dict]:
        return await self._get("/api/games", token, timeout=QUICK_TIMEOUT if quick else None, retry=not quick)

    async def game(self, token: str, game_id: int) -> dict:
        return await self._get(f"/api/games/{int(game_id)}", token)

    async def list_signups(self, token: str, *, quick: bool = False) -> list[dict]:
        return await self._get("/api/signups", token, timeout=QUICK_TIMEOUT if quick else None, retry=not quick)

    async def wake(self, token: str, game_id: int) -> dict:
        return await self._post(f"/api/games/{int(game_id)}/wake", token, timeout=CONTROL_TIMEOUT)

    async def start(self, token: str, game_id: int) -> dict:
        return await self._post(f"/api/games/{int(game_id)}/start", token, timeout=CONTROL_TIMEOUT)

    async def stop(self, token: str, game_id: int) -> dict:
        return await self._post(f"/api/games/{int(game_id)}/stop", token, timeout=CONTROL_TIMEOUT)

    async def restart(self, token: str, game_id: int) -> dict:
        return await self._post(f"/api/games/{int(game_id)}/restart", token, timeout=CONTROL_TIMEOUT)

    async def archive_game(self, token: str, game_id: int, archived: bool) -> dict:
        """Moves a game to the archive (it stops its server) or back out of it."""
        return await self._post(
            f"/api/games/{int(game_id)}/archive", token, timeout=CONTROL_TIMEOUT, body={"archived": bool(archived)}
        )

    async def delete_game(self, token: str, game_id: int) -> dict:
        """Deletes the game with its files, save and history. Not retried, like every change."""
        return await self._send("DELETE", f"/api/games/{int(game_id)}", token, timeout=CONTROL_TIMEOUT)

    async def activity(self, token: str, game_id: int, after: int) -> dict:
        """The activity feed of a game: `{"entries": [...], "last": int}`, the entries numbered after `after`."""
        return await self._get(f"/api/games/{int(game_id)}/activity?after={max(int(after), 0)}", token)

    async def tracker(self, token: str, game_id: int, slot: int) -> dict:
        """One slot's items, locations and hints. Only its player (or the game's organizer) may ask."""
        path = f"/api/games/{int(game_id)}/slots/{int(slot)}/tracker"
        return await self._get(path, token, timeout=TRACKER_TIMEOUT)

    async def accept_handover(self, token: str, game_id: int, slot: int) -> dict:
        return await self._post(f"/api/games/{int(game_id)}/slots/{int(slot)}/handover/accept", token)

    async def cancel_handover(self, token: str, game_id: int, slot: int) -> dict:
        """The player takes their offer back, or the person it was offered to declines. Not retried, like every change."""
        return await self._send("DELETE", f"/api/games/{int(game_id)}/slots/{int(slot)}/handover", token)

    # --- the player's tools: text client, hints, slot status, claiming and handing over -----------------------------
    async def client_poll(self, token: str, game_id: int, slot: int, after: int, *, quick: bool = False) -> dict:
        """Opens the player's text client connection (or keeps it open) and returns the messages after `after`:
        `{state, error, last, messages, hint_points, hint_cost}`. A change as far as the manager is concerned (it
        starts a connection), so not retried. `quick` gives up after two seconds, for suggestions."""
        path = f"/api/games/{int(game_id)}/slots/{int(slot)}/client"
        return await self._post(path, token, body={"after": max(int(after), 0)}, timeout=QUICK_TIMEOUT if quick else None)

    async def client_say(self, token: str, game_id: int, slot: int, text: str) -> dict:
        path = f"/api/games/{int(game_id)}/slots/{int(slot)}/client/say"
        return await self._post(path, token, body={"text": text})

    async def client_names(self, token: str, game_id: int, slot: int, *, quick: bool = False) -> dict:
        """The item and location names of the slot's world: `{items, locations}`, both empty until a text client
        connection of this player has loaded them. `quick` gives up after two seconds without retrying."""
        path = f"/api/games/{int(game_id)}/slots/{int(slot)}/client/names"
        return await self._get(path, token, timeout=QUICK_TIMEOUT if quick else None, retry=not quick)

    async def slot_action(self, token: str, game_id: int, slot: int, action: str) -> dict:
        """`release` or `collect` for the player: `{done, message}`, the server's answer."""
        path = f"/api/games/{int(game_id)}/slots/{int(slot)}/action"
        return await self._post(path, token, timeout=ACTION_TIMEOUT, body={"action": action})

    async def set_hint_status(
        self, token: str, game_id: int, slot: int, finder: int, location: int, status: int
    ) -> dict:
        """Marks a hint for one of the player's items (10 no priority, 20 avoid, 30 priority): `{status}`."""
        path = f"/api/games/{int(game_id)}/slots/{int(slot)}/hints/{int(finder)}/{int(location)}"
        return await self._put(path, token, {"status": int(status)}, timeout=ACTION_TIMEOUT)

    async def set_slot_status(self, token: str, game_id: int, slot: int, status: str | None, note: str) -> dict:
        """What the player says about how they are doing (playing, stuck, away; None clears it)."""
        path = f"/api/games/{int(game_id)}/slots/{int(slot)}/status"
        return await self._put(path, token, {"status": status, "note": note})

    async def claim(self, token: str, game_id: int, slot: int) -> dict:
        return await self._post(f"/api/games/{int(game_id)}/slots/{int(slot)}/claim", token)

    async def offer_handover(self, token: str, game_id: int, slot: int, to: str | None, note: str) -> dict:
        """Offers a slot to the user named `to`, or to whoever takes it when `to` is None."""
        path = f"/api/games/{int(game_id)}/slots/{int(slot)}/handover"
        return await self._put(path, token, {"to": to, "note": note})

    # --- generation -------------------------------------------------------------------------------------------------
    async def start_job(
        self,
        token: str,
        config_ids: Sequence[int],
        *,
        name: str | None,
        spoiler: int,
        race: bool,
        seed: int | None,
        host_after: bool,
    ) -> dict:
        """Queues the generation of a game from saved configs (the person's own, and ones friends share with them)."""
        body = {
            "config_ids": [int(config_id) for config_id in config_ids],
            "name": name,
            "spoiler": int(spoiler),
            "race": bool(race),
            "seed": None if seed is None else int(seed),
            "host_after": bool(host_after),
        }
        return await self._post("/api/jobs", token, body=body)

    async def job(self, token: str, job_id: int, *, quick: bool = False) -> dict:
        path = f"/api/jobs/{int(job_id)}"
        return await self._get(path, token, timeout=QUICK_TIMEOUT if quick else None, retry=not quick)

    async def cancel_job(self, token: str, job_id: int) -> dict:
        """Cancels a queued or running job; the manager answers 409 when it is over already."""
        return await self._post(f"/api/jobs/{int(job_id)}/cancel", token)

    # --- sign-ups and configs ---------------------------------------------------------------------------------------
    async def signup(self, token: str, signup_id: int, *, quick: bool = False) -> dict:
        """One round with its entries (the asker's own, and all of them for the round's organizer) and, for the
        organizer, the latest test."""
        path = f"/api/signups/{int(signup_id)}"
        return await self._get(path, token, timeout=QUICK_TIMEOUT if quick else None, retry=not quick)

    async def create_signup(
        self, token: str, name: str, description: str | None, deadline: float | None, slots_per_user: int
    ) -> dict:
        """Opens a round (organizers and administrators only)."""
        body = {"name": name, "description": description, "deadline": deadline, "slots_per_user": int(slots_per_user)}
        return await self._post("/api/signups", token, body=body)

    async def close_signup(self, token: str, signup_id: int) -> dict:
        return await self._post(f"/api/signups/{int(signup_id)}/close", token)

    async def test_signup(self, token: str, signup_id: int) -> dict:
        """Starts a test generation of the round's entries; the result shows in `signup()` once it is done."""
        return await self._post(f"/api/signups/{int(signup_id)}/test", token)

    async def add_entry(self, token: str, signup_id: int, config_id: int) -> dict:
        """Enters one of the person's saved configs: the round, and the manager's `warnings` about the config."""
        return await self._post(f"/api/signups/{int(signup_id)}/entries", token, body={"config_id": int(config_id)})

    async def remove_entry(self, token: str, signup_id: int, entry_id: int) -> dict:
        return await self._send("DELETE", f"/api/signups/{int(signup_id)}/entries/{int(entry_id)}", token)

    async def list_configs(self, token: str, *, quick: bool = False) -> list[dict]:
        """The person's saved configs."""
        return await self._get("/api/configs", token, timeout=QUICK_TIMEOUT if quick else None, retry=not quick)

    async def create_config(self, token: str, name: str, content: str) -> dict:
        """Saves a config text in the person's library; the manager gives it a free name."""
        return await self._post("/api/configs/text", token, body={"name": name, "content": content})

    async def check_config(self, token: str, content: str) -> dict:
        """Whether the generator takes a config, without generating (slow: Archipelago reads it; one check at a time
        per person, else 429). Nothing is saved."""
        return await self._post("/api/configs/check", token, timeout=CHECK_TIMEOUT, body={"content": content})
