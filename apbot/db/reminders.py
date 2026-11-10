"""Which sign-up deadline reminders were already posted (M5): once per server, round, deadline and lead time."""

import time
from collections.abc import Callable, Collection

from apbot.db.database import Database


class ReminderStore:
    def __init__(self, db: Database, *, now: Callable[[], float] = time.time):
        self._db = db
        self._now = now

    def was_sent(self, guild_id: int, signup_id: int, deadline: float, before_seconds: int) -> bool:
        row = self._db.query_one(
            "SELECT 1 FROM signup_reminders WHERE guild_id = ? AND signup_id = ? AND deadline = ? AND before_seconds = ?",
            (guild_id, signup_id, deadline, before_seconds),
        )
        return row is not None

    def mark_sent(self, guild_id: int, signup_id: int, deadline: float, before_seconds: int) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO signup_reminders (guild_id, signup_id, deadline, before_seconds, sent_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (guild_id, signup_id, deadline, before_seconds, self._now()),
        )

    def forget_other_rounds(self, guild_id: int, open_signup_ids: Collection[int]) -> None:
        """Drops what is remembered about rounds that are not open any more (the table would only grow)."""
        marks = ", ".join("?" for _ in open_signup_ids)
        self._db.execute(
            f"DELETE FROM signup_reminders WHERE guild_id = ? AND signup_id NOT IN ({marks})",  # marks are only "?"
            (guild_id, *open_signup_ids),
        )

    def delete_guild(self, guild_id: int) -> None:
        self._db.execute("DELETE FROM signup_reminders WHERE guild_id = ?", (guild_id,))
