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
RECENT_PROGRESSION = 10  # progression items a tracker keeps

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
    slot: int = field(default=-1, kw_only=True)  # the slot's number in the multiworld
    claimable: bool = field(default=False, kw_only=True)  # the person who asked may claim it


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
    # What the asking account may do with the game. Never shown: they only spare a pointless question.
    can_manage: bool = field(default=False, kw_only=True)
    can_archive: bool = field(default=False, kw_only=True)
    return_blocked: str | None = field(default=None, kw_only=True)  # why the game cannot leave the archive


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
    status: str = "open"  # open, closed, generating, done
    can_manage: bool = False  # for the account that asked: may it close and test the round


@dataclass(frozen=True)
class EntryView:
    """One config entered in a round. `name`, `generation_error` are only sent for the asker's own entries and to the
    round's organizer; the other entries of a round are not shown to players at all."""

    id: int
    mine: bool
    username: str | None
    name: str | None
    config_id: int | None  # the saved config the entry was made from
    games: tuple[str, ...]
    slots: int
    issues: tuple[str, ...]
    generation_error: str | None


@dataclass(frozen=True)
class RoundTest:
    """The latest test of a round (only sent to its organizer). `current` is False when the entries changed after it."""

    status: str  # queued, running, succeeded, failed, cancelled
    current: bool
    error: str | None


@dataclass(frozen=True)
class SignupDetail(SignupSummary):
    description: str | None = None
    allowed_games: tuple[str, ...] = ()
    mystery: bool = False
    my_slot_count: int = 0
    error: str | None = None  # why the last generation failed (organizer only)
    entries: tuple[EntryView, ...] = ()
    test: RoundTest | None = None


@dataclass(frozen=True)
class ConfigView:
    """A saved config as the manager lists it. Only what a pick list needs: never the players' names or the content."""

    id: int
    name: str
    games: tuple[str, ...]
    owner_id: int | None = field(default=None, kw_only=True)  # the manager user it belongs to
    owner: str | None = field(default=None, kw_only=True)  # their username
    share_with: str | None = field(default=None, kw_only=True)  # everyone | friends | chosen; None when not shared
    shared_with: tuple[int, ...] = field(default=(), kw_only=True)  # the friends it is shared with by name (own configs)


@dataclass(frozen=True)
class PlayerCheck:
    name: str
    games: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class CheckResult:
    """What the manager says about a config without generating it."""

    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    players: tuple[PlayerCheck, ...]
    missing_roms: tuple[str, ...]


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
class JobPlayer:
    """One config of a generation: whose it is (`owner` is None for a deleted account) and the file it became."""

    user_id: int | None
    owner: str | None
    config_id: int | None
    filename: str


JOB_FINISHED = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class JobView:
    """A generation job. `status` is queued, running, succeeded, failed or cancelled (anything else is kept as text)."""

    id: int
    name: str | None
    status: str
    game_id: int | None
    error: str | None
    queue_position: int | None
    host_after: bool
    players: tuple[JobPlayer, ...]


@dataclass(frozen=True)
class FriendView:
    """A person on the friend list or in a request. `at` is when they became friends or when the request was made."""

    id: int
    username: str
    at: float | None
    shared_configs: int = 0  # configs they share with the asker (friends only)
    games_together: int = 0


@dataclass(frozen=True)
class FriendsOverview:
    friends: tuple[FriendView, ...]
    incoming: tuple[FriendView, ...]  # requests waiting for the asker's answer
    outgoing: tuple[FriendView, ...]  # requests the asker made

    @property
    def is_empty(self) -> bool:
        return not (self.friends or self.incoming or self.outgoing)


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
    status: int = field(default=0, kw_only=True)  # 10 no priority, 20 avoid, 30 priority, 40 found; 0 when not marked
    # What the server knows the hint by, to change its status; None when the manager did not send them.
    finder_slot: int | None = field(default=None, kw_only=True)
    location_id: int | None = field(default=None, kw_only=True)


FLAG_PROGRESSION = 0b001  # Archipelago's item classification: the item is needed to finish
RULES = ("available", "after_goal", "automatic", "disabled", "done")  # what !release and !collect would do now


@dataclass(frozen=True)
class TrackerView:
    """One slot's tracker: counts and the newest progression items instead of every item and location."""

    name: str
    game: str
    goal: bool
    received: int
    recent_progression: tuple[str, ...]  # the newest ones last
    checked: int
    total: int
    hints_used: int
    hint_points: int | None
    hint_cost: int
    release: str  # one of RULES
    collect: str
    hints: tuple[HintView, ...]


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _id_or_none(value: Any) -> int | None:
    """A slot or location number the server uses; None when absent or not a non-negative number."""
    number = _optional_int(value)
    return number if number is not None and number >= 0 else None


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
        slot=_int(raw.get("slot"), -1),
        claimable=bool(raw.get("claimable")),
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
        "can_manage": bool(raw.get("can_manage")),
        "can_archive": bool(raw.get("can_archive")),
        "return_blocked": _text(raw.get("return_blocked")),
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
        status=_text(raw.get("status")) or "open",
        can_manage=bool(raw.get("can_manage")),
    )


def _texts(value: Any) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str) and item) if isinstance(value, list) else ()


def _entry_view(raw: Any) -> EntryView | None:
    if not isinstance(raw, Mapping) or _int(raw.get("id"), -1) < 0:
        return None
    return EntryView(
        id=raw["id"],
        mine=bool(raw.get("mine")),
        username=_text(raw.get("username")),
        name=_text(raw.get("name")),
        config_id=_optional_int(raw.get("config_id")),
        games=_texts(raw.get("games")),
        slots=_int(raw.get("slots"), 1),
        issues=_texts(raw.get("issues")),
        generation_error=_text(raw.get("generation_error")),
    )


def _round_test(raw: Any) -> RoundTest | None:
    if not isinstance(raw, Mapping) or not _text(raw.get("status")):
        return None
    return RoundTest(status=raw["status"], current=bool(raw.get("current")), error=_text(raw.get("error")))


def parse_signup_detail(raw: Mapping) -> SignupDetail:
    """A round with its entries, as the asker may see them. Raises ValueError when the answer is not a round."""
    summary = parse_signup(raw)
    entries = raw.get("entries") if isinstance(raw.get("entries"), list) else []
    return SignupDetail(
        **vars(summary),
        description=_text(raw.get("description")),
        allowed_games=_texts(raw.get("allowed_games")),
        mystery=bool(raw.get("mystery")),
        my_slot_count=_int(raw.get("my_slot_count")),
        error=_text(raw.get("error")),
        entries=tuple(e for e in map(_entry_view, entries) if e is not None),
        test=_round_test(raw.get("test")),
    )


def parse_added_warnings(raw: Any) -> tuple[str, ...]:
    """The manager's warnings about a config that was just entered in a round."""
    return _texts(raw.get("warnings")) if isinstance(raw, Mapping) else ()


def parse_config(raw: Mapping) -> ConfigView:
    """Raises ValueError when the answer is not a config."""
    chosen = raw.get("shared_with") if isinstance(raw.get("shared_with"), list) else []
    return ConfigView(
        id=_require_id(raw, "a config"),
        name=_text(raw.get("name")) or "?",
        games=_texts(raw.get("games")),
        owner_id=_optional_int(raw.get("user_id")),
        owner=_text(raw.get("owner")),
        share_with=_text(raw.get("share_with")) if raw.get("shared") is True else None,
        shared_with=tuple(
            person["id"] for person in chosen if isinstance(person, Mapping) and _optional_int(person.get("id")) is not None
        ),
    )


def parse_check(raw: Any) -> CheckResult:
    """Raises ValueError when the answer is not a check result."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("ok"), bool):
        raise ValueError("The manager sent a check result without a verdict")
    players = raw.get("players") if isinstance(raw.get("players"), list) else []
    return CheckResult(
        ok=raw["ok"],
        errors=_texts(raw.get("errors")),
        warnings=_texts(raw.get("warnings")),
        players=tuple(
            PlayerCheck(
                name=_text(p.get("name")) or "?",
                games=_texts(p.get("games")),
                errors=_texts(p.get("errors")),
                warnings=_texts(p.get("warnings")),
            )
            for p in players
            if isinstance(p, Mapping)
        ),
        missing_roms=_texts(raw.get("missing_roms")),
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


def _job_player(raw: Any) -> JobPlayer | None:
    if not isinstance(raw, Mapping):
        return None
    return JobPlayer(
        user_id=_optional_int(raw.get("user_id")),
        owner=_text(raw.get("owner")),
        config_id=_optional_int(raw.get("config_id")),
        filename=_text(raw.get("filename")) or "?",
    )


def parse_job(raw: Any) -> JobView:
    """Raises ValueError when the answer is not a job (no id)."""
    job_id = _require_id(raw, "a job")
    players = raw.get("players") if isinstance(raw.get("players"), list) else []
    return JobView(
        id=job_id,
        name=_text(raw.get("name")),
        status=_text(raw.get("status")) or "queued",
        game_id=_optional_int(raw.get("game_id")),
        error=_text(raw.get("error")),
        queue_position=_optional_int(raw.get("queue_position")),
        host_after=bool(raw.get("host_after")),
        players=tuple(p for p in map(_job_player, players) if p is not None),
    )


def _friend(raw: Any, time_key: str) -> FriendView | None:
    if not isinstance(raw, Mapping) or _int(raw.get("id"), -1) < 0 or not _text(raw.get("username")):
        return None
    return FriendView(
        id=raw["id"],
        username=raw["username"],
        at=_number(raw.get(time_key)),
        shared_configs=_int(raw.get("shared_configs")),
        games_together=_int(raw.get("games_together")),
    )


def _friend_list(raw: Mapping, key: str, time_key: str) -> tuple[FriendView, ...]:
    entries = raw.get(key)
    if not isinstance(entries, list):
        raise ValueError(f"The manager's friend list has no list of {key}")
    return tuple(friend for friend in (_friend(entry, time_key) for entry in entries) if friend is not None)


def parse_friends(raw: Any) -> FriendsOverview:
    """The friend list, the requests for the asker and the ones they made. Raises ValueError when the answer does not
    have that shape. The manager's suggestions ("people you played with") are not read."""
    if not isinstance(raw, Mapping):
        raise ValueError("The manager sent a friend list that is not an object")
    return FriendsOverview(
        friends=_friend_list(raw, "friends", "since"),
        incoming=_friend_list(raw, "incoming", "asked_at"),
        outgoing=_friend_list(raw, "outgoing", "asked_at"),
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
            status=_int(hint.get("status")),
            finder_slot=_id_or_none(hint.get("finder_slot")),
            location_id=_id_or_none(hint.get("location_id")),
        )
        for hint in raw["hints"]
        if isinstance(hint, Mapping)
    )


def _rule(value: Any) -> str:
    """A rule the manager reports; one the bot does not know counts as available, so the server decides."""
    return value if value in RULES else "available"


def parse_tracker(raw: Any) -> TrackerView:
    """Raises ValueError when the answer is not a tracker."""
    hints = parse_hints(raw)
    items = [item for item in raw.get("items") or [] if isinstance(item, Mapping)]
    locations = [location for location in raw.get("locations") or [] if isinstance(location, Mapping)]
    progression = [_text(item.get("item")) or "?" for item in items if _int(item.get("flags")) & FLAG_PROGRESSION]
    return TrackerView(
        name=_text(raw.get("name")) or "?",
        game=_text(raw.get("game")) or "?",
        goal=bool(raw.get("goal")),
        received=len(items),
        recent_progression=tuple(progression[-RECENT_PROGRESSION:]),
        checked=sum(1 for location in locations if location.get("checked")),
        total=len(locations),
        hints_used=_int(raw.get("hints_used")),
        hint_points=_optional_int(raw.get("hint_points")),
        hint_cost=_int(raw.get("hint_cost")),
        release=_rule(raw.get("release")),
        collect=_rule(raw.get("collect")),
        hints=hints,
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


def parse_configs(raw: Any) -> list[ConfigView]:
    return _parse_all(raw, parse_config, "configs")


def listed_games(games: Iterable[GameSummary]) -> list[GameSummary]:
    """The games that are not finished: running ones first, then the newest."""
    live = [g for g in games if g.finished_at is None]
    return sorted(live, key=lambda g: (g.state != "running", -g.created_at, -g.id))


def open_signups(signups: Iterable[SignupSummary]) -> list[SignupSummary]:
    """The rounds that take entries, the one closing soonest first (rounds without a deadline last)."""
    rounds = [s for s in signups if s.open]
    return sorted(rounds, key=lambda s: (s.deadline is None, s.deadline or 0.0, s.id))
