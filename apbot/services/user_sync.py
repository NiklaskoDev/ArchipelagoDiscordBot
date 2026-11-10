"""What each linked person sees on the manager: their slots and the slots offered to them, looked at every two minutes
with their own token.

From that come three things: events (one of their games went online, a slot was offered to them), the list of games
they play in (it decides who has which game role), and the games and readers the activity feed needs for games the bot
account cannot see. The first look at a person only remembers; it never raises an event, so a restart is quiet.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apbot.access import Access
from apbot.db.links import LinkStore, UserLink
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError, TokenInvalid
from apbot.manager.views import SlotRef, UserGame, parse_user_games

log = logging.getLogger(__name__)

SYNC_INTERVAL = 120  # seconds, see Periodic in bot.py
STAGGER = 0.25  # seconds between two people, so their requests do not arrive in one burst

GAME_ONLINE = "game_online"
SLOT_OFFERED = "slot_offered"


@dataclass(frozen=True)
class UserGameEvent:
    kind: str  # game_online | slot_offered
    discord_user_id: int
    game_id: int
    game_name: str
    slot: int | None = None  # slot_offered: the slot and who offers it
    slot_name: str | None = None
    world: str | None = None
    offered_by: str | None = None
    note: str | None = None


def events_between(discord_id: int, before: dict[int, UserGame], after: dict[int, UserGame]) -> list[UserGameEvent]:
    """What happened to a person between two looks. Pure."""
    events: list[UserGameEvent] = []
    offered_before = {(game.id, offer.slot) for game in before.values() for offer in game.offers}
    for game in after.values():
        earlier = before.get(game.id)
        if earlier is not None and earlier.state != "running" and game.state == "running" and game.my_slots:
            events.append(UserGameEvent(GAME_ONLINE, discord_id, game.id, game.name))
        for offer in game.offers:
            if offer.to_me and (game.id, offer.slot) not in offered_before:
                events.append(
                    UserGameEvent(
                        SLOT_OFFERED, discord_id, game.id, game.name, offer.slot, offer.name, offer.game, offer.owner,
                        offer.note,
                    )
                )
    return events


class UserSync:
    def __init__(
        self,
        links: LinkStore,
        access: Access,
        client: ManagerClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._links = links
        self._access = access
        self._client = client
        self._sleep = sleep
        self._states: dict[int, dict[int, UserGame]] = {}
        self._on_events: Callable[[list[UserGameEvent]], Awaitable[None]] | None = None
        self._on_memberships: Callable[[int, frozenset[int]], Awaitable[None]] | None = None
        self._on_token_stopped: Callable[[UserLink], Awaitable[None]] | None = None

    def listen(
        self,
        *,
        on_events: Callable[[list[UserGameEvent]], Awaitable[None]] | None = None,
        on_memberships: Callable[[int, frozenset[int]], Awaitable[None]] | None = None,
        on_token_stopped: Callable[[UserLink], Awaitable[None]] | None = None,
    ) -> None:
        """Who hears about it: `on_events` gets what happened to one person, `on_memberships` the ids of the games a
        person plays in, `on_token_stopped` a link whose token the manager no longer accepts."""
        self._on_events, self._on_memberships, self._on_token_stopped = on_events, on_memberships, on_token_stopped

    # --- what the others ask ---------------------------------------------------------------------------------------
    def slots_of(self, discord_id: int, game_id: int) -> tuple[SlotRef, ...]:
        game = self._states.get(discord_id, {}).get(game_id)
        return game.my_slots if game else ()

    def participants(self, game_id: int) -> dict[int, tuple[SlotRef, ...]]:
        """The linked people who play in a game, with their slots."""
        found = {}
        for discord_id, games in self._states.items():
            game = games.get(game_id)
            if game is not None and game.my_slots:
                found[discord_id] = game.my_slots
        return found

    def memberships(self) -> dict[int, frozenset[int]]:
        """For each linked person, the games they play in that are not finished."""
        return {discord_id: _playing(games) for discord_id, games in self._states.items()}

    def known_games(self) -> dict[int, UserGame]:
        """Every game any linked person plays in, as the latest look saw it (the bot account may not see these)."""
        found: dict[int, UserGame] = {}
        for games in self._states.values():
            for game in games.values():
                if game.my_slots:
                    found.setdefault(game.id, game)
        return found

    def forget(self, discord_id: int) -> None:
        self._states.pop(discord_id, None)

    async def _drop(self, discord_id: int) -> None:
        """Forgets a person whose link no longer works, and tells the listener they play in nothing now (their game
        roles go), if they were known."""
        known = discord_id in self._states
        self.forget(discord_id)
        if known and self._on_memberships:
            await self._on_memberships(discord_id, frozenset())

    # --- looking ---------------------------------------------------------------------------------------------------
    async def run_once(self) -> None:
        links = self._links.all_active()
        for stale in set(self._states) - {link.discord_user_id for link in links}:
            await self._drop(stale)
        for link in links:
            try:
                await self.sync_user(link)
            except Exception:
                log.exception("Looking at one linked person's games failed; the others carry on")
            await self._sleep(STAGGER)

    async def sync_user(self, link: UserLink) -> None:
        discord_id = link.discord_user_id
        token = self._access.stored_token(link)
        if token is None:
            await self._drop(discord_id)
            return
        try:
            games = parse_user_games(await self._client.list_games(token))
        except TokenInvalid:
            self._access.on_token_invalid(discord_id)
            await self._drop(discord_id)
            if self._on_token_stopped:
                await self._on_token_stopped(link)
            return
        except ManagerError as exc:
            log.debug("Could not look at the games of a linked person: %s", exc.detail)
            return
        except TypeError:
            log.warning("The manager's list of games was not a list")
            return
        current = {game.id: game for game in games}
        previous = self._states.get(discord_id)
        self._states[discord_id] = current
        if previous is not None and self._on_events:
            events = events_between(discord_id, previous, current)
            if events:
                await self._on_events(events)
        if self._on_memberships:
            await self._on_memberships(discord_id, _playing(current))


def _playing(games: dict[int, UserGame]) -> frozenset[int]:
    return frozenset(game.id for game in games.values() if game.my_slots and game.finished_at is None)
