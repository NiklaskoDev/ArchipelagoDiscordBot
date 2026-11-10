import dataclasses

import pytest
from fakes import SECRETS, activity_json, entry, game_json, hint_json, tracker_json

from apbot.manager.views import (
    FEED_FIELDS,
    FeedEntry,
    UserGame,
    parse_activity,
    parse_entry,
    parse_game_summary,
    parse_hints,
    parse_user_games,
)


# --- the activity feed -------------------------------------------------------------------------------------------------
def test_every_kind_of_the_managers_feed_is_known_with_its_fields():
    assert set(FEED_FIELDS) == {
        "goal", "release", "collect", "join", "part", "server", "hint", "item", "chat", "status", "handover",
        "handover_done",
    }


def test_an_entry_keeps_only_the_fields_of_its_kind():
    parsed = parse_entry(entry(3, "chat", player="Alice", text="hello", password="room-secret", extra="x"))
    assert parsed == FeedEntry(seq=3, at=1_700_000_003.0, kind="chat", fields={"player": "Alice", "text": "hello"})
    assert "room-secret" not in str(parsed)


@pytest.mark.parametrize(
    "raw",
    [
        {"seq": 1, "kind": "teleport", "player": "A"},  # a kind the bot does not know
        {"kind": "goal", "player": "A"},  # no number
        {"seq": -1, "kind": "goal", "player": "A"},
        {"seq": True, "kind": "goal", "player": "A"},
        {"seq": "1", "kind": "goal", "player": "A"},
        "text",
        None,
    ],
)
def test_a_broken_entry_is_dropped(raw):
    assert parse_entry(raw) is None


def test_fields_that_are_not_text_or_are_empty_are_left_out():
    parsed = parse_entry(entry(1, "status", player="Alice", status=None, note=""))
    assert parsed.fields == {"player": "Alice"}
    assert parsed.get("status") == "" and parsed.get("note") == ""


def test_a_feed_page_has_its_entries_in_order_and_the_last_number():
    page = parse_activity(
        activity_json([entry(9, "goal", player="B"), entry(7, "goal", player="A"), {"seq": 8, "kind": "nope"}], last=9)
    )
    assert [e.seq for e in page.entries] == [7, 9]
    assert page.last == 9


@pytest.mark.parametrize("raw", [[], {"entries": "x", "last": 1}, {"entries": [], "last": -1}, {"entries": []}, None])
def test_an_answer_that_is_not_a_feed_is_refused(raw):
    with pytest.raises(ValueError):
        parse_activity(raw)


# --- a person's games --------------------------------------------------------------------------------------------------
def test_a_users_game_has_their_slots_and_the_offers_made_to_them():
    raw = game_json(
        7, "Friday Run", state="running", finished_at=None,
        my_slots=[{"slot": 1, "name": "Alice", "game": "Timespinner", "idle_since": None}],
        offered_slots=[
            {"slot": 2, "name": "Bob", "game": "Hollow Knight", "at": 1.0, "from": "bob", "to": "alice",
             "to_me": True, "note": "no time", "can_accept": True},
            {"slot": 3, "name": "Cy", "game": "Zelda", "from": "cy", "to": None, "to_me": False, "note": None},
        ],
    )
    [game] = parse_user_games([raw])
    assert (game.id, game.name, game.state, game.finished_at) == (7, "Friday Run", "running", None)
    assert [(s.slot, s.name) for s in game.my_slots] == [(1, "Alice")]
    first, second = game.offers
    assert (first.slot, first.name, first.game, first.owner, first.note, first.to_me) == (
        2, "Bob", "Hollow Knight", "bob", "no time", True,
    )
    assert (second.owner, second.note, second.to_me) == ("cy", None, False)


def test_a_users_game_never_holds_a_secret():
    [game] = parse_user_games([game_json(7)])
    assert isinstance(game, UserGame)
    shown = str(dataclasses.asdict(game))
    assert not any(isinstance(value, str) and value in shown for value in SECRETS.values())


def test_broken_slots_and_games_are_skipped():
    raw = game_json(7, my_slots=[{"name": "no number"}, "x", {"slot": 4, "name": "Dan"}], offered_slots=[{"x": 1}])
    [game] = parse_user_games([raw, {"name": "no id"}])
    assert [s.slot for s in game.my_slots] == [4] and game.offers == ()


def test_a_list_of_games_that_is_not_a_list_is_refused():
    with pytest.raises(TypeError):
        parse_user_games({"games": []})


# --- visibility --------------------------------------------------------------------------------------------------------
def test_a_game_says_who_may_see_it():
    assert parse_game_summary(game_json(1, visibility="all")).visibility == "all"
    assert parse_game_summary(game_json(1, visibility="participants")).visibility == "participants"
    assert parse_game_summary({"id": 1}).visibility is None


# --- a tracker's hints -------------------------------------------------------------------------------------------------
def test_hints_are_read_from_a_tracker():
    tracker = tracker_json(
        [
            hint_json("Sword", "Alice", "Bob", "Chest"),
            hint_json("Bow", "Bob", "Alice", "Cave", for_me=False, in_my_world=True, found=True),
            "not a hint",
        ]
    )
    sword, bow = parse_hints(tracker)
    assert (sword.item, sword.receiver, sword.finder, sword.location) == ("Sword", "Alice", "Bob", "Chest")
    assert (sword.for_me, sword.in_my_world, sword.found) == (True, False, False)
    assert (bow.for_me, bow.in_my_world, bow.found) == (False, True, True)


@pytest.mark.parametrize("raw", [{}, {"hints": "x"}, [], None])
def test_an_answer_that_is_not_a_tracker_is_refused(raw):
    with pytest.raises(ValueError):
        parse_hints(raw)
