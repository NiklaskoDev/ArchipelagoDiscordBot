import discord
import pytest
from fakes import check_json, entry_json, json_text, signup_detail_json

from apbot.manager.views import parse_check, parse_signup_detail
from apbot.ui.signups import (
    check_result_embed,
    entry_added_embed,
    signup_detail_embed,
    signup_reminder_embed,
)
from apbot.ui.style import ACCENT, ERROR, SUCCESS, WARNING

DEADLINE = 1_800_000_000.0


def detail(name="Spring Async", **overrides):
    return parse_signup_detail(signup_detail_json(3, name, **{"deadline": DEADLINE, **overrides}))


def fields(embed: discord.Embed) -> dict[str, str]:
    return {field.name: field.value for field in embed.fields}


def within_limits(*embeds: discord.Embed) -> bool:
    return sum(len(e) for e in embeds) <= 6000 and all(
        len(e.fields) <= 25 and all(len(f.value) <= 1024 for f in e.fields) and len(e.description or "") <= 4096
        for e in embeds
    )


# --- one round ---------------------------------------------------------------------------------------------------------
def test_a_round_shows_its_state_deadline_counts_and_the_askers_slots():
    embed = signup_detail_embed(detail(my_slot_count=1, description="Bring a game"), "en")
    assert embed.title == "Spring Async" and embed.colour == ACCENT
    assert embed.description == "Open\nBring a game"
    assert fields(embed)["Deadline"] == f"<t:{int(DEADLINE)}:F>\n<t:{int(DEADLINE)}:R>"
    assert (fields(embed)["Entries"], fields(embed)["Players"], fields(embed)["Your slots"]) == ("5", "3", "1 of 2")
    assert fields(embed)["Opened by"] == "olga"


def test_a_round_without_a_deadline_says_so_and_a_closed_one_says_closed():
    embed = signup_detail_embed(detail(deadline=None, status="closed", open=False), "en")
    assert fields(embed)["Deadline"] == "No deadline" and embed.description == "Closed"


def test_the_allowed_games_and_a_mystery_round_are_shown():
    embed = signup_detail_embed(detail(allowed_games=["Timespinner", "Hollow Knight"], mystery=True), "en")
    assert fields(embed)["Games"] == "Timespinner, Hollow Knight"
    assert "Mystery round" in embed.description


def test_with_no_entries_the_embed_says_so():
    assert fields(signup_detail_embed(detail(), "en"))["Your entries"] == "You have not entered anything yet"


def test_only_the_askers_own_entries_are_listed_other_players_entries_are_only_counted():
    entries = [
        entry_json(1, "alice.yaml", games=["Timespinner"], slots=1),
        entry_json(2, mine=False, name=None, username="bob", games=["Zelda"]),
    ]
    embed = signup_detail_embed(detail(entries=entries), "en")
    text = json_text(embed)
    assert fields(embed)["Your entries"] == "**alice.yaml** — Timespinner — 1 slot"
    assert "bob" not in text and "Zelda" not in text


def test_an_entry_shows_its_issues_and_what_the_generator_said():
    entry = entry_json(
        1, "alice.yaml", slots=2, issues=["Another entry also uses the name Alice", "Not allowed in this round: Zelda"],
        generation_error="Unknown option goal",
    )
    value = fields(signup_detail_embed(detail(entries=[entry]), "en"))["Your entries"]
    assert value.splitlines() == [
        "**alice.yaml** — Timespinner — 2 slots",
        "Problem: Another entry also uses the name Alice",
        "Problem: Not allowed in this round: Zelda",
        "The generator said: Unknown option goal",
    ]


def test_an_organizer_sees_the_test_and_what_the_generator_said_about_other_players_entries():
    entries = [
        entry_json(1, "alice.yaml", mine=True),
        entry_json(2, "bob.yaml", mine=False, username="bob", generation_error="Item name unknown"),
    ]
    test = {"status": "failed", "error": "Generation failed", "current": True}
    embed = signup_detail_embed(detail(entries=entries, test=test, error="Last try failed"), "en")
    assert fields(embed)["Test"].splitlines() == ["Failed", "Generation failed", "**bob.yaml** (bob): Item name unknown"]
    assert fields(embed)["Last generation"] == "Last try failed"
    assert "bob.yaml" not in fields(embed)["Your entries"]


def test_a_test_that_does_not_match_the_entries_any_more_says_so_only_when_it_is_over():
    done = signup_detail_embed(detail(test={"status": "succeeded", "current": False}), "en")
    assert fields(done)["Test"] == "Succeeded — the entries changed since"
    running = signup_detail_embed(detail(test={"status": "running", "current": False}), "en")
    assert fields(running)["Test"] == "Running"
    queued = signup_detail_embed(detail(test={"status": "queued", "current": True}), "en")
    assert fields(queued)["Test"] == "Waiting in the queue"


def test_without_a_test_there_is_no_test_field():
    assert "Test" not in fields(signup_detail_embed(detail(), "en"))


def test_everything_from_players_is_escaped_and_cannot_ping():
    entry = entry_json(1, "*bold*.yaml", issues=["@everyone <@123> `x`"], generation_error="**boom**")
    embed = signup_detail_embed(detail(entries=[entry], description="@everyone", name="[x](http://evil)"), "en")
    text = json_text(embed)
    assert "@everyone" not in text.replace("@​", "") and "<@123>" not in text
    assert "\\\\*bold\\\\*" in text and "\\\\*\\\\*boom\\\\*\\\\*" in text


def test_a_round_full_of_long_entries_stays_inside_discords_limits():
    entries = [
        entry_json(i, "x" * 200, games=["Game " * 40], issues=["problem " * 50] * 5, generation_error="error " * 80)
        for i in range(1, 30)
    ]
    test = {"status": "failed", "error": "e" * 3000, "current": True}
    others = [entry_json(100 + i, "y" * 200, mine=False, username="u" * 80, generation_error="z" * 900) for i in range(30)]
    embed = signup_detail_embed(detail(entries=entries + others, test=test, error="g" * 3000, description="d" * 3000), "en")
    assert within_limits(embed)
    assert "…and" in fields(embed)["Your entries"]


def test_the_round_is_shown_in_german():
    embed = signup_detail_embed(detail(status="closed", open=False), "de")
    assert embed.description == "Geschlossen" and "Frist" in fields(embed) and "Deine Slots" in fields(embed)


# --- the check of a config ----------------------------------------------------------------------------------------------
def test_a_config_that_passes_is_green_and_lists_its_players():
    embed = check_result_embed(parse_check(check_json(True)), "alice.yaml", "en")
    assert embed.colour == SUCCESS and embed.title == "Check of alice.yaml"
    assert embed.description == "The config passed the check"
    assert fields(embed) == {"Players": "**Alice** — Timespinner"}


def test_a_config_with_problems_is_red_and_says_nothing_was_saved():
    result = parse_check(check_json(
        False, errors=["Not a mapping"], warnings=["Careful"], missing_roms=["Zelda", "Metroid"],
        players=[{"name": "Alice", "games": ["Timespinner"], "errors": ["Unknown option"], "warnings": ["Ignored key"]}],
    ))
    embed = check_result_embed(result, "alice.yaml", "en")
    assert embed.colour == ERROR and "Nothing was saved or entered" in embed.description
    assert fields(embed)["Problems"].splitlines() == ["Not a mapping", "**Alice**: Unknown option"]
    assert fields(embed)["Warnings"].splitlines() == ["Careful", "**Alice**: Ignored key"]
    assert fields(embed)["Base ROM missing on the server"] == "These games need it to generate: Zelda, Metroid"


def test_a_player_without_a_game_and_without_name_still_shows():
    result = parse_check(check_json(True, players=[{"games": [], "errors": [], "warnings": []}]))
    assert fields(check_result_embed(result, "a.yaml", "en"))["Players"] == "**?** — no game"


def test_the_check_embed_escapes_the_file_name_and_what_archipelago_said():
    result = parse_check(check_json(False, errors=["@everyone *x*"]))
    text = json_text(check_result_embed(result, "@here_*.yaml", "en"))
    assert "@here" not in text.replace("@​", "") and "@everyone" not in text.replace("@​", "")


def test_a_check_with_hundreds_of_findings_stays_inside_the_limits_and_counts_what_it_left_out():
    result = parse_check(check_json(
        False, errors=["error " * 40] * 100, warnings=["warning " * 40] * 100,
        players=[{"name": f"P{i}", "games": ["G" * 90], "errors": ["e" * 300], "warnings": ["w" * 300]} for i in range(60)],
        missing_roms=["Game " * 50] * 30,
    ))
    embed = check_result_embed(result, "x" * 300, "en")
    assert within_limits(embed)
    assert "more" in fields(embed)["Problems"]


# --- an entry was made --------------------------------------------------------------------------------------------------
def test_entering_a_config_says_how_many_slots_are_used():
    after = detail(entries=[entry_json(1, "alice.yaml")], my_slot_count=1)
    embed = entry_added_embed(after.entries[0], after, [], "en")
    assert embed.colour == SUCCESS and embed.fields == []
    assert embed.description == "Entered **alice.yaml** in **Spring Async**. You use 1 of 2 slots."


def test_warnings_of_the_manager_turn_the_embed_amber_and_are_listed():
    after = detail(entries=[entry_json(1, "alice.yaml")], my_slot_count=1)
    embed = entry_added_embed(after.entries[0], after, ["Not installed on this server yet: Zelda."], "en")
    assert embed.colour == WARNING and fields(embed)["Warnings"] == "Not installed on this server yet: Zelda."


def test_an_entry_the_manager_did_not_name_is_shown_with_a_question_mark_not_an_error():
    embed = entry_added_embed(None, detail(), [], "en")
    assert embed.description.startswith("Entered **?** in")


# --- the reminder -------------------------------------------------------------------------------------------------------
def test_the_reminder_names_the_round_and_when_it_closes():
    embed = signup_reminder_embed(parse_signup_detail(signup_detail_json(3, "Spring Async", deadline=DEADLINE)), "en")
    assert embed.colour == WARNING and embed.title == "Sign-ups for Spring Async close soon"
    assert embed.description == f"Sign-ups close <t:{int(DEADLINE)}:R> (<t:{int(DEADLINE)}:F>)."
    assert fields(embed) == {"Entries": "5", "Players": "3", "Slots per player": "2"}


@pytest.mark.parametrize("lang", ["de", "fr", "nl"])
def test_the_reminder_is_translated(lang):
    embed = signup_reminder_embed(parse_signup_detail(signup_detail_json(3, "Spring Async", deadline=DEADLINE)), lang)
    assert "Spring Async" in embed.title and embed.title != "Sign-ups for Spring Async close soon"
