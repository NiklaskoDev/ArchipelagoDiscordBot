import json

import discord
from fakes import SECRETS, SITE_URL, game_json, json_text, signup_json, slot_json

from apbot.manager.views import listed_games, parse_game_detail, parse_games, parse_signups
from apbot.ui.embeds import error_embed, game_detail_embeds, games_list_embed, signups_embed
from apbot.ui.format import ZERO_WIDTH_SPACE
from apbot.ui.style import DESCRIPTION, ERROR, FIELD_VALUE, FIELDS
from apbot.ui.views import manager_page_url, open_in_manager_view


def games(count: int, name: str | None = None, **overrides):
    raw = [game_json(i, name or f"Game {i}", **overrides) for i in range(1, count + 1)]
    return listed_games(parse_games(raw))


def total_length(embeds) -> int:
    return sum(len(embed) for embed in embeds)


# --- /games ------------------------------------------------------------------------------------------------------------
def test_empty_games_list_says_what_to_do():
    embed = games_list_embed([], "en")
    assert embed.description == "No games are running. Organizers can add one in the manager."


def test_one_game_line_has_name_state_worlds_players_goals():
    embed = games_list_embed(games(1, goal_count=1), "en")
    assert embed.description == "**Game 1** — Running — Worlds 2 — Players 1/2 — Goals 1/2"
    assert "more" not in embed.description


def test_forty_games_are_cut_to_25_lines_and_say_how_many_are_missing():
    embed = games_list_embed(games(40), "en")
    lines = embed.description.split("\n")
    assert len(lines) == 26
    assert lines[-1] == "…and 15 more"
    assert len(embed) <= 6000
    assert len(embed.description) <= DESCRIPTION


def test_a_list_of_very_long_names_still_fits_discords_limits():
    long_name = "*" * 300  # every character is escaped, which doubles it
    embed = games_list_embed(games(25, name=long_name), "en")
    assert len(embed.description) <= DESCRIPTION
    assert len(embed) <= 6000
    assert "more" in embed.description.split("\n")[-1]


def test_game_list_is_localized():
    embed = games_list_embed(games(1), "de")
    assert embed.title == "Spiele"
    assert "Läuft" in embed.description and "Welten 2" in embed.description


def test_names_cannot_inject_markdown_or_pings():
    parsed = parse_games([game_json(1, "*bold* @everyone <@12345>")])
    text = games_list_embed(parsed, "en").description
    assert "@everyone" not in text and "<@12345>" not in text
    assert r"\*bold\*" in text
    assert ZERO_WIDTH_SPACE in text


# --- /game -------------------------------------------------------------------------------------------------------------
def detail(**overrides):
    return parse_game_detail(game_json(**overrides))


def test_game_detail_has_the_facts():
    embeds = game_detail_embeds(detail(start={"at": 1_800_000_000.0}), "en")
    info = embeds[0]
    assert info.title == "Friday Run"
    assert info.description == "Running"
    fields = {f.name: f.value for f in info.fields}
    assert fields["Address"] == "ap.example:38281"
    assert fields["Archipelago version"] == "0.6.8"
    assert fields["Planned start"] == "<t:1800000000:F>\n<t:1800000000:R>"
    assert fields["Progress"] == "40/200 (20%)"
    assert fields["Goals"] == "0/2"
    assert fields["Players"] == "1/2 claimed"


def test_tls_game_says_encrypted():
    info = game_detail_embeds(detail(tls=True), "en")[0]
    assert {f.name: f.value for f in info.fields}["Address"] == "ap.example:38281 — encrypted (wss)"
    plain = game_detail_embeds(detail(tls=False), "en")[0]
    assert "encrypted" not in json_text(plain)


def test_game_without_address_or_planned_start_has_no_such_field():
    info = game_detail_embeds(detail(address=None, start=None), "en")[0]
    names = [f.name for f in info.fields]
    assert "Address" not in names and "Planned start" not in names


def test_slot_lines_show_world_owner_progress_and_status():
    slots = [
        slot_json(1, "Alice", "Timespinner", player_status="stuck"),
        slot_json(2, "Bob", "Hollow Knight", owner=None, user_id=None, checked=0, status="offline"),
        slot_json(3, "Cara", "Celeste", owner="cara", checked=100, status="goal"),
    ]
    embed = game_detail_embeds(detail(slots=slots), "en")[1]
    lines = embed.fields[0].value.split("\n")
    assert lines[0] == "**Alice** — Timespinner — alice — 40/100 — Online — Stuck"
    assert lines[1] == "**Bob** — Hollow Knight — Unclaimed — 0/100 — Offline"
    assert lines[2] == "**Cara** — Celeste — cara — 100/100 — Goal reached"


def test_finished_game_shows_its_outcome():
    info = game_detail_embeds(detail(state="stopped", finished_at=1_700_500_000.0, outcome="finished"), "en")[0]
    assert info.description == "Finished — <t:1700500000:R>"


def test_state_colour_carries_meaning():
    assert game_detail_embeds(detail(state="crashed"), "en")[0].colour == ERROR
    assert game_detail_embeds(detail(state="running"), "en")[0].colour != ERROR


def test_forty_slots_are_split_over_fields_of_at_most_1024_characters():
    slots = [slot_json(i, f"Player {i}", "Timespinner") for i in range(1, 41)]
    embeds = game_detail_embeds(detail(slots=slots), "en")
    slot_embed = embeds[1]
    assert len(slot_embed.fields) > 1
    assert all(len(f.value) <= FIELD_VALUE for f in slot_embed.fields)
    assert len(slot_embed.fields) <= FIELDS
    assert total_length(embeds) <= 6000
    shown = sum(len(f.value.split("\n")) for f in slot_embed.fields)
    footer = slot_embed.footer.text if slot_embed.footer else None
    assert (shown == 40) == (footer is None)


def test_hundreds_of_long_slot_lines_stay_inside_6000_characters_and_report_the_rest():
    slots = [slot_json(i, "N" * 80, "W" * 80, owner="O" * 80) for i in range(1, 301)]
    embeds = game_detail_embeds(detail(slots=slots), "en")
    assert total_length(embeds) <= 6000
    assert len(embeds[1].fields) <= FIELDS
    shown = sum(len(f.value.split("\n")) for f in embeds[1].fields)
    assert embeds[1].footer.text == f"…and {300 - shown} more"


def test_player_names_in_slot_lines_are_escaped():
    slots = [slot_json(1, "@everyone *x*", "Game", owner="<@999>")]
    text = json_text(game_detail_embeds(detail(slots=slots), "en"))
    assert "@everyone" not in text and "<@999>" not in text


def test_no_secret_reaches_any_embed():
    parsed = parse_game_detail(game_json(start={"at": 1_800_000_000.0}, tls=True))
    text = json_text(game_detail_embeds(parsed, "en") + game_detail_embeds(parsed, "de"))
    text += json_text(games_list_embed(listed_games([parsed]), "en"))
    for secret in (SECRETS["password"], SECRETS["server_password"], SECRETS["notes"], "webhook-token", "spectate-token"):
        assert secret not in text


def test_game_detail_is_localized():
    info, slots = game_detail_embeds(detail(tls=True), "fr")
    assert info.description == "En cours"
    assert "chiffré (wss)" in json_text(info)
    assert slots.fields[0].name == "Slots"


# --- the link button ---------------------------------------------------------------------------------------------------
def test_open_in_manager_button_links_to_the_game_page():
    view = open_in_manager_view(SITE_URL + "/", "/games/12", "en")
    [button] = view.children
    assert isinstance(button, discord.ui.Button)
    assert button.url == f"{SITE_URL}/#/games/12"
    assert button.label == "Open in manager"
    assert button.style == discord.ButtonStyle.link


def test_no_site_url_means_no_button():
    assert open_in_manager_view(None, "/games/1", "en") is None
    assert open_in_manager_view("", "/games/1", "en") is None


def test_site_url_that_is_not_http_is_not_used():
    assert manager_page_url("javascript:alert(1)", "/games/1") is None


def test_button_label_is_localized():
    [button] = open_in_manager_view(SITE_URL, "/games/1", "nl").children
    assert button.label == "Openen in manager"


# --- /signups ----------------------------------------------------------------------------------------------------------
def test_signup_line_has_deadline_entries_players_slots_and_creator():
    embed = signups_embed(parse_signups([signup_json(1, "Spring Async")]), "en")
    assert embed.description == (
        "**Spring Async** — Closes <t:1800000000:R> — Entries 5 — Players 3 — Slots per player 2 — by olga"
    )


def test_signup_without_deadline_or_creator():
    embed = signups_embed(parse_signups([signup_json(1, deadline=None, creator=None)]), "en")
    assert "No deadline" in embed.description
    assert "by " not in embed.description


def test_no_open_signups_says_what_to_do():
    assert signups_embed([], "en").description == "No sign-up rounds are open. Organizers can open one in the manager."


def test_forty_signups_are_cut_with_a_count():
    rounds = parse_signups([signup_json(i, f"Round {i}") for i in range(1, 41)])
    embed = signups_embed(rounds, "en")
    assert embed.description.split("\n")[-1] == "…and 15 more"
    assert len(embed) <= 6000


def test_signups_are_localized():
    embed = signups_embed(parse_signups([signup_json(1)]), "de")
    assert embed.title == "Anmeldungen"
    assert "Schließt" in embed.description and "von olga" in embed.description


# --- errors ------------------------------------------------------------------------------------------------------------
def test_error_embed_is_plain_text_in_the_error_colour():
    embed = error_embed("It failed")
    assert embed.description == "It failed" and embed.colour == ERROR
    assert json.loads(json_text(embed))[0]["description"] == "It failed"
