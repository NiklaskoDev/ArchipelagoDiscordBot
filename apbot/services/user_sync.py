"""What each linked person sees on the manager: their slots and the slots offered to them, looked at every two minutes
with their own token.

From that come three things: events (one of their games went online, a slot was offered to them), the list of games
they play in (it decides who has which game role), and the games and readers the activity feed needs for games the bot
account cannot see. The first look at a person only remembers; it never raises an event, so a restart is quiet.

For people who asked to hear about friend requests it also looks at their friend requests (one more call each). The
manager has no push for that, so polling is the only source.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apbot.access import Access
from apbot.db.links import LinkStore, UserLink
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError, NotFound, TokenInvalid
from apbot.manager.views import SlotRef, UserGame, parse_friends, parse_user_games

log = logging.getLogger(__name__)

SYNC_INTERVAL = 120  # seconds, see Periodic in bot.py
STAGGER = 0.25  # seconds between two people, so their requests do not arrive in one burst

GAME_ONLINE = "game_online"
SLOT_OFFERED = "slot_offered"
FRIEND_REQUEST = "friend_request"  # the alert kind a person turns on to hear about friend requests
FRIENDS_RETRY = 3600  # seconds to leave friends alone after the manager said it has none (an older manager)


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


@dataclass(frozen=True)
class FriendRequestEvent:
    """Someone asked the person to be their friend on the manager."""

    discord_user_id: int
    from_id: int  # the asker's manager user id
    from_name: str


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
        now: Callable[[], float] = time.monotonic,
    ):
        self._links = links
        self._access = access
        self._client = client
        self._sleep = sleep
        self._now = now
        self._unreadable_reported = False
        self._states: dict[int, dict[int, UserGame]] = {}
        self._incoming: dict[int, set[int]] = {}  # per person with the alert on: manager ids that have asked them
        self._friends_missing_until = 0.0  # no friend calls before this time (an older manager has no friends)
        self._on_events: Callable[[list[UserGameEvent]], Awaitable[None]] | None = None
        self._on_memberships: Callable[[int, frozenset[int]], Awaitable[None]] | None = None
        self._on_token_stopped: Callable[[UserLink], Awaitable[None]] | None = None
        self._on_friend_requests: Callable[[list[FriendRequestEvent]], Awaitable[None]] | None = None
        self._wants_friend_requests: Callable[[int], bool] | None = None

    def listen(
        self,
        *,
        on_events: Callable[[list[UserGameEvent]], Awaitable[None]] | None = None,
        on_memberships: Callable[[int, frozenset[int]], Awaitable[None]] | None = None,
        on_token_stopped: Callable[[UserLink], Awaitable[None]] | None = None,
        on_friend_requests: Callable[[list[FriendRequestEvent]], Awaitable[None]] | None = None,
        wants_friend_requests: Callable[[int], bool] | None = None,
    ) -> None:
        """Who hears about it: `on_events` gets what happened to one person, `on_memberships` the ids of the games a
        person plays in, `on_token_stopped` a link whose token the manager no longer accepts, `on_friend_requests` the
        new friend requests of one person. Friends are only looked at for people `wants_friend_requests` says yes to."""
        self._on_events, self._on_memberships, self._on_token_stopped = on_events, on_memberships, on_token_stopped
        self._on_friend_requests, self._wants_friend_requests = on_friend_requests, wants_friend_requests

    # --- what the others ask ---------------------------------------------------------------------------------------
    def slots_of(self, discord_id: int, game_id: int) -> tuple[SlotRef, ...]:
        game = self._states.get(discord_id, {}).get(game_id)
        return game.my_slots if game else ()

    def games_of(self, discord_id: int) -> list[UserGame]:
        """The games a person plays in, as the latest look saw them: those still going first, then the newest."""
        games = [game for game in self._states.get(discord_id, {}).values() if game.my_slots]
        return sorted(games, key=lambda game: (game.finished_at is not None, -game.id))

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
        self._incoming.pop(discord_id, None)

    async def token_refused(self, link: UserLink) -> None:
        """The manager refused a person's token (here or while reading a game's feed with it): the link is marked
        invalid, their game roles go and they are told once."""
        self._access.on_token_invalid(link.discord_user_id)
        await self._drop(link.discord_user_id)
        if self._on_token_stopped:
            await self._on_token_stopped(link)

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
        self._report_unreadable(links)

    def _report_unreadable(self, links: list[UserLink]) -> None:
        """One warning per start: the key can only change with a restart, so the first full pass shows everything."""
        if self._unreadable_reported:
            return
        self._unreadable_reported = True
        count = sum(1 for link in links if self._access.is_unreadable(link.discord_user_id))
        if count:
            log.warning(
                "%d linked people have a stored manager token the bot cannot decrypt; BOT_ENCRYPTION_KEY may be wrong. "
                "Their links are kept",
                count,
            )

    async def sync_user(self, link: UserLink) -> None:
        discord_id = link.discord_user_id
        token = self._access.stored_token(link)
        if token is None:
            if not self._access.is_unreadable(discord_id):  # an unreadable token says nothing about the link itself
                await self._drop(discord_id)
            return
        try:
            games = parse_user_games(await self._client.list_games(token))
        except TokenInvalid:
            await self.token_refused(link)
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
        if self._wants_friend_requests and self._wants_friend_requests(discord_id):
            await self._sync_friends(link, token)
        else:
            self._incoming.pop(discord_id, None)  # when they turn it on again the first look is quiet

    async def _sync_friends(self, link: UserLink, token: str) -> None:
        """Looks at the person's incoming friend requests; the ones not seen before are events. The first look only
        remembers, like the one at games."""
        discord_id = link.discord_user_id
        now = self._now()
        if self._friends_missing_until > now:
            return
        try:
            overview = parse_friends(await self._client.friends(token))
        except TokenInvalid:
            await self.token_refused(link)
            return
        except NotFound:
            self._friends_missing_until = now + FRIENDS_RETRY
            log.debug("The manager has no friends feature; not looking for friend requests for %d seconds", FRIENDS_RETRY)
            return
        except (ManagerError, ValueError) as exc:
            log.debug("Could not look at the friend requests of a linked person: %s", exc)
            return
        asking = {friend.id: friend for friend in overview.incoming}
        previous = self._incoming.get(discord_id)
        self._incoming[discord_id] = set(asking)
        if previous is None or not self._on_friend_requests:
            return
        events = [
            FriendRequestEvent(discord_id, friend.id, friend.username)
            for friend in asking.values()
            if friend.id not in previous
        ]
        if events:
            await self._on_friend_requests(events)


def _playing(games: dict[int, UserGame]) -> frozenset[int]:
    return frozenset(game.id for game in games.values() if game.my_slots and game.finished_at is None)
