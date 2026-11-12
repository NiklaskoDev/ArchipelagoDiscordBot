import sqlite3

import discord
import pytest

from apbot.db.database import Database
from apbot.db.links import LinkStore
from apbot.db.live import (
    ALERT_KINDS,
    DEFAULT_FEED_KINDS,
    AlertPrefStore,
    FeedCursorStore,
    GameSpaceStore,
    OverviewStore,
    ScheduledEventStore,
)
from apbot.manager.views import FEED_KINDS
from apbot.ui.embeds import ALERT_KIND_LABELS


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "bot.sqlite3", now=lambda: 1000.0)
    database.migrate()
    yield database
    database.close()


def link(db, discord_id=1, manager_id=10):
    LinkStore(db).save(discord_id, manager_id, "alice", b"cipher", "abcd", "en")


# --- feed kinds --------------------------------------------------------------------------------------------------------
def test_new_threads_show_everything_except_items_joins_and_leaves():
    assert set(FEED_KINDS) - set(DEFAULT_FEED_KINDS) == {"item", "join", "part"}
    assert len(FEED_KINDS) == 12


# --- game spaces -------------------------------------------------------------------------------------------------------
def test_a_game_space_is_stored_per_server_and_game(db):
    spaces = GameSpaceStore(db, now=lambda: 5.0)
    spaces.create(1, 7, 100, 200, DEFAULT_FEED_KINDS)
    spaces.create(2, 7, 101, 201, ["chat"])
    space = spaces.get(1, 7)
    assert (space.thread_id, space.role_id, space.kinds, space.closed_at, space.created_at) == (
        100, 200, DEFAULT_FEED_KINDS, None, 5.0,
    )
    assert spaces.get(2, 7).kinds == ("chat",)
    assert spaces.get(1, 8) is None
    assert [s.guild_id for s in spaces.open_for_game(7)] == [1, 2]
    assert [s.game_id for s in spaces.open_for_guild(1)] == [7]


def test_a_space_exists_once_per_server_and_game(db):
    spaces = GameSpaceStore(db)
    spaces.create(1, 7, 100, None, DEFAULT_FEED_KINDS)
    with pytest.raises(sqlite3.IntegrityError):
        spaces.create(1, 7, 101, None, DEFAULT_FEED_KINDS)


def test_kinds_are_kept_in_feed_order_and_unknown_ones_are_dropped(db):
    spaces = GameSpaceStore(db)
    spaces.create(1, 7, 100, None, DEFAULT_FEED_KINDS)
    spaces.set_kinds(1, 7, ["chat", "item", "nonsense", "goal"])
    assert spaces.get(1, 7).kinds == ("goal", "item", "chat")
    spaces.set_kinds(1, 7, [])
    assert spaces.get(1, 7).kinds == ()


def test_closing_a_space_keeps_the_row_forgets_the_role_and_lists_it_no_more(db):
    spaces = GameSpaceStore(db, now=lambda: 9.0)
    spaces.create(1, 7, 100, 200, DEFAULT_FEED_KINDS)
    spaces.close(1, 7)
    space = spaces.get(1, 7)
    assert (space.closed_at, space.role_id, space.thread_id) == (9.0, None, 100)
    assert spaces.open_for_guild(1) == [] and spaces.open_for_game(7) == []


def test_the_role_can_be_replaced(db):
    spaces = GameSpaceStore(db)
    spaces.create(1, 7, 100, None, DEFAULT_FEED_KINDS)
    spaces.set_role(1, 7, 55)
    assert spaces.get(1, 7).role_id == 55


# --- feed cursors ------------------------------------------------------------------------------------------------------
def test_a_cursor_is_remembered_and_moves_on(db):
    cursors = FeedCursorStore(db)
    assert cursors.get(7) is None
    cursors.set(7, 12)
    cursors.set(7, 15)
    assert cursors.get(7) == 15 and cursors.get(8) is None


# --- alert choices -----------------------------------------------------------------------------------------------------
def test_alerts_are_off_until_chosen(db):
    link(db)
    assert AlertPrefStore(db).enabled(1) == frozenset()


def test_choosing_alerts_replaces_the_earlier_choice(db):
    link(db)
    prefs = AlertPrefStore(db)
    prefs.replace(1, ["item_received", "game_online"])
    assert prefs.enabled(1) == {"item_received", "game_online"}
    prefs.replace(1, ["slot_offered"])
    assert prefs.enabled(1) == {"slot_offered"}
    prefs.replace(1, [])
    assert prefs.enabled(1) == frozenset()


def test_alert_choices_belong_to_one_person(db):
    link(db, 1, 10)
    link(db, 2, 20)
    prefs = AlertPrefStore(db)
    prefs.replace(1, ["hint_found"])
    assert prefs.enabled(2) == frozenset()


def test_deleting_a_link_deletes_the_alert_choices_with_it(db):
    link(db, 1, 10)
    link(db, 2, 20)
    prefs = AlertPrefStore(db)
    prefs.replace(1, ["hint_found", "slot_offered"])
    prefs.replace(2, ["game_online"])
    assert LinkStore(db).delete(1) is True
    assert prefs.enabled(1) == frozenset()
    assert db.query_all("SELECT * FROM alert_prefs WHERE discord_user_id = 1") == []
    assert prefs.enabled(2) == {"game_online"}


def test_alerts_cannot_be_stored_for_someone_who_is_not_linked_or_for_an_unknown_kind(db):
    link(db)
    prefs = AlertPrefStore(db)
    with pytest.raises(sqlite3.IntegrityError):
        prefs.replace(99, ["hint_found"])
    with pytest.raises(sqlite3.IntegrityError):
        prefs.replace(1, ["nonsense"])
    assert prefs.enabled(1) == frozenset()


def test_a_failing_replace_keeps_the_old_choice(db):
    link(db)
    prefs = AlertPrefStore(db)
    prefs.replace(1, ["hint_found"])
    with pytest.raises(sqlite3.IntegrityError):
        prefs.replace(1, ["game_online", "nonsense"])
    assert prefs.enabled(1) == {"hint_found"}


# --- overview and events -----------------------------------------------------------------------------------------------
def test_the_overview_message_is_stored_per_server(db):
    store = OverviewStore(db)
    assert store.get(1) is None
    store.save(1, 10, 20, "hash-a")
    store.save(1, 11, 21, "hash-b")
    found = store.get(1)
    assert (found.channel_id, found.message_id, found.content_hash) == (11, 21, "hash-b")


def test_scheduled_events_are_stored_by_what_they_announce(db):
    store = ScheduledEventStore(db)
    store.save(1, "game_start", 7, 500, 100.0)
    store.save(1, "signup_deadline", 7, 501, 200.0)
    store.save(1, "game_start", 7, 502, 150.0)  # the same source: replaced
    rows = {(r.source, r.source_id): (r.event_id, r.starts_at) for r in store.for_guild(1)}
    assert rows == {("game_start", 7): (502, 150.0), ("signup_deadline", 7): (501, 200.0)}
    store.delete(1, "game_start", 7)
    assert [r.source for r in store.for_guild(1)] == ["signup_deadline"]


def test_only_known_event_sources_are_accepted(db):
    with pytest.raises(sqlite3.IntegrityError):
        ScheduledEventStore(db).save(1, "birthday", 7, 500, 1.0)


# --- leaving a server --------------------------------------------------------------------------------------------------
async def test_leaving_a_server_deletes_its_live_data_only(bot):
    for guild_id in (5000, 6000):
        bot.guild_settings.update(guild_id, language="de")
        bot.spaces.create(guild_id, 7, 100, 200, DEFAULT_FEED_KINDS)
        bot._overview_store.save(guild_id, 10, 20, "hash")
        bot._event_store.save(guild_id, "game_start", 7, 500, 1.0)
        bot._reminder_store.mark_sent(guild_id, 7, 100.0, 3600)
    await bot.on_guild_remove(discord.Object(5000))
    for table in ("guild_settings", "game_spaces", "overview_messages", "scheduled_events", "signup_reminders"):
        assert bot.db.query_all(f"SELECT * FROM {table} WHERE guild_id = 5000") == [], table
        assert len(bot.db.query_all(f"SELECT * FROM {table} WHERE guild_id = 6000")) == 1, table


def test_the_friend_alerts_can_be_stored_and_every_offered_kind_has_a_label(db):
    link(db)
    prefs = AlertPrefStore(db)
    prefs.replace(1, ["friend_request", "friend_game", "slot_offered"])
    assert prefs.enabled(1) == {"friend_request", "friend_game", "slot_offered"}
    assert {"friend_request", "friend_game"} <= set(ALERT_KINDS) and set(ALERT_KINDS) <= set(ALERT_KIND_LABELS)
