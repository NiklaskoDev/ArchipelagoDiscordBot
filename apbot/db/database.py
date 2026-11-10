"""The bot's SQLite file and its schema migrations.

One connection, used from the event loop only. The queries are small and local, so they run inline instead of in a
thread pool. Each statement commits by itself (autocommit); `migrate` wraps every migration in one transaction.
"""

import logging
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DATABASE_FILE = "bot.sqlite3"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
BUSY_TIMEOUT_MS = 5000
_MIGRATION_FILE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class Database:
    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], float] = time.time,
        migrations_dir: Path = MIGRATIONS_DIR,
    ):
        """Opens (and creates) the file. Raises OSError or sqlite3.Error when it cannot be used."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now
        self._migrations_dir = migrations_dir
        self._conn = sqlite3.connect(path, isolation_level=None)  # None: autocommit, transactions are explicit
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")

    def close(self) -> None:
        self._conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, tuple(params))

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def migrate(self) -> list[int]:
        """Applies the migrations that have not run yet, oldest first, each in its own transaction. Returns their
        versions. A failing migration is rolled back and raises; an applied one is never edited or run again."""
        self.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)"
        )
        applied = {row["version"] for row in self.query_all("SELECT version FROM schema_migrations")}
        done: list[int] = []
        for version, name, sql in self._pending(applied):
            log.info("Applying database migration %03d_%s", version, name)
            # name and version come from a file name that matched _MIGRATION_FILE, so they are safe to write inline
            script = (
                f"BEGIN;\n{sql}\n"
                f"INSERT INTO schema_migrations (version, name, applied_at) VALUES ({version}, '{name}', {self._now()!r});\n"
                "COMMIT;"
            )
            try:
                self._conn.executescript(script)
            except sqlite3.Error:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise
            done.append(version)
        return done

    def _pending(self, applied: set[int]) -> list[tuple[int, str, str]]:
        found = []
        for path in sorted(self._migrations_dir.glob("*.sql")):
            match = _MIGRATION_FILE.match(path.name)
            if match is None:
                raise ValueError(f"Migration file {path.name} does not match NNN_name.sql")
            version = int(match.group(1))
            if version not in applied:
                found.append((version, match.group(2), path.read_text(encoding="utf-8")))
        return found
