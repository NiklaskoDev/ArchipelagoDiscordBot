import dataclasses
import json

import pytest
from fakes import SECRETS, friend_json, friends_json, game_json, job_json, signup_json, slot_json

from apbot.manager.views import (
    JOB_FINISHED,
    FriendsOverview,
    FriendView,
    GameDetail,
    GameSummary,
    JobPlayer,
    JobView,
    SignupSummary,
    SlotView,
    listed_games,
    open_signups,
    parse_friends,
    parse_game_detail,
    parse_game_summary,
    parse_games,
    parse_job,
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
        "ap_version", "start_at", "created_at", "finished_at", "outcome", "visibility",
        "can_manage", "can_archive", "return_blocked",
    }
    assert {f.name for f in dataclasses.fields(GameSummary)} == summary
    assert {f.name for f in dataclasses.fields(GameDetail)} == summary | {"slots", "checked", "total"}


# --- what the account may do with a game, and generation jobs ----------------------------------------------------------
def test_a_game_carries_what_the_asking_account_may_do_with_it():
    game = parse_game_summary(game_json(can_manage=True, can_archive=True, return_blocked="version"))
    assert (game.can_manage, game.can_archive, game.return_blocked) == (True, True, "version")
    detail = parse_game_detail(game_json(can_manage=True, can_archive=True, return_blocked="finished"))
    assert (detail.can_manage, detail.can_archive, detail.return_blocked) == (True, True, "finished")


def test_what_the_account_may_do_defaults_to_nothing():
    game = parse_game_summary({"id": 1})
    assert (game.can_manage, game.can_archive, game.return_blocked) == (False, False, None)
    plain = parse_game_summary(game_json())  # the manager sends return_blocked: false when nothing blocks it
    assert (plain.can_manage, plain.can_archive, plain.return_blocked) == (False, False, None)


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed", "cancelled"])
def test_every_job_status_is_kept(status):
    assert parse_job(job_json(status=status)).status == status


def test_an_unknown_job_status_is_kept_as_text_and_a_missing_one_counts_as_queued():
    assert parse_job(job_json(status="paused")).status == "paused"
    assert parse_job({"id": 3}).status == "queued"


def test_a_job_has_its_values_and_players():
    job = parse_job(
        job_json(
            9,
            "running",
            name="Friday",
            game_id=5,
            error="Boom",
            queue_position=2,
            host_after=False,
            players=[
                {"filename": "alice.yaml", "user_id": 10, "owner": "alice", "config_id": 40},
                {"filename": "gone.yaml", "user_id": None, "owner": None, "config_id": None},
                "junk",
                {"user_id": "x"},
            ],
        )
    )
    assert job == JobView(
        id=9, name="Friday", status="running", game_id=5, error="Boom", queue_position=2, host_after=False,
        players=(
            JobPlayer(user_id=10, owner="alice", config_id=40, filename="alice.yaml"),
            JobPlayer(user_id=None, owner=None, config_id=None, filename="gone.yaml"),
            JobPlayer(user_id=None, owner=None, config_id=None, filename="?"),
        ),
    )


def test_a_job_without_optional_values_has_none_and_no_players():
    job = parse_job({"id": 3, "status": "queued", "players": "nope"})
    assert (job.name, job.game_id, job.error, job.queue_position, job.host_after, job.players) == (
        None, None, None, None, False, (),
    )


@pytest.mark.parametrize("raw", [{}, {"id": -1}, {"id": "3"}, {"id": True}, None, [], "job"])
def test_an_answer_that_is_not_a_job_is_refused(raw):
    with pytest.raises(ValueError):
        parse_job(raw)


def test_a_job_is_over_when_it_succeeded_failed_or_was_cancelled():
    assert JOB_FINISHED == {"succeeded", "failed", "cancelled"}


def test_a_job_view_holds_no_log_options_or_meta():
    assert {f.name for f in dataclasses.fields(JobView)} == {
        "id", "name", "status", "game_id", "error", "queue_position", "host_after", "players",
    }
    assert {f.name for f in dataclasses.fields(JobPlayer)} == {"user_id", "owner", "config_id", "filename"}


# --- friends -----------------------------------------------------------------------------------------------------------
def test_the_three_friend_lists_are_read_and_the_suggestions_are_ignored():
    overview = parse_friends(
        friends_json(
            friends=[friend_json(20, "bob", since=1_700_000_100.0, shared_configs=2, games_together=1)],
            incoming=[friend_json(21, "cy", asked_at=1_700_000_200.0)],
            outgoing=[friend_json(22, "dee", asked_at=1_700_000_300.0)],
            suggestions=[friend_json(23, "eve", games_together=4)],
        )
    )
    assert overview.friends == (FriendView(20, "bob", 1_700_000_100.0, 2, 1),)
    assert overview.incoming == (FriendView(21, "cy", 1_700_000_200.0),)
    assert overview.outgoing == (FriendView(22, "dee", 1_700_000_300.0),)
    assert not overview.is_empty
    assert not any("eve" in repr(person) for person in (*overview.friends, *overview.incoming, *overview.outgoing))


def test_an_empty_friend_list_is_empty():
    assert parse_friends(friends_json()).is_empty


def test_people_without_an_id_or_a_name_are_skipped_and_odd_values_get_defaults():
    overview = parse_friends(
        friends_json(
            friends=[
                {"username": "no id"}, {"id": 5}, {"id": 6, "username": ""}, "bob", None, {"id": True, "username": "x"},
                {"id": 7, "username": "ok", "since": "yesterday", "shared_configs": "many"},
            ]
        )
    )
    assert overview.friends == (FriendView(7, "ok", None, 0, 0),)


@pytest.mark.parametrize(
    "raw",
    [
        None, [], "friends", {}, {"friends": [], "incoming": []}, {"friends": [], "incoming": [], "outgoing": "none"},
        {"friends": {}, "incoming": [], "outgoing": []},
    ],
)
def test_an_answer_that_is_not_a_friend_list_is_refused(raw):
    with pytest.raises(ValueError):
        parse_friends(raw)


def test_friend_views_hold_only_names_and_numbers():
    assert {f.name for f in dataclasses.fields(FriendView)} == {"id", "username", "at", "shared_configs", "games_together"}
    assert {f.name for f in dataclasses.fields(FriendsOverview)} == {"friends", "incoming", "outgoing"}
