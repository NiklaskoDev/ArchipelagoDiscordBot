"""Linked manager accounts: which Discord user acts as which manager user, and with which (encrypted) token."""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from apbot.db.database import Database

STATUS_ACTIVE = "active"
STATUS_INVALID = "invalid"


@dataclass(frozen=True)
class UserLink:
    discord_user_id: int
    manager_user_id: int
    manager_username: str
    token_hint: str | None  # last 4 characters of the token
    status: str  # active | invalid
    locale: str
    linked_at: float
    checked_at: float
    token_ciphertext: bytes | None = field(default=None, repr=False)  # None once the manager refused the token


def _link(row: sqlite3.Row) -> UserLink:
    return UserLink(**{key: row[key] for key in row.keys()})


class LinkStore:
    def __init__(self, db: Database, *, now: Callable[[], float] = time.time):
        self._db = db
        self._now = now

    def get(self, discord_user_id: int) -> UserLink | None:
        row = self._db.query_one("SELECT * FROM user_links WHERE discord_user_id = ?", (discord_user_id,))
        return _link(row) if row else None

    def by_manager_user(self, manager_user_id: int) -> UserLink | None:
        row = self._db.query_one("SELECT * FROM user_links WHERE manager_user_id = ?", (manager_user_id,))
        return _link(row) if row else None

    def save(
        self,
        discord_user_id: int,
        manager_user_id: int,
        manager_username: str,
        token_ciphertext: bytes,
        token_hint: str,
        locale: str,
    ) -> None:
        """Links (or links again, replacing the old token). Raises sqlite3.IntegrityError when the manager account is
        linked to another Discord user already; callers check `by_manager_user` first to say so in words."""
        now = self._now()
        self._db.execute(
            "INSERT INTO user_links (discord_user_id, manager_user_id, manager_username, token_ciphertext, token_hint,"
            " status, locale, linked_at, checked_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)"
            " ON CONFLICT (discord_user_id) DO UPDATE SET manager_user_id = excluded.manager_user_id,"
            " manager_username = excluded.manager_username, token_ciphertext = excluded.token_ciphertext,"
            " token_hint = excluded.token_hint, status = 'active', locale = excluded.locale,"
            " linked_at = excluded.linked_at, checked_at = excluded.checked_at",
            (discord_user_id, manager_user_id, manager_username, token_ciphertext, token_hint, locale, now, now),
        )

    def replace_ciphertext(self, discord_user_id: int, token_ciphertext: bytes) -> None:
        """Stores the same token encrypted again (with a newer key). Does nothing for an invalid link."""
        self._db.execute(
            "UPDATE user_links SET token_ciphertext = ? WHERE discord_user_id = ? AND status = 'active'",
            (token_ciphertext, discord_user_id),
        )

    def mark_invalid(self, discord_user_id: int) -> None:
        """The manager no longer accepts the token: forget it, keep the link so the person is told to link again."""
        self._db.execute(
            "UPDATE user_links SET status = 'invalid', token_ciphertext = NULL, checked_at = ?"
            " WHERE discord_user_id = ?",
            (self._now(), discord_user_id),
        )

    def delete(self, discord_user_id: int) -> bool:
        return self._db.execute("DELETE FROM user_links WHERE discord_user_id = ?", (discord_user_id,)).rowcount > 0

    def set_locale(self, discord_user_id: int, locale: str) -> None:
        self._db.execute("UPDATE user_links SET locale = ? WHERE discord_user_id = ?", (locale, discord_user_id))

    def all_active(self) -> list[UserLink]:
        rows = self._db.query_all("SELECT * FROM user_links WHERE status = 'active' ORDER BY discord_user_id")
        return [_link(row) for row in rows]
