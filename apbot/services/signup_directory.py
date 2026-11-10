"""The rounds, saved configs and entries a person may pick from when they type a sign-up command.

Asked with the person's own token (what they may enter, withdraw or run is theirs to see) and kept for a minute, because
Discord asks for suggestions on every keystroke and waits 3 seconds. The calls give up after two seconds.
"""

import time
from collections.abc import Callable

from apbot.manager.client import ManagerClient
from apbot.manager.views import ConfigView, EntryView, SignupSummary, parse_configs, parse_signup_detail, parse_signups
from apbot.services.ttl_cache import TtlCache

SUGGESTION_TTL = 60.0  # seconds
MAX_CACHED = 500


class SignupDirectory:
    def __init__(self, client: ManagerClient, *, now: Callable[[], float] = time.monotonic):
        self._client = client
        self._rounds: TtlCache[tuple[SignupSummary, ...]] = TtlCache(SUGGESTION_TTL, MAX_CACHED, now=now)
        self._configs: TtlCache[tuple[ConfigView, ...]] = TtlCache(SUGGESTION_TTL, MAX_CACHED, now=now)
        self._entries: TtlCache[tuple[EntryView, ...]] = TtlCache(SUGGESTION_TTL, MAX_CACHED, now=now)

    async def rounds(self, owner: int, token: str) -> tuple[SignupSummary, ...]:
        """All rounds, with whether the person runs each. `owner` (a Discord id) keys the cache."""
        cached = self._rounds.get(owner)
        if cached is not None:
            return cached
        rounds = tuple(parse_signups(await self._client.list_signups(token, quick=True)))
        self._rounds.set(owner, rounds)
        return rounds

    async def configs(self, owner: int, token: str) -> tuple[ConfigView, ...]:
        cached = self._configs.get(owner)
        if cached is not None:
            return cached
        configs = tuple(parse_configs(await self._client.list_configs(token, quick=True)))
        self._configs.set(owner, configs)
        return configs

    async def my_entries(self, owner: int, token: str, signup_id: int) -> tuple[EntryView, ...]:
        """The person's own entries in a round."""
        key = (owner, signup_id)
        cached = self._entries.get(key)
        if cached is not None:
            return cached
        detail = parse_signup_detail(await self._client.signup(token, signup_id, quick=True))
        entries = tuple(entry for entry in detail.entries if entry.mine)
        self._entries.set(key, entries)
        return entries

    def forget_entries(self, owner: int, signup_id: int) -> None:
        """The person entered or withdrew something: the suggestions must show it at once."""
        self._entries.discard((owner, signup_id))

    def forget_rounds(self, owner: int) -> None:
        """A round was opened or closed."""
        self._rounds.discard(owner)

    def forget_configs(self, owner: int) -> None:
        """The person saved a new config."""
        self._configs.discard(owner)
