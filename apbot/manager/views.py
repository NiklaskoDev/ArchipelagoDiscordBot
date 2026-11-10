"""The manager's JSON as plain, read-only values: the only way manager data reaches embeds.

Each class lists exactly the fields the bot may show in public. The manager's answers also hold room passwords, webhook
URLs, notes and spectator links for the accounts that may see them; none of those has a field here, so none can be
printed by mistake, whatever token the bot used.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

PLAYER_SLOT_TYPE = 1  # Archipelago: 1 is a player, 2 a group of players


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
    visibility: str
    address: str | None  # host:port, as players type it
    tls: bool
    ap_version: str | None
    start_at: float | None
    created_at: float
    finished_at: float | None
    outcome: str | None  # finished | aborted, once the game is in the archive


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
        "visibility": _text(raw.get("visibility")) or "all",
        "address": _text(raw.get("address")),
        "tls": bool(raw.get("tls")),
        "ap_version": _text(raw.get("ap_version")),
        "start_at": _number(start.get("at")) if isinstance(start, Mapping) else None,
        "created_at": _number(raw.get("created_at")) or 0.0,
        "finished_at": _number(raw.get("finished_at")),
        "outcome": _text(raw.get("outcome")),
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
