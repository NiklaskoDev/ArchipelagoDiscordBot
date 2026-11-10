import sqlite3

import pytest

from apbot.db.database import MIGRATIONS_DIR, Database
from apbot.db.guilds import GuildSettings, GuildSettingsStore
from apbot.db.links import LinkStore


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db(tmp_path, clock):
    database = Database(tmp_path / "bot.sqlite3", now=clock)
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def links(db, clock) -> LinkStore:
    return LinkStore(db, now=clock)


@pytest.fixture
def guilds(db, clock) -> GuildSettingsStore:
    return GuildSettingsStore(db, now=clock)


def link(store: LinkStore, discord_id=1, manager_id=10, username="alice", ciphertext=b"cipher", hint="abcd", locale="en"):
    store.save(discord_id, manager_id, username, ciphertext, hint, locale)


# --- migrations --------------------------------------------------------------------------------------------------------
def test_first_start_applies_the_migrations_and_records_them(tmp_path, clock):
    database = Database(tmp_path / "nested" / "bot.sqlite3", now=clock)  # the folder is created
    assert database.migrate() == [1]
    [row] = database.query_all("SELECT version, name, applied_at FROM schema_migrations")
    assert (row["version"], row["name"], row["applied_at"]) == (1, "links_settings", 1000.0)
    tables = {r["name"] for r in database.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"user_links", "guild_settings", "schema_migrations"} <= tables
    database.close()


def test_a_second_start_applies_nothing_and_keeps_the_data(tmp_path, clock):
    path = tmp_path / "bot.sqlite3"
    first = Database(path, now=clock)
    first.migrate()
    link(LinkStore(first, now=clock))
    first.close()
    second = Database(path, now=clock)
    assert second.migrate() == []
    assert len(second.query_all("SELECT * FROM schema_migrations")) == 1
    assert LinkStore(second, now=clock).get(1) is not None
    second.close()


def test_only_the_new_migration_runs_when_one_is_added(tmp_path, clock):
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "001_first.sql").write_text("CREATE TABLE one (id INTEGER);", encoding="utf-8")
    database = Database(tmp_path / "bot.sqlite3", now=clock, migrations_dir=folder)
    assert database.migrate() == [1]
    (folder / "002_second.sql").write_text("CREATE TABLE two (id INTEGER);", encoding="utf-8")
    assert database.migrate() == [2]
    assert [r["version"] for r in database.query_all("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2]
    database.close()


def test_a_failing_migration_is_rolled_back_and_not_recorded(tmp_path, clock):
    folder = tmp_path / "migrations"
    folder.mkdir()
    broken = "CREATE TABLE half (id INTEGER);\nINSERT INTO missing VALUES (1);"
    (folder / "001_broken.sql").write_text(broken, encoding="utf-8")
    database = Database(tmp_path / "bot.sqlite3", now=clock, migrations_dir=folder)
    with pytest.raises(sqlite3.Error):
        database.migrate()
    assert database.query_all("SELECT * FROM schema_migrations") == []
    assert database.query_one("SELECT name FROM sqlite_master WHERE name = 'half'") is None
    (folder / "001_broken.sql").write_text("CREATE TABLE half (id INTEGER);", encoding="utf-8")
    assert database.migrate() == [1]  # the fixed file is applied: the failed one left no trace
    database.close()


def test_a_migration_file_with_a_bad_name_is_refused(tmp_path):
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "first.sql").write_text("SELECT 1;", encoding="utf-8")
    database = Database(tmp_path / "bot.sqlite3", migrations_dir=folder)
    with pytest.raises(ValueError):
        database.migrate()
    database.close()


def test_the_shipped_migrations_have_consecutive_numbers():
    numbers = [int(path.name[:3]) for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    assert numbers == list(range(1, len(numbers) + 1))


def test_connection_uses_wal_foreign_keys_and_a_busy_timeout(db):
    assert db.query_one("PRAGMA journal_mode")[0] == "wal"
    assert db.query_one("PRAGMA foreign_keys")[0] == 1
    assert db.query_one("PRAGMA busy_timeout")[0] == 5000


# --- links -------------------------------------------------------------------------------------------------------------
def test_a_saved_link_can_be_read_back_by_both_ids(links):
    link(links, locale="de")
    saved = links.get(1)
    assert (saved.discord_user_id, saved.manager_user_id, saved.manager_username) == (1, 10, "alice")
    assert (saved.token_ciphertext, saved.token_hint, saved.status, saved.locale) == (b"cipher", "abcd", "active", "de")
    assert (saved.linked_at, saved.checked_at) == (1000.0, 1000.0)
    assert links.by_manager_user(10) == saved
    assert links.get(2) is None and links.by_manager_user(11) is None


def test_the_repr_of_a_link_does_not_show_the_ciphertext(links):
    link(links, ciphertext=b"secret-cipher-bytes")
    assert "secret-cipher-bytes" not in repr(links.get(1))


def test_a_manager_account_can_only_be_linked_to_one_discord_user(links):
    link(links, discord_id=1, manager_id=10)
    with pytest.raises(sqlite3.IntegrityError):
        link(links, discord_id=2, manager_id=10)
    assert links.get(2) is None


def test_linking_again_replaces_the_token_and_makes_the_link_active(links, clock):
    link(links, ciphertext=b"old", hint="1111")
    links.mark_invalid(1)
    clock.now = 2000.0
    link(links, ciphertext=b"new", hint="2222", username="alice2", manager_id=11, locale="fr")
    saved = links.get(1)
    assert (saved.token_ciphertext, saved.token_hint, saved.status) == (b"new", "2222", "active")
    assert (saved.manager_user_id, saved.manager_username, saved.locale, saved.linked_at) == (11, "alice2", "fr", 2000.0)


def test_an_invalid_link_forgets_the_token_but_keeps_the_rest(links, clock):
    link(links)
    clock.now = 3000.0
    links.mark_invalid(1)
    saved = links.get(1)
    assert saved.status == "invalid" and saved.token_ciphertext is None
    assert (saved.manager_username, saved.token_hint, saved.checked_at) == ("alice", "abcd", 3000.0)
    assert links.all_active() == []


def test_replacing_the_ciphertext_changes_nothing_else_and_skips_invalid_links(links):
    link(links)
    links.replace_ciphertext(1, b"rotated")
    assert links.get(1).token_ciphertext == b"rotated" and links.get(1).status == "active"
    links.mark_invalid(1)
    links.replace_ciphertext(1, b"zombie")
    assert links.get(1).token_ciphertext is None


def test_deleting_a_link_removes_all_of_it(links):
    link(links)
    assert links.delete(1) is True
    assert links.get(1) is None and links.by_manager_user(10) is None
    assert links.delete(1) is False
    link(links, discord_id=2, manager_id=10)  # the manager account is free again


def test_locale_can_change_and_only_known_languages_are_stored(links):
    link(links)
    links.set_locale(1, "nl")
    assert links.get(1).locale == "nl"
    with pytest.raises(sqlite3.IntegrityError):
        links.set_locale(1, "xx")


def test_only_active_links_are_listed(links):
    link(links, 1, 10)
    link(links, 2, 20)
    link(links, 3, 30)
    links.mark_invalid(2)
    assert [saved.discord_user_id for saved in links.all_active()] == [1, 3]


def test_a_link_with_an_unknown_status_is_refused_by_the_database(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO user_links (discord_user_id, manager_user_id, manager_username, status, linked_at, checked_at)"
            " VALUES (1, 1, 'a', 'weird', 0, 0)"
        )


# --- guild settings ----------------------------------------------------------------------------------------------------
def test_a_server_without_settings_has_the_defaults_and_nothing_is_stored(guilds, db):
    assert guilds.get(5) == GuildSettings(5)
    assert guilds.get(5).language == "en" and guilds.get(5).timezone == "UTC"
    assert db.query_all("SELECT * FROM guild_settings") == []


def test_updating_changes_only_the_given_settings(guilds):
    guilds.update(5, language="de", games_channel_id=111)
    guilds.update(5, timezone="Europe/Berlin", organizer_role_id=222)
    assert guilds.get(5) == GuildSettings(
        5, language="de", timezone="Europe/Berlin", games_channel_id=111, organizer_role_id=222
    )
    assert guilds.get(6) == GuildSettings(6)  # other servers are not touched


def test_none_clears_a_channel_or_role(guilds):
    guilds.update(5, admin_role_id=7, signups_channel_id=8)
    guilds.update(5, admin_role_id=None)
    assert guilds.get(5).admin_role_id is None and guilds.get(5).signups_channel_id == 8


def test_scheduled_events_is_a_boolean(guilds):
    guilds.update(5, scheduled_events=True)
    assert guilds.get(5).scheduled_events is True
    guilds.update(5, scheduled_events=False)
    assert guilds.get(5).scheduled_events is False


def test_update_stamps_the_time(guilds, db, clock):
    guilds.update(5, language="fr")
    clock.now = 4000.0
    guilds.update(5, language="nl")
    assert db.query_one("SELECT updated_at FROM guild_settings")[0] == 4000.0


@pytest.mark.parametrize("fields", [{"nonsense": 1}, {"language": "de", "updated_at": 9}, {}])
def test_update_refuses_names_that_are_not_settings(guilds, fields):
    with pytest.raises(ValueError):
        guilds.update(5, **fields)


def test_an_unknown_language_is_refused_by_the_database(guilds):
    with pytest.raises(sqlite3.IntegrityError):
        guilds.update(5, language="xx")


def test_deleting_a_server_removes_only_its_settings(guilds):
    guilds.update(5, language="de")
    guilds.update(6, language="fr")
    guilds.delete(5)
    assert guilds.get(5) == GuildSettings(5)
    assert guilds.get(6).language == "fr"
    guilds.delete(5)  # nothing there: no error
