"""Talking to a game's server as a player, through the text client the manager keeps open for them.

The manager holds one connection per (game, slot, person) and closes it after 90 seconds without a poll. A command
opens it, waits until it is connected, says something and listens for the server's answer for a few seconds. Only
messages that concern the player are answers: the server also sends everyone else's chat and items to every client.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from apbot.errors import UserFacingError
from apbot.manager.client import ManagerClient
from apbot.services.ttl_cache import TtlCache
from apbot.ui.format import safe_text

CONNECT_TIMEOUT = 20.0  # seconds to wait for the connection to the game's server
POLL_INTERVAL = 1.0
ANSWER_TIMEOUT = 8.0  # seconds to wait for the first line of an answer
QUIET_PERIOD = 1.5  # after the first line, how long more lines are still collected
MAX_LINES = 15
LINE_LIMIT = 240  # 15 lines of this size stay below the 4096 characters of an embed description
MAX_TEXT_LENGTH = 1000  # the manager's limit for one message
NAMES_TTL = 600.0
MAX_CACHED_NAMES = 200
# What the server sends back to a command: the command's result, and the hint a !hint or !hint_location gives. Other
# messages that concern the player (items sent, joins, chat) arrive at any time and are not answers.
ANSWER_TYPES = frozenset({"CommandResult", "Hint"})
NAMES_BUDGET = 2.5  # seconds for suggestions in all; Discord waits 3
NAMES_LOADING_PAUSE = 5.0  # seconds a "still loading" answer is reused instead of asking the manager on every keystroke


@dataclass(frozen=True)
class TextAnswer:
    lines: tuple[str, ...]  # escaped, at most MAX_LINES
    hidden: int  # lines of the answer that were left out


@dataclass(frozen=True)
class Names:
    items: tuple[str, ...]
    locations: tuple[str, ...]


def checked_text(text: str) -> str:
    """The text to say, or UserFacingError: the manager would refuse these too, this answers without a round trip."""
    text = text.strip()
    if not text:
        raise UserFacingError("Type a message or a command")
    if len(text) > MAX_TEXT_LENGTH:
        raise UserFacingError("A message can be at most {limit} characters long", limit=MAX_TEXT_LENGTH)
    if text.startswith("/"):
        raise UserFacingError(
            "Commands that start with / belong to Archipelago's own clients. The server's commands start with !, "
            "for example !hint or !help."
        )
    if not text.isprintable():
        raise UserFacingError("A message can only be plain text on one line")
    return text


def _message_lines(message: Any) -> list[str]:
    parts = message.get("parts") if isinstance(message, Mapping) else None
    if not isinstance(parts, list):
        return []
    text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, Mapping))
    return [line for line in (safe_text(raw, LINE_LIMIT) for raw in text.splitlines()) if line]


def answer_lines(messages: Any) -> list[str]:
    """The lines of the messages that answer this player's command (see ANSWER_TYPES)."""
    lines: list[str] = []
    for message in messages if isinstance(messages, list) else []:
        if isinstance(message, Mapping) and message.get("mine") and message.get("type") in ANSWER_TYPES:
            lines += _message_lines(message)
    return lines


class TextClientBridge:
    def __init__(
        self,
        client: ManagerClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._sleep = sleep
        self._now = now
        self._names: TtlCache[Names] = TtlCache(NAMES_TTL, MAX_CACHED_NAMES, now=now)
        self._loading: TtlCache[bool] = TtlCache(NAMES_LOADING_PAUSE, MAX_CACHED_NAMES, now=now)

    async def connect(self, token: str, game_id: int, slot: int) -> int:
        """Opens the player's connection and waits until it is connected. Returns the number of the newest message, so
        what the server says next can be told from what came before."""
        deadline = self._now() + CONNECT_TIMEOUT
        while True:
            page = await self._client.client_poll(token, game_id, slot, 0)
            state = page.get("state")
            if state == "connected":
                last = page.get("last")
                return last if isinstance(last, int) else 0
            if state == "offline":
                raise UserFacingError("The game's server is not running")
            if state == "refused":
                error = page.get("error")
                detail = safe_text(error, LINE_LIMIT) if isinstance(error, str) and error else "?"
                raise UserFacingError("The game's server refused the connection: {error}", error=detail)
            if self._now() >= deadline:
                raise UserFacingError("The game's server did not answer in time. Try again in a moment.")
            await self._sleep(POLL_INTERVAL)

    async def say(self, token: str, game_id: int, slot: int, text: str) -> None:
        """Says `text` in the player's name: chat, or a server command when it starts with !."""
        text = checked_text(text)
        await self.connect(token, game_id, slot)
        await self._client.client_say(token, game_id, slot, text)

    async def run(self, token: str, game_id: int, slot: int, text: str) -> TextAnswer:
        """Says `text` and collects the server's answer."""
        text = checked_text(text)
        after = await self.connect(token, game_id, slot)
        await self._client.client_say(token, game_id, slot, text)
        lines = await self._collect(token, game_id, slot, after)
        return TextAnswer(tuple(lines[:MAX_LINES]), max(len(lines) - MAX_LINES, 0))

    async def _collect(self, token: str, game_id: int, slot: int, after: int) -> list[str]:
        """Polls for the answer: up to ANSWER_TIMEOUT for the first line, then once more after the quiet period."""
        deadline = self._now() + ANSWER_TIMEOUT
        lines: list[str] = []
        while True:
            await self._sleep(QUIET_PERIOD if lines else POLL_INTERVAL)
            page = await self._client.client_poll(token, game_id, slot, after)
            if isinstance(page.get("last"), int):
                after = max(after, page["last"])
            new = answer_lines(page.get("messages"))
            if lines:  # the quiet period is over: what came in meanwhile is part of the answer, nothing is awaited
                return lines + new
            lines = new
            if not lines and self._now() >= deadline:
                return lines

    async def names(self, owner: int, token: str, game_id: int, slot: int) -> Names | None:
        """The slot's item and location names, for suggestions; None until the manager has them. The first call opens
        the connection that loads them. `owner` (a Discord id) keys the cache, so no token is held in memory."""
        key = (owner, game_id, slot)
        cached = self._names.get(key)
        if cached is not None:
            return cached
        if self._loading.get(key):
            return None
        names = None
        try:
            async with asyncio.timeout(NAMES_BUDGET):
                await self._client.client_poll(token, game_id, slot, 0, quick=True)
                raw = await self._client.client_names(token, game_id, slot, quick=True)
            names = Names(_strings(raw.get("items")), _strings(raw.get("locations")))
        except TimeoutError:
            pass  # too slow for a suggestion: counts as still loading
        if names is None or not (names.items or names.locations):
            self._loading.set(key, True)
            return None
        self._names.set(key, names)
        return names


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str) and item) if isinstance(value, list) else ()
