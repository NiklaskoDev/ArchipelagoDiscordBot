"""Runs a coroutine over and over in the background."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


class StopPeriodic(Exception):
    """Raised by the coroutine of a Periodic when it has nothing left to do."""


class Periodic:
    """Calls `action` now and then every `interval` seconds. A failing call is logged and the loop carries on."""

    def __init__(
        self,
        name: str,
        interval: float,
        action: Callable[[], Awaitable[None]],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.name = name
        self._interval = interval
        self._action = action
        self._sleep = sleep
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name=self.name)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._action()
            except StopPeriodic:
                return
            except Exception:
                log.exception("Background task %s failed; it runs again in %s s", self.name, self._interval)
            await self._sleep(self._interval)
