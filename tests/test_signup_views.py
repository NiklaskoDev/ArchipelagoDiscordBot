import dataclasses

import pytest
from fakes import check_json, config_json, entry_json, signup_detail_json, signup_json

from apbot.manager.views import (
    parse_added_warnings,
    parse_check,
    parse_config,
    parse_configs,
    parse_signup,
    parse_signup_detail,
)


def test_a_round_in_the_list_has_its_state_and_whether_the_asker_runs_it():
    summary = parse_signup(signup_json(3, "Spring", status="closed", open=False, can_manage=True))
    assert (summary.id, summary.name, summary.status, summary.open, summary.can_manage) == (3, "Spring", "closed", False, True)
    assert (summary.entry_count, summary.user_count, summary.slots_per_user, summary.creator) == (5, 3, 2, "olga")


def test_a_round_without_the_new_fields_still_parses_with_safe_defaults():
    raw = {key: value for key, value in signup_json(3).items() if key not in ("status", "can_manage")}
    summary = parse_signup(raw)
    assert summary.status == "open" and summary.can_manage is False


def test_a_detail_has_the_own_entries_with_what_the_manager_sent():
    own = entry_json(1, "alice.yaml", games=["Timespinner", "Hollow Knight"], slots=2, issues=["Another entry also uses the name Alice"],
                     generation_error="Player names must be unique")
    raw = signup_detail_json(3, entries=[own, entry_json(2, mine=False, name=None, username="bob")],
                             description="About it", allowed_games=["Timespinner"], my_slot_count=2)
    detail = parse_signup_detail(raw)
    assert (detail.description, detail.allowed_games, detail.my_slot_count) == ("About it", ("Timespinner",), 2)
    first, second = detail.entries
    assert (first.id, first.mine, first.name, first.config_id, first.slots) == (1, True, "alice.yaml", 41, 2)
    assert first.games == ("Timespinner", "Hollow Knight") and first.issues == ("Another entry also uses the name Alice",)
    assert first.generation_error == "Player names must be unique"
    assert (second.mine, second.name, second.config_id, second.generation_error) == (False, None, None, None)


def test_the_test_result_is_there_for_organizers_and_absent_for_others():
    assert parse_signup_detail(signup_detail_json(3)).test is None
    raw = signup_detail_json(3, test={"job_id": 9, "status": "failed", "error": "boom", "current": False, "queue_position": None})
    test = parse_signup_detail(raw).test
    assert (test.status, test.current, test.error) == ("failed", False, "boom")


@pytest.mark.parametrize("junk", [None, "x", [], {"status": ""}, {"nothing": 1}])
def test_a_test_result_that_is_not_one_is_dropped(junk):
    assert parse_signup_detail(signup_detail_json(3, test=junk)).test is None


def test_entries_without_an_id_are_dropped_not_fatal():
    raw = signup_detail_json(3, entries=[entry_json(1), {"name": "no id"}, "junk", None])
    assert [e.id for e in parse_signup_detail(raw).entries] == [1]


def test_a_detail_that_is_not_a_round_is_an_error():
    with pytest.raises(ValueError):
        parse_signup_detail({"name": "no id"})


def test_nothing_the_bot_must_not_show_has_a_field():
    """Job ids, the options, the mystery weights and the other entries' owners' ids stay out of the parsed values."""
    raw = signup_detail_json(3, job_id=77, options={"spoiler": 3}, mystery_weights="secret weights", game_id=12,
                             entries=[entry_json(1, user_id=999, player_names=["Alice"])])
    detail = parse_signup_detail(raw)
    fields = {f.name for f in dataclasses.fields(detail)} | {f.name for f in dataclasses.fields(detail.entries[0])}
    assert not fields & {"job_id", "options", "mystery_weights", "game_id", "user_id", "player_names"}
    text = repr(detail)
    assert "secret weights" not in text and "999" not in text


def test_the_warnings_that_come_with_an_entry():
    assert parse_added_warnings({"warnings": ["Not installed", "", 5, "Second"]}) == ("Not installed", "Second")
    assert parse_added_warnings({}) == () and parse_added_warnings(None) == () and parse_added_warnings([]) == ()


def test_a_saved_config_has_an_id_a_name_and_games():
    config = parse_config(config_json(40, "alice.yaml"))
    assert (config.id, config.name, config.games) == (40, "alice.yaml", ("Timespinner",))
    with pytest.raises(ValueError):
        parse_config({"name": "no id"})


def test_the_list_of_configs_skips_what_has_no_id():
    assert [c.id for c in parse_configs([config_json(1), {"name": "x"}, config_json(2)])] == [1, 2]
    with pytest.raises(TypeError):
        parse_configs({"not": "a list"})


def test_a_check_result_has_the_verdict_the_problems_and_the_players():
    raw = check_json(
        False, errors=["Bad file"], warnings=["Careful"], missing_roms=["Zelda"],
        players=[{"name": "Alice", "games": ["Timespinner"], "errors": ["Unknown option"], "warnings": ["Ignored"]}],
    )
    result = parse_check(raw)
    assert (result.ok, result.errors, result.warnings, result.missing_roms) == (False, ("Bad file",), ("Careful",), ("Zelda",))
    [player] = result.players
    assert (player.name, player.games, player.errors, player.warnings) == ("Alice", ("Timespinner",), ("Unknown option",), ("Ignored",))


@pytest.mark.parametrize("junk", [None, [], "ok", {"errors": []}, {"ok": "yes"}, {"ok": 1}])
def test_a_check_answer_without_a_verdict_is_an_error_never_a_pass(junk):
    with pytest.raises(ValueError):
        parse_check(junk)
