"""What the live features remember (M3): the thread and role of each game per server, how far each game's activity feed
was read, who wants which direct messages, the overview message and the scheduled events the bot made."""

import json
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from apbot.db.database import Database
from apbot.manager.views import FEED_KINDS

ALERT_KINDS = ("item_received", "hint_found", "stuck_in_my_world", "game_online", "slot_offered")
# What a new thread shows. Items and joins are off: they are many, and a text client makes the server announce joins.
DEFAULT_FEED_KINDS = tuple(kind for kind in FEED_KINDS if kind not in ("item", "join", "part"))


@dataclass(frozen=True)
class GameSpace:
    guild_id: int
    game_id: int
    thread_id: int | None
    role_id: int | None
    kinds: tuple[str, ...]
    closed_at: float | None
    created_at: float


def _space(row: sqlite3.Row) -> GameSpace:
    try:
        loaded = json.loads(row["kinds"])
    except ValueError:
        loaded = []
    kinds = tuple(kind for kind in FEED_KINDS if isinstance(loaded, list) and kind in loaded)
    return GameSpace(
        guild_id=row["guild_id"],
        game_id=row["game_id"],
        thread_id=row["thread_id"],
        role_id=row["role_id"],
        kinds=kinds,
        closed_at=row["closed_at"],
        created_at=row["created_at"],
    )


class GameSpaceStore:
    """The thread and the role the bot made for a game in a server."""

    def __init__(self, db: Database, *, now: Callable[[], float] = time.time):
        self._db = db
        self._now = now

    def get(self, guild_id: int, game_id: int) -> GameSpace | None:
        row = self._db.query_one("SELECT * FROM game_spaces WHERE guild_id = ? AND game_id = ?", (guild_id, game_id))
        return _space(row) if row else None

    def open_for_guild(self, guild_id: int) -> list[GameSpace]:
        rows = self._db.query_all(
            "SELECT * FROM game_spaces WHERE guild_id = ? AND closed_at IS NULL ORDER BY game_id", (guild_id,)
        )
        return [_space(row) for row in rows]

    def open_for_game(self, game_id: int) -> list[GameSpace]:
        rows = self._db.query_all(
            "SELECT * FROM game_spaces WHERE game_id = ? AND closed_at IS NULL ORDER BY guild_id", (game_id,)
        )
        return [_space(row) for row in rows]

    def create(
        self, guild_id: int, game_id: int, thread_id: int | None, role_id: int | None, kinds: Iterable[str]
    ) -> None:
        self._db.execute(
            "INSERT INTO game_spaces (guild_id, game_id, thread_id, role_id, kinds, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, game_id, thread_id, role_id, json.dumps(list(kinds)), self._now()),
        )

    def set_thread(self, guild_id: int, game_id: int, thread_id: int | None) -> None:
        self._db.execute(
            "UPDATE game_spaces SET thread_id = ? WHERE guild_id = ? AND game_id = ?", (thread_id, guild_id, game_id)
        )

    def set_role(self, guild_id: int, game_id: int, role_id: int | None) -> None:
        self._db.execute(
            "UPDATE game_spaces SET role_id = ? WHERE guild_id = ? AND game_id = ?", (role_id, guild_id, game_id)
        )

    def set_kinds(self, guild_id: int, game_id: int, kinds: Iterable[str]) -> None:
        wanted = set(kinds)
        ordered = [kind for kind in FEED_KINDS if kind in wanted]  # also drops anything that is not a feed kind
        self._db.execute(
            "UPDATE game_spaces SET kinds = ? WHERE guild_id = ? AND game_id = ?",
            (json.dumps(ordered), guild_id, game_id),
        )

    def close(self, guild_id: int, game_id: int) -> None:
        """The game is over: the thread is archived and the role gone."""
        self._db.execute(
            "UPDATE game_spaces SET closed_at = ?, role_id = NULL WHERE guild_id = ? AND game_id = ?",
            (self._now(), guild_id, game_id),
        )

    def delete_guild(self, guild_id: int) -> None:
        self._db.execute("DELETE FROM game_spaces WHERE guild_id = ?", (guild_id,))


class FeedCursorStore:
    """The number of the last activity entry of each game that was read, so a restart repeats nothing."""

    def __init__(self, db: Database, *, now: Callable[[], float] = time.time):
        self._db = db
        self._now = now

    def get(self, game_id: int) -> int | None:
        row = self._db.query_one("SELECT last_seq FROM feed_cursors WHERE game_id = ?", (game_id,))
        return row["last_seq"] if row else None

    def set(self, game_id: int, last_seq: int) -> None:
        self._db.execute(
            "INSERT INTO feed_cursors (game_id, last_seq, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (game_id) DO UPDATE SET last_seq = excluded.last_seq, updated_at = excluded.updated_at",
            (game_id, last_seq, self._now()),
        )


class AlertPrefStore:
    """Which direct messages a linked user asked for. Nothing is on until they choose it."""

    def __init__(self, db: Database):
        self._db = db

    def enabled(self, discord_user_id: int) -> frozenset[str]:
        rows = self._db.query_all("SELECT kind FROM alert_prefs WHERE discord_user_id = ?", (discord_user_id,))
        return frozenset(row["kind"] for row in rows)

    def replace(self, discord_user_id: int, kinds: Iterable[str]) -> None:
        """Makes exactly `kinds` the user's choice. Raises sqlite3.IntegrityError for someone who is not linked or for a
        kind that does not exist; callers check the link first."""
        with self._db.transaction():
            self._db.execute("DELETE FROM alert_prefs WHERE discord_user_id = ?", (discord_user_id,))
            for kind in sorted(set(kinds)):
                self._db.execute(
                    "INSERT INTO alert_prefs (discord_user_id, kind) VALUES (?, ?)", (discord_user_id, kind)
                )


@dataclass(frozen=True)
class OverviewMessage:
    guild_id: int
    channel_id: int
    message_id: int
    content_hash: str


class OverviewStore:
    def __init__(self, db: Database):
        self._db = db

    def get(self, guild_id: int) -> OverviewMessage | None:
        row = self._db.query_one("SELECT * FROM overview_messages WHERE guild_id = ?", (guild_id,))
        return OverviewMessage(**{key: row[key] for key in row.keys()}) if row else None

    def save(self, guild_id: int, channel_id: int, message_id: int, content_hash: str) -> None:
        self._db.execute(
            "INSERT INTO overview_messages (guild_id, channel_id, message_id, content_hash) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (guild_id) DO UPDATE SET channel_id = excluded.channel_id,"
            " message_id = excluded.message_id, content_hash = excluded.content_hash",
            (guild_id, channel_id, message_id, content_hash),
        )

    def delete_guild(self, guild_id: int) -> None:
        self._db.execute("DELETE FROM overview_messages WHERE guild_id = ?", (guild_id,))


@dataclass(frozen=True)
class ScheduledEventRow:
    guild_id: int
    source: str
    source_id: int
    event_id: int
    starts_at: float


class ScheduledEventStore:
    """The Discord scheduled events the bot made, by what they announce (a game's start, a sign-up's deadline)."""

    def __init__(self, db: Database):
        self._db = db

    def for_guild(self, guild_id: int) -> list[ScheduledEventRow]:
        rows = self._db.query_all("SELECT * FROM scheduled_events WHERE guild_id = ?", (guild_id,))
        return [ScheduledEventRow(**{key: row[key] for key in row.keys()}) for row in rows]

    def guilds(self) -> list[int]:
        return [row["guild_id"] for row in self._db.query_all("SELECT DISTINCT guild_id FROM scheduled_events")]

    def save(self, guild_id: int, source: str, source_id: int, event_id: int, starts_at: float) -> None:
        self._db.execute(
            "INSERT INTO scheduled_events (guild_id, source, source_id, event_id, starts_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (guild_id, source, source_id) DO UPDATE SET event_id = excluded.event_id,"
            " starts_at = excluded.starts_at",
            (guild_id, source, source_id, event_id, starts_at),
        )

    def delete(self, guild_id: int, source: str, source_id: int) -> None:
        self._db.execute(
            "DELETE FROM scheduled_events WHERE guild_id = ? AND source = ? AND source_id = ?",
            (guild_id, source, source_id),
        )

    def delete_guild(self, guild_id: int) -> None:
        self._db.execute("DELETE FROM scheduled_events WHERE guild_id = ?", (guild_id,))
