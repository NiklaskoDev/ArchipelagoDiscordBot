"""A file whose age tells the container's HEALTHCHECK that the bot is connected to Discord."""

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

HEARTBEAT_FILE = "heartbeat"
HEARTBEAT_INTERVAL = 30  # seconds; the HEALTHCHECK accepts a file up to 90 s old


class Heartbeat:
    def __init__(self, data_dir: Path, is_connected: Callable[[], bool], *, now: Callable[[], float] = time.time):
        self._path = data_dir / HEARTBEAT_FILE
        self._is_connected = is_connected
        self._now = now

    async def beat(self) -> None:
        """Writes the time while the gateway is connected; stays silent otherwise so the file goes stale."""
        if not self._is_connected():
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(self._path.name + ".tmp")
            temporary.write_text(str(int(self._now())), encoding="ascii")
            os.replace(temporary, self._path)
        except OSError as exc:
            log.error("Could not write the heartbeat file: %s", exc.strerror or type(exc).__name__)
