import dataclasses
import json

import pytest
from fakes import SECRETS, game_json, signup_json, slot_json

from apbot.manager.views import (
    GameDetail,
    GameSummary,
    SignupSummary,
    SlotView,
    listed_games,
    open_signups,
    parse_game_detail,
    parse_game_summary,
    parse_games,
    parse_signups,
)

FORBIDDEN_FIELDS = {"password", "server_password", "discord_webhook_url", "notes", "spectate", "has_password"}


def test_no_view_has_a_field_for_anything_sensitive():
    for view in (GameSummary, GameDetail, SlotView, SignupSummary):
        assert not FORBIDDEN_FIELDS & {field.name for field in dataclasses.fields(view)}


def test_detail_never_carries_a_secret_value():
    detail = parse_game_detail(game_json())
    text = json.dumps(dataclasses.asdict(detail))
    for value in (SECRETS["password"], SECRETS["server_password"], SECRETS["discord_webhook_url"], SECRETS["notes"]):
        assert value not in text
    assert "spectate-token" not in text


def test_game_detail_values():
    detail = parse_game_detail(
        game_json(start={"at": 1_800_000_000.0, "opens_at": 1_799_999_000.0}, tls=True, goal_count=1)
    )
    assert (detail.id, detail.name, detail.state) == (1, "Friday Run", "running")
    assert detail.world_count == 2
    assert (detail.player_count, detail.claimed_count, detail.goal_count) == (2, 1, 1)
    assert detail.address == "ap.example:38281"
    assert detail.tls is True
    assert detail.start_at == 1_800_000_000.0
    assert (detail.checked, detail.total) == (40, 200)
    assert [s.name for s in detail.slots] == ["Alice", "Bob"]
    assert detail.slots[1].owner is None


def test_only_player_slots_are_kept():
    detail = parse_game_detail(game_json(slots=[slot_json(1), slot_json(2, "Team", type=2)]))
    assert [s.name for s in detail.slots] == ["Alice"]


def test_missing_optional_values_get_neutral_defaults():
    summary = parse_game_summary({"id": 5})
    assert (summary.name, summary.state, summary.world_count) == ("?", "stopped", 0)
    assert summary.address is None and summary.start_at is None and summary.finished_at is None


def test_entries_without_an_id_are_skipped_not_fatal():
    games = parse_games([game_json(1), {"name": "no id"}, "garbage", game_json(2)])
    assert [g.id for g in games] == [1, 2]
    assert [s.id for s in parse_signups([signup_json(3), None])] == [3]


def test_list_that_is_not_a_list_is_an_error():
    with pytest.raises(TypeError):
        parse_games({"detail": "oops"})


def test_detail_that_is_not_a_game_is_an_error():
    with pytest.raises(ValueError):
        parse_game_detail({"name": "no id"})


def test_listed_games_hides_finished_and_puts_running_first_then_newest():
    games = parse_games(
        [
            game_json(1, "old stopped", state="stopped", created_at=100.0),
            game_json(2, "new stopped", state="stopped", created_at=300.0),
            game_json(3, "running", state="running", created_at=200.0),
            game_json(4, "finished", state="stopped", created_at=400.0, finished_at=500.0),
        ]
    )
    assert [g.name for g in listed_games(games)] == ["running", "new stopped", "old stopped"]


def test_open_signups_shows_open_rounds_soonest_deadline_first_and_no_deadline_last():
    rounds = parse_signups(
        [
            signup_json(1, "late", deadline=3000.0),
            signup_json(2, "closed", open=False, deadline=1000.0),
            signup_json(3, "no deadline", deadline=None),
            signup_json(4, "soon", deadline=2000.0),
        ]
    )
    assert [s.name for s in open_signups(rounds)] == ["soon", "late", "no deadline"]


def test_views_hold_exactly_the_whitelisted_fields():
    """Adding a field to a public view has to be a deliberate change: this list is the whitelist."""
    summary = {
        "id", "name", "state", "world_count", "player_count", "claimed_count", "goal_count", "address", "tls",
        "ap_version", "start_at", "created_at", "finished_at", "outcome",
    }
    assert {f.name for f in dataclasses.fields(GameSummary)} == summary
    assert {f.name for f in dataclasses.fields(GameDetail)} == summary | {"slots", "checked", "total"}
