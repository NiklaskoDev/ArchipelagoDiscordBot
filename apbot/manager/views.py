"""The manager's JSON as plain, read-only values: the only way manager data reaches embeds.

Each class lists exactly the fields the bot may show in public. The manager's answers also hold room passwords, webhook
URLs, notes and spectator links for the accounts that may see them; none of those has a field here, so none can be
printed by mistake, whatever token the bot used.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

PLAYER_SLOT_TYPE = 1  # Archipelago: 1 is a player, 2 a group of players

# The activity feed's entry kinds, each with the fields the bot reads. Anything else in an entry is dropped.
FEED_FIELDS: dict[str, tuple[str, ...]] = {
    "goal": ("player",),
    "release": ("player",),
    "collect": ("player",),
    "join": ("player", "game"),
    "part": ("player",),
    "server": ("text",),
    "hint": ("receiver", "item", "location", "finder", "entrance", "status"),
    "item": ("finder", "item", "receiver", "location"),
    "chat": ("player", "text"),
    "status": ("player", "status", "note"),
    "handover": ("player", "from", "to", "note"),
    "handover_done": ("player", "to", "from"),
}
FEED_KINDS = tuple(FEED_FIELDS)


@dataclass(frozen=True)
class SlotView:
    name: str
    game: str
    owner: str | None
    checked: int
    locations: int
    status: str | None  # online | offline | goal
    player_status: str | None  # what the player says: playing | stuck | away


@dataclass(frozen=True)
class GameSummary:
    id: int
    name: str
    state: str  # stopped | starting | running | stopping | crashed | sleeping
    world_count: int
    player_count: int
    claimed_count: int
    goal_count: int
    address: str | None  # host:port, as players type it
    tls: bool
    ap_version: str | None
    start_at: float | None
    created_at: float
    finished_at: float | None
    outcome: str | None  # finished | aborted, once the game is in the archive
    visibility: str | None = field(default=None, kw_only=True)  # all | participants; None when the manager did not say


@dataclass(frozen=True)
class GameDetail(GameSummary):
    slots: tuple[SlotView, ...]
    checked: int
    total: int


@dataclass(frozen=True)
class SignupSummary:
    id: int
    name: str
    open: bool
    deadline: float | None
    entry_count: int
    user_count: int
    slots_per_user: int
    creator: str | None


@dataclass(frozen=True)
class SlotRef:
    slot: int
    name: str


@dataclass(frozen=True)
class Offer:
    """A slot its player hands over to this user (`to_me`) or to whoever takes it."""

    slot: int
    name: str
    game: str
    owner: str | None
    note: str | None
    to_me: bool


@dataclass(frozen=True)
class UserGame:
    """A game as one linked user sees it: which slots are theirs and which are offered to them."""

    id: int
    name: str
    state: str
    finished_at: float | None
    my_slots: tuple[SlotRef, ...]
    offers: tuple[Offer, ...]


@dataclass(frozen=True)
class FeedEntry:
    seq: int
    at: float
    kind: str
    fields: Mapping[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str:
        """A field's text, "" when the entry has none."""
        return self.fields.get(name, "")


@dataclass(frozen=True)
class ActivityPage:
    entries: tuple[FeedEntry, ...]
    last: int  # the number of the newest entry the manager has, whatever was asked for


@dataclass(frozen=True)
class HintView:
    receiver: str
    item: str
    location: str
    finder: str
    found: bool
    for_me: bool
    in_my_world: bool


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _slot(raw: Mapping) -> SlotView:
    return SlotView(
        name=_text(raw.get("name")) or "?",
        game=_text(raw.get("game")) or "?",
        owner=_text(raw.get("owner")),
        checked=_int(raw.get("checked")),
        locations=_int(raw.get("locations")),
        status=_text(raw.get("status")),
        player_status=_text(raw.get("player_status")),
    )


def _require_id(raw: Any, what: str) -> int:
    if not isinstance(raw, Mapping) or _int(raw.get("id"), -1) < 0:
        raise ValueError(f"The manager sent {what} without an id")
    return raw["id"]


def _summary_fields(raw: Mapping) -> dict[str, Any]:
    game_id = _require_id(raw, "a game")
    start = raw.get("start")
    return {
        "id": game_id,
        "name": _text(raw.get("name")) or "?",
        "state": _text(raw.get("state")) or "stopped",
        "world_count": len(raw["games"]) if isinstance(raw.get("games"), list) else 0,
        "player_count": _int(raw.get("player_count")),
        "claimed_count": _int(raw.get("claimed_count")),
        "goal_count": _int(raw.get("goal_count")),
        "address": _text(raw.get("address")),
        "tls": bool(raw.get("tls")),
        "ap_version": _text(raw.get("ap_version")),
        "start_at": _number(start.get("at")) if isinstance(start, Mapping) else None,
        "created_at": _number(raw.get("created_at")) or 0.0,
        "finished_at": _number(raw.get("finished_at")),
        "outcome": _text(raw.get("outcome")),
        "visibility": _text(raw.get("visibility")),
    }


def parse_game_summary(raw: Mapping) -> GameSummary:
    """Raises ValueError when the answer is not a game (no id)."""
    return GameSummary(**_summary_fields(raw))


def parse_game_detail(raw: Mapping) -> GameDetail:
    fields = _summary_fields(raw)
    progress = raw.get("progress") if isinstance(raw.get("progress"), Mapping) else {}
    slots = raw.get("slots") if isinstance(raw.get("slots"), list) else []
    return GameDetail(
        **fields,
        slots=tuple(_slot(s) for s in slots if isinstance(s, Mapping) and s.get("type") == PLAYER_SLOT_TYPE),
        checked=_int(progress.get("checked")),
        total=_int(progress.get("total")),
    )


def parse_signup(raw: Mapping) -> SignupSummary:
    return SignupSummary(
        id=_require_id(raw, "a sign-up round"),
        name=_text(raw.get("name")) or "?",
        open=bool(raw.get("open")),
        deadline=_number(raw.get("deadline")),
        entry_count=_int(raw.get("entry_count")),
        user_count=_int(raw.get("user_count")),
        slots_per_user=_int(raw.get("slots_per_user"), 1),
        creator=_text(raw.get("creator")),
    )


def _slot_ref(raw: Any) -> SlotRef | None:
    if not isinstance(raw, Mapping) or _int(raw.get("slot"), -1) < 0:
        return None
    return SlotRef(slot=raw["slot"], name=_text(raw.get("name")) or "?")


def _offer(raw: Any) -> Offer | None:
    ref = _slot_ref(raw)
    if ref is None:
        return None
    return Offer(
        slot=ref.slot,
        name=ref.name,
        game=_text(raw.get("game")) or "?",
        owner=_text(raw.get("from")),
        note=_text(raw.get("note")),
        to_me=bool(raw.get("to_me")),
    )


def parse_user_game(raw: Mapping) -> UserGame:
    """Raises ValueError when the answer is not a game (no id)."""
    summary = _summary_fields(raw)
    my_slots = raw.get("my_slots") if isinstance(raw.get("my_slots"), list) else []
    offers = raw.get("offered_slots") if isinstance(raw.get("offered_slots"), list) else []
    return UserGame(
        id=summary["id"],
        name=summary["name"],
        state=summary["state"],
        finished_at=summary["finished_at"],
        my_slots=tuple(ref for ref in map(_slot_ref, my_slots) if ref is not None),
        offers=tuple(offer for offer in map(_offer, offers) if offer is not None),
    )


def parse_entry(raw: Any) -> FeedEntry | None:
    """One entry of the activity feed, reduced to the fields of its kind; None for an unknown kind or a broken entry."""
    if not isinstance(raw, Mapping) or raw.get("kind") not in FEED_FIELDS or _int(raw.get("seq"), -1) < 0:
        return None
    kind = raw["kind"]
    texts = {name: raw[name] for name in FEED_FIELDS[kind] if isinstance(raw.get(name), str) and raw[name]}
    return FeedEntry(seq=raw["seq"], at=_number(raw.get("at")) or 0.0, kind=kind, fields=texts)


def parse_activity(raw: Any) -> ActivityPage:
    """Raises ValueError when the answer is not an activity feed."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("entries"), list) or _int(raw.get("last"), -1) < 0:
        raise ValueError("The manager sent an activity feed in a form the bot does not know")
    entries = (parse_entry(item) for item in raw["entries"])
    return ActivityPage(entries=tuple(sorted((e for e in entries if e), key=lambda e: e.seq)), last=raw["last"])


def parse_hints(raw: Any) -> tuple[HintView, ...]:
    """The hints of a slot's tracker. Raises ValueError when the answer is not a tracker."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("hints"), list):
        raise ValueError("The manager sent a tracker in a form the bot does not know")
    return tuple(
        HintView(
            receiver=_text(hint.get("receiver")) or "?",
            item=_text(hint.get("item")) or "?",
            location=_text(hint.get("location")) or "?",
            finder=_text(hint.get("finder")) or "?",
            found=bool(hint.get("found")),
            for_me=bool(hint.get("for_me")),
            in_my_world=bool(hint.get("in_my_world")),
        )
        for hint in raw["hints"]
        if isinstance(hint, Mapping)
    )


def _parse_all(raw: Any, parse, what: str) -> list:
    if not isinstance(raw, list):
        raise TypeError(f"expected a list of {what}")
    parsed = []
    for item in raw:
        try:
            parsed.append(parse(item))
        except ValueError:
            log.warning("Skipping an entry in the manager's list of %s that has no usable id", what)
    return parsed


def parse_games(raw: Any) -> list[GameSummary]:
    return _parse_all(raw, parse_game_summary, "games")


def parse_user_games(raw: Any) -> list[UserGame]:
    return _parse_all(raw, parse_user_game, "games")


def parse_signups(raw: Any) -> list[SignupSummary]:
    return _parse_all(raw, parse_signup, "sign-ups")


def listed_games(games: Iterable[GameSummary]) -> list[GameSummary]:
    """The games that are not finished: running ones first, then the newest."""
    live = [g for g in games if g.finished_at is None]
    return sorted(live, key=lambda g: (g.state != "running", -g.created_at, -g.id))


def open_signups(signups: Iterable[SignupSummary]) -> list[SignupSummary]:
    """The rounds that take entries, the one closing soonest first (rounds without a deadline last)."""
    rounds = [s for s in signups if s.open]
    return sorted(rounds, key=lambda s: (s.deadline is None, s.deadline or 0.0, s.id))
