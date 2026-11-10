"""Per-server settings, set with /setup. A server that never ran /setup has the defaults (nothing is stored for it)."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from apbot.db.database import Database

_FIELDS = (
    "language",
    "timezone",
    "games_channel_id",
    "signups_channel_id",
    "overview_channel_id",
    "organizer_role_id",
    "admin_role_id",
    "scheduled_events",
)


@dataclass(frozen=True)
class GuildSettings:
    guild_id: int
    language: str = "en"
    timezone: str = "UTC"
    games_channel_id: int | None = None
    signups_channel_id: int | None = None
    overview_channel_id: int | None = None
    organizer_role_id: int | None = None
    admin_role_id: int | None = None
    scheduled_events: bool = False


class GuildSettingsStore:
    def __init__(self, db: Database, *, now: Callable[[], float] = time.time):
        self._db = db
        self._now = now

    def get(self, guild_id: int) -> GuildSettings:
        row = self._db.query_one("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        if row is None:
            return GuildSettings(guild_id)
        values = {name: row[name] for name in _FIELDS}
        return GuildSettings(guild_id, **{**values, "scheduled_events": bool(values["scheduled_events"])})

    def update(self, guild_id: int, **fields: str | int | bool | None) -> None:
        """Changes the given settings and leaves the others as they are; None clears a channel or role.
        Raises ValueError for a name that is not a setting (a bug, never user input)."""
        unknown = set(fields) - set(_FIELDS)
        if unknown or not fields:
            raise ValueError(f"not settings: {sorted(unknown) or 'nothing to change'}")
        values = {name: int(value) if isinstance(value, bool) else value for name, value in fields.items()}
        columns = ", ".join(values)  # names are checked against _FIELDS above
        marks = ", ".join("?" for _ in values)
        assignments = ", ".join(f"{name} = excluded.{name}" for name in values)
        self._db.execute(
            f"INSERT INTO guild_settings (guild_id, {columns}, updated_at) VALUES (?, {marks}, ?)"
            f" ON CONFLICT (guild_id) DO UPDATE SET {assignments}, updated_at = excluded.updated_at",
            (guild_id, *values.values(), self._now()),
        )

    def clear_role(self, guild_id: int, role_id: int) -> None:
        """A role was deleted in Discord: the organizer or admin role that was it is not set any more (a deleted
        organizer role would otherwise lock everyone out of /start, /stop and /restart)."""
        self._db.execute(
            "UPDATE guild_settings SET"
            " organizer_role_id = CASE WHEN organizer_role_id = ?1 THEN NULL ELSE organizer_role_id END,"
            " admin_role_id = CASE WHEN admin_role_id = ?1 THEN NULL ELSE admin_role_id END,"
            " updated_at = ?2 WHERE guild_id = ?3 AND ?1 IN (organizer_role_id, admin_role_id)",
            (role_id, self._now(), guild_id),
        )

    def delete(self, guild_id: int) -> None:
        """Forgets everything about a server (the bot was removed from it)."""
        self._db.execute("DELETE FROM guild_settings WHERE guild_id = ?", (guild_id,))
