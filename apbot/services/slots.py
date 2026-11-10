"""Finding the slot a command is about, and the slots a person could claim.

Both ask the manager with the person's own token: what they play and what they may claim is theirs to see, never read
from the bot account's view.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from apbot.errors import UserFacingError
from apbot.manager.client import ManagerClient
from apbot.manager.views import parse_game_detail, parse_user_game
from apbot.services.ttl_cache import TtlCache

CLAIMABLE_TTL = 60.0  # seconds a person's list of claimable slots is reused (autocomplete asks on every keystroke)
MAX_CACHED = 500
MAX_SLOT_DIGITS = 9  # ids come from the autocomplete; this only rejects typed nonsense early


@dataclass(frozen=True)
class ClaimableSlot:
    slot: int
    name: str
    game: str  # the world, such as "Timespinner"


def number_or_none(value: str) -> int | None:
    """The number in an option's text (autocomplete values are numbers); None for anything else, long numbers included."""
    text = value.strip()
    if not text.isascii() or not text.isdecimal() or len(text) > MAX_SLOT_DIGITS:
        return None
    return int(text)


def parse_slot_id(value: str) -> int:
    number = number_or_none(value)
    if number is None:
        raise UserFacingError("Pick a slot from the list")
    return number


class SlotDirectory:
    def __init__(self, client: ManagerClient, *, now: Callable[[], float] = time.monotonic):
        self._client = client
        self._claimable: TtlCache[tuple[ClaimableSlot, ...]] = TtlCache(CLAIMABLE_TTL, MAX_CACHED, now=now)

    async def resolve(self, token: str, game_id: int, slot: int | None) -> int:
        """The number of the slot a command is about. Given: as it is (the manager refuses a slot that is not theirs).
        Omitted: the person's only slot in the game, asked from the manager so a slot claimed a minute ago counts."""
        if slot is not None:
            return slot
        mine = parse_user_game(await self._client.game(token, game_id)).my_slots
        if not mine:
            raise UserFacingError("You have no slot in this game")
        if len(mine) > 1:
            raise UserFacingError("You have more than one slot in this game. Pick one.")
        return mine[0].slot

    async def claimable(self, owner: int, token: str, game_id: int) -> tuple[ClaimableSlot, ...]:
        """The slots of a game that the person may claim now. `owner` (a Discord id) keys the cache."""
        key = (owner, game_id)
        cached = self._claimable.get(key)
        if cached is not None:
            return cached
        detail = parse_game_detail(await self._client.game(token, game_id))
        slots = tuple(ClaimableSlot(s.slot, s.name, s.game) for s in detail.slots if s.claimable and s.slot >= 0)
        self._claimable.set(key, slots)
        return slots

    def forget(self, owner: int, game_id: int) -> None:
        """What was claimable changed (the person just claimed a slot)."""
        self._claimable.discard((owner, game_id))
