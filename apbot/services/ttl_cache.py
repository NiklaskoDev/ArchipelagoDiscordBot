"""A small in-memory cache whose entries expire, for answers that are asked for on every keystroke."""

import time
from collections.abc import Callable, Hashable


class TtlCache[V]:
    def __init__(self, ttl: float, max_size: int, *, now: Callable[[], float] = time.monotonic):
        self._ttl = ttl
        self._max_size = max_size
        self._now = now
        self._entries: dict[Hashable, tuple[float, V]] = {}

    def get(self, key: Hashable) -> V | None:
        entry = self._entries.get(key)
        if entry is None or self._now() - entry[0] >= self._ttl:
            return None
        return entry[1]

    def set(self, key: Hashable, value: V) -> None:
        now = self._now()
        self._entries = {k: v for k, v in self._entries.items() if now - v[0] < self._ttl and k != key}
        while len(self._entries) >= self._max_size:  # full of fresh entries: the oldest goes
            del self._entries[min(self._entries, key=lambda k: self._entries[k][0])]
        self._entries[key] = (now, value)

    def discard(self, key: Hashable) -> None:
        self._entries.pop(key, None)
