"""Direct messages for linked people who asked for them with `/alerts`.

What is worth a message is decided by plain functions (`rules_for`, `alert_for_event`) that look at one activity entry
or one event and the person's choices, and nothing else. `AlertEngine` gathers what they need (the person's slots, their
open hints, a tracker when someone is stuck), sends the messages and stops writing to people who closed their DMs.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import discord

from apbot.access import Access
from apbot.db.links import LinkStore, UserLink
from apbot.db.live import AlertPrefStore
from apbot.i18n import t, translatable
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError, TokenInvalid
from apbot.manager.views import FeedEntry, HintView, SlotRef, parse_hints
from apbot.services.feeds import FeedGame
from apbot.services.user_sync import FRIEND_REQUEST, GAME_ONLINE, SLOT_OFFERED, FriendRequestEvent, UserGameEvent, UserSync
from apbot.ui.dynamic import friend_request_view, handover_view
from apbot.ui.embeds import notice_embed
from apbot.ui.format import fit_lines, safe_text
from apbot.ui.feed import TEXT_LIMIT
from apbot.ui.style import ACCENT, DESCRIPTION, NAME_LIMIT, SEPARATOR

log = logging.getLogger(__name__)

ITEM_RECEIVED = "item_received"
HINT_FOUND = "hint_found"
STUCK_IN_MY_WORLD = "stuck_in_my_world"
FEED_ALERTS = frozenset({ITEM_RECEIVED, HINT_FOUND, STUCK_IN_MY_WORLD})  # the ones that come from a game's activity
MUTE_SECONDS = 24 * 60 * 60  # how long the bot leaves someone alone after Discord refused a direct message to them
MAX_STUCK_ITEMS = 8
TOKEN_STOPPED_TEXT = translatable("Your manager token stopped working. Make a new one and run /link again")

HintKey = tuple[str, str, str, str]  # receiver, item, finder, location: how an item entry names the hint it fulfils


@dataclass(frozen=True)
class Alert:
    kind: str
    game_id: int
    game_name: str
    key: str  # English text with {placeholders}, translated when it is sent
    values: Mapping[str, str] = field(default_factory=dict)  # already safe to show
    note: str | None = None
    slot: int | None = None  # set for an offer: the message carries the buttons that answer it

    def text(self, lang: str) -> str:
        line = t(self.key, lang, **self.values)
        return f"{line}{SEPARATOR}{self.note}" if self.note else line


@dataclass(frozen=True)
class AlertContext:
    """What the rules know about one person in one game."""

    enabled: frozenset[str]
    slot_names: frozenset[str]
    open_hints: frozenset[HintKey] = frozenset()  # hints for them that are not found yet
    items_for_stuck_player: tuple[HintView, ...] = ()  # items in their world that the player who got stuck waits for


def _name(text: str) -> str:
    return safe_text(text, NAME_LIMIT) or "?"


def hint_key(entry: FeedEntry) -> HintKey:
    return entry.get("receiver"), entry.get("item"), entry.get("finder"), entry.get("location")


def rules_for(entry: FeedEntry, game: FeedGame, context: AlertContext) -> list[Alert]:
    """The alerts one activity entry causes for one person. Pure."""
    alerts: list[Alert] = []
    if entry.kind == "item":
        receiver, finder = entry.get("receiver"), entry.get("finder")
        if ITEM_RECEIVED in context.enabled and receiver in context.slot_names and finder != receiver:
            values = {
                "finder": _name(finder), "item": _name(entry.get("item")), "receiver": _name(receiver),
                "location": _name(entry.get("location")),
            }
            alerts.append(
                Alert(
                    ITEM_RECEIVED, game.id, game.name,
                    translatable("**{finder}** sent {item} to **{receiver}** ({location})"), values,
                )
            )
        if HINT_FOUND in context.enabled and hint_key(entry) in context.open_hints:
            values = {
                "item": _name(entry.get("item")), "receiver": _name(receiver), "finder": _name(finder),
                "location": _name(entry.get("location")),
            }
            alerts.append(
                Alert(
                    HINT_FOUND, game.id, game.name,
                    translatable("Your hint was found: {item} for **{receiver}** at {location} in **{finder}**'s world"),
                    values,
                )
            )
    elif _is_stuck(entry) and STUCK_IN_MY_WORLD in context.enabled and entry.get("player") not in context.slot_names:
        waiting = context.items_for_stuck_player
        if waiting:
            alerts.append(_stuck_alert(entry.get("player"), game, waiting))
    return alerts


def _is_stuck(entry: FeedEntry) -> bool:
    return entry.kind == "status" and entry.get("status") == "stuck"


def _stuck_alert(player: str, game: FeedGame, waiting: tuple[HintView, ...]) -> Alert:
    shown = waiting[:MAX_STUCK_ITEMS]
    values = {
        "player": _name(player),
        "items": ", ".join(f"{_name(hint.item)} ({_name(hint.location)})" for hint in shown),
    }
    if len(waiting) == len(shown):
        key = translatable("**{player}** is stuck and waits for items in your world: {items}")
    else:
        key = translatable("**{player}** is stuck and waits for items in your world: {items}, and {count} more")
        values["count"] = str(len(waiting) - len(shown))
    return Alert(STUCK_IN_MY_WORLD, game.id, game.name, key, values)


def alert_for_event(event: UserGameEvent, enabled: frozenset[str]) -> Alert | None:
    """The alert an event of `UserSync` causes, when the person asked for it. Pure."""
    if event.kind not in enabled:
        return None
    if event.kind == GAME_ONLINE:
        return Alert(GAME_ONLINE, event.game_id, event.game_name, translatable("The game is online"))
    if event.kind == SLOT_OFFERED and event.slot is not None:
        values = {
            "owner": _name(event.offered_by or "?"), "slot": _name(event.slot_name or "?"),
            "world": _name(event.world or "?"),
        }
        note = safe_text(event.note, TEXT_LIMIT) if event.note else None
        return Alert(
            SLOT_OFFERED, event.game_id, event.game_name, translatable("**{owner}** offers you **{slot}** ({world})"),
            values, note, slot=event.slot,
        )
    return None


class AlertEngine:
    def __init__(
        self,
        prefs: AlertPrefStore,
        links: LinkStore,
        access: Access,
        user_sync: UserSync,
        client: ManagerClient,
        fetch_user: Callable[[int], Awaitable[discord.abc.Messageable]],
        *,
        now: Callable[[], float] = time.time,
    ):
        self._prefs = prefs
        self._links = links
        self._access = access
        self._user_sync = user_sync
        self._client = client
        self._fetch_user = fetch_user
        self._now = now
        self._muted_until: dict[int, float] = {}
        # (person, game) -> the slot names it was read for, and their hints that are not found yet
        self._hints: dict[tuple[int, int], tuple[frozenset[str], set[HintKey]]] = {}

    def forget(self, discord_id: int) -> None:
        """Drops what is remembered about a person's hints (they unlinked, or their token stopped working): it would
        be out of date by the time they are back."""
        for key in [key for key in self._hints if key[0] == discord_id]:
            del self._hints[key]

    # --- from the activity feed --------------------------------------------------------------------------------------
    async def handle(self, game: FeedGame, entries: tuple[FeedEntry, ...]) -> None:
        """What happened in a game, for each linked person who plays in it and asked to hear about it."""
        for discord_id, slots in self._user_sync.participants(game.id).items():
            enabled = self._prefs.enabled(discord_id)
            if HINT_FOUND not in enabled:
                self.forget(discord_id)  # the feed is not followed for them now, so the list would go stale
            link = self._links.get(discord_id)
            if link is None or not enabled & FEED_ALERTS:
                continue
            alerts = await self._alerts_for(link, game, slots, enabled, entries)
            await self._deliver(link, alerts)

    async def _alerts_for(
        self, link: UserLink, game: FeedGame, slots: tuple[SlotRef, ...], enabled: frozenset[str],
        entries: tuple[FeedEntry, ...],
    ) -> list[Alert]:
        names = frozenset(slot.name for slot in slots)
        open_hints = await self._open_hints(link, game, slots, entries) if HINT_FOUND in enabled else None
        alerts: list[Alert] = []
        for entry in entries:
            waiting = ()
            if _is_stuck(entry) and STUCK_IN_MY_WORLD in enabled and entry.get("player") not in names:
                waiting = await self._items_waiting_for(link, game, slots, entry.get("player"))
            context = AlertContext(enabled, names, frozenset(open_hints or ()), waiting)
            alerts.extend(rules_for(entry, game, context))
            if open_hints is not None:
                _keep_hints_current(open_hints, entry, names)
        return alerts

    async def _open_hints(
        self, link: UserLink, game: FeedGame, slots: tuple[SlotRef, ...], entries: tuple[FeedEntry, ...]
    ) -> set[HintKey] | None:
        """The person's hints that are not found yet: read from their trackers the first time (and again when their
        slots changed), then kept current from the feed. None when the manager could not tell (the next batch tries
        again)."""
        key = (link.discord_user_id, game.id)
        names = frozenset(slot.name for slot in slots)
        if key not in self._hints or self._hints[key][0] != names:
            seeded = await self._seed(link, game, slots, entries)
            if seeded is None:
                return None
            self._hints[key] = (names, seeded)
        return self._hints[key][1]

    async def _seed(
        self, link: UserLink, game: FeedGame, slots: tuple[SlotRef, ...], entries: tuple[FeedEntry, ...]
    ) -> set[HintKey] | None:
        """The tracker is read after `entries` were fetched, so a hint they fulfil already shows as found there. Those
        stay open for this batch, or their alert would be lost."""
        arriving = {hint_key(entry) for entry in entries if entry.kind == "item"}
        seeded: set[HintKey] = set()
        for slot in slots:
            hints = await self._tracker_hints(link, game.id, slot)
            if hints is None:
                return None
            for hint in hints:
                if not hint.for_me:
                    continue
                hint_id = (hint.receiver, hint.item, hint.finder, hint.location)
                if not hint.found or hint_id in arriving:
                    seeded.add(hint_id)
        return seeded

    async def _items_waiting_for(
        self, link: UserLink, game: FeedGame, slots: tuple[SlotRef, ...], player: str
    ) -> tuple[HintView, ...]:
        """Items in the person's own worlds that `player` was hinted and has not got yet."""
        waiting: list[HintView] = []
        for slot in slots:
            hints = await self._tracker_hints(link, game.id, slot)
            waiting.extend(h for h in hints or () if h.in_my_world and h.receiver == player and not h.found)
        return tuple(waiting)

    async def _tracker_hints(self, link: UserLink, game_id: int, slot: SlotRef) -> tuple[HintView, ...] | None:
        token = self._access.stored_token(link)
        if token is None:
            return None
        try:
            return parse_hints(await self._client.tracker(token, game_id, slot.slot))
        except TokenInvalid:
            self._access.on_token_invalid(link.discord_user_id)
        except ManagerError as exc:
            log.debug("Could not read a tracker for an alert: %s", exc.detail)
        except ValueError as exc:
            log.warning("Skipping a tracker for an alert: %s", exc)
        return None

    # --- from UserSync -----------------------------------------------------------------------------------------------
    async def handle_events(self, events: list[UserGameEvent]) -> None:
        """What happened to one linked person (a game went online, a slot was offered), if they asked to hear it."""
        for discord_id in dict.fromkeys(event.discord_user_id for event in events):
            link = self._links.get(discord_id)
            if link is None:
                continue
            enabled = self._prefs.enabled(discord_id)
            alerts = [a for event in events if event.discord_user_id == discord_id if (a := alert_for_event(event, enabled))]
            await self._deliver(link, alerts)

    async def handle_friend_requests(self, events: list[FriendRequestEvent]) -> None:
        """One message per new friend request, with buttons to answer it, for people who turned the alert on."""
        for event in events:
            link = self._links.get(event.discord_user_id)
            if link is None or FRIEND_REQUEST not in self._prefs.enabled(event.discord_user_id):
                continue
            lang = link.locale
            text = t("**{user}** wants to be your friend on the manager", lang, user=_name(event.from_name))
            embed = discord.Embed(title=t("Friend request", lang), description=text, colour=ACCENT)
            await self._send(link.discord_user_id, embed=embed, view=friend_request_view(event.from_id, lang))

    async def token_stopped(self, link: UserLink) -> None:
        """Tells a person, once, that the manager stopped accepting their token (they never have to have asked)."""
        self.forget(link.discord_user_id)
        await self._send(link.discord_user_id, embed=notice_embed(t(TOKEN_STOPPED_TEXT, link.locale)))

    # --- sending -----------------------------------------------------------------------------------------------------
    async def _deliver(self, link: UserLink, alerts: list[Alert]) -> None:
        """One message per game for the plain alerts, one per offer (its buttons belong to it)."""
        lang = link.locale
        by_game: dict[int, list[Alert]] = {}
        for alert in alerts:
            if alert.slot is None:
                by_game.setdefault(alert.game_id, []).append(alert)
        for game_alerts in by_game.values():
            lines, _ = fit_lines([a.text(lang) for a in game_alerts], DESCRIPTION)
            embed = discord.Embed(title=safe_text(game_alerts[0].game_name, NAME_LIMIT), description="\n".join(lines), colour=ACCENT)
            if not await self._send(link.discord_user_id, embed=embed):
                return
        for alert in alerts:
            if alert.slot is not None:
                embed = discord.Embed(title=safe_text(alert.game_name, NAME_LIMIT), description=alert.text(lang), colour=ACCENT)
                if not await self._send(link.discord_user_id, embed=embed, view=handover_view(alert.game_id, alert.slot, lang)):
                    return

    async def _send(self, discord_id: int, **message) -> bool:
        """False when nothing was sent. A person who closed their DMs is left alone for a day."""
        if self._muted_until.get(discord_id, 0) > self._now():
            return False
        try:
            recipient = await self._fetch_user(discord_id)
            await recipient.send(allowed_mentions=discord.AllowedMentions.none(), **message)
        except discord.Forbidden:
            self._muted_until[discord_id] = self._now() + MUTE_SECONDS
            log.info("A linked person does not take direct messages; not writing to them for a day")
            return False
        except discord.HTTPException as exc:
            log.warning("Could not send a direct message: %s", exc.text or exc.status)
            return False
        return True


def _keep_hints_current(open_hints: set[HintKey], entry: FeedEntry, slot_names: frozenset[str]) -> None:
    """A hint entry adds or closes a hint for the person; an item entry that fulfils one closes it."""
    if entry.kind == "hint" and entry.get("receiver") in slot_names:
        if entry.get("status").strip().lower() == "found":
            open_hints.discard(hint_key(entry))
        else:
            open_hints.add(hint_key(entry))
    elif entry.kind == "item":
        open_hints.discard(hint_key(entry))
