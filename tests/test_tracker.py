import json

import discord
import pytest
from fakes import (
    BOT_TOKEN,
    USER_TOKEN,
    FakeInteraction,
    FakeMessage,
    FakeMessageable,
    game_json,
    hint_json,
    json_text,
    received_json,
    tracker_json,
)

from apbot.cogs.tracker import TrackerCog
from apbot.errors import NotLinkedError, UserFacingError
from apbot.manager.errors import Conflict
from apbot.manager.views import RECENT_PROGRESSION, parse_hints, parse_tracker
from apbot.ui.dynamic import HintStatusButton, hint_status_view
from apbot.ui.tracker import tracker_embed
from apbot.ui.views import HintPickerView, hint_picker

TRACKER = "/api/games/7/slots/2/tracker"
HINT_PATH = "/api/games/7/slots/2/hints/5/77"


def link(bot, user_id=1001, token=USER_TOKEN):
    bot.links.save(user_id, 10, "alice", bot.cipher.encrypt(token), token[-4:], "en")


def asker(bot, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, **kwargs)


def field_values(embed: discord.Embed) -> dict[str, str]:
    return {field.name: field.value for field in embed.fields}


# --- reading a tracker ------------------------------------------------------------------------------------------------
def test_a_tracker_is_reduced_to_counts_and_the_newest_progression_items():
    items = [received_json(f"Key {n}", flags=0b001) for n in range(12)] + [
        received_json("Potion", flags=0b010),
        received_json("Start item", flags=0b001, start=True, sender=None, location=None),
    ]
    locations = [{"location": f"Loc {n}", "checked": n < 3} for n in range(8)]
    tracker = parse_tracker(tracker_json(items=items, locations=locations, goal=True, hints_used=2))
    assert (tracker.name, tracker.game, tracker.goal) == ("Alice", "Timespinner", True)
    assert tracker.received == 14
    assert (tracker.checked, tracker.total) == (3, 8)
    assert len(tracker.recent_progression) == RECENT_PROGRESSION == 10
    assert tracker.recent_progression[-1] == "Start item" and tracker.recent_progression[0] == "Key 3"
    assert (tracker.hints_used, tracker.hint_points, tracker.hint_cost) == (2, 12, 5)


def test_a_tracker_without_progression_items_lists_none():
    assert parse_tracker(tracker_json(items=[received_json(flags=0)])).recent_progression == ()


@pytest.mark.parametrize("rule", ["available", "after_goal", "automatic", "disabled", "done"])
def test_the_rules_for_release_and_collect_are_kept(rule):
    tracker = parse_tracker(tracker_json(release=rule, collect=rule))
    assert (tracker.release, tracker.collect) == (rule, rule)


def test_a_rule_the_bot_does_not_know_counts_as_available_so_the_server_decides():
    tracker = parse_tracker(tracker_json(release="sometimes", collect=None))
    assert (tracker.release, tracker.collect) == ("available", "available")


def test_hints_keep_their_status_and_the_numbers_the_server_knows_them_by():
    [hint] = parse_hints(tracker_json([hint_json(status=30, finder_slot=5, location_id=77)]))
    assert (hint.status, hint.finder_slot, hint.location_id) == (30, 5, 77)


def test_hints_without_numbers_have_none_and_so_cannot_be_marked():
    raw = tracker_json([hint_json(finder_slot=None, location_id="x"), hint_json(finder_slot=-1, location_id=True)])
    assert [(h.finder_slot, h.location_id) for h in parse_hints(raw)] == [(None, None), (None, None)]


@pytest.mark.parametrize("raw", [{}, {"hints": "x"}, [], None])
def test_an_answer_that_is_not_a_tracker_is_refused(raw):
    with pytest.raises(ValueError):
        parse_tracker(raw)


def test_a_tracker_with_broken_lists_is_still_read():
    tracker = parse_tracker({"hints": [], "items": ["x", None, {"flags": "a"}], "locations": [1, {"checked": True}]})
    assert (tracker.received, tracker.checked, tracker.total, tracker.name, tracker.hint_points) == (1, 1, 1, "?", None)


# --- the embed --------------------------------------------------------------------------------------------------------
def build(raw=None, lang="en", **kwargs):
    return tracker_embed(parse_tracker(raw or tracker_json()), lang, **kwargs)


def sample_tracker() -> dict:
    return tracker_json(
        [
            hint_json("Sword", "Alice", "Bob", "Chest", status=30),
            hint_json("Bow", "Alice", "Cy", "Cave", status=20),
            hint_json("Gone", "Alice", "Bob", "Old", found=True),
            hint_json("Shield", "Cy", "Alice", "Tower", for_me=False, in_my_world=True),
            hint_json("Mine", "Alice", "Alice", "Home", in_my_world=True),
        ],
        items=[received_json(f"Key {n}", flags=1) for n in range(3)] + [received_json("Potion")],
        locations=[{"location": "a", "checked": True}, {"location": "b", "checked": False}],
        release="after_goal",
        collect="disabled",
    )


def test_the_full_embed_has_the_summary_and_the_hints():
    embed = build(sample_tracker())
    assert (embed.title, embed.description) == ("Alice", "Timespinner")
    values = field_values(embed)
    assert list(values) == [
        "Items received", "Locations checked", "Hint points", "Release", "Collect", "Latest progression items",
        "Hints for you", "Hints in your world",
    ]
    assert values["Items received"] == "4" and values["Locations checked"] == "1/2 (50%)"
    assert values["Hint points"] == "12, a hint costs 5"
    assert (values["Release"], values["Collect"]) == ("After your goal", "Disabled")
    assert values["Latest progression items"].splitlines() == ["Key 2", "Key 1", "Key 0"]  # newest first


def test_hints_for_you_lists_the_open_ones_with_how_they_are_marked_and_not_the_found_ones():
    values = field_values(build(sample_tracker()))
    lines = values["Hints for you"].splitlines()
    assert "**Sword** at Chest in **Bob**'s world (priority)" in lines
    assert "**Bow** at Cave in **Cy**'s world (avoid)" in lines
    assert "**Mine** at Home in **Alice**'s world (no priority)" in lines
    assert not any("Gone" in line for line in lines)


def test_hints_in_your_world_list_what_others_wait_for_and_a_hint_for_yourself_only_once():
    values = field_values(build(sample_tracker()))
    assert values["Hints in your world"] == "**Shield** for **Cy** at Tower (no priority)"


def test_the_hints_shortcut_has_no_summary():
    embed = build(sample_tracker(), sections=("hints",))
    assert list(field_values(embed)) == ["Hints for you", "Hints in your world"]
    assert embed.title == "Alice"


def test_the_summary_alone_has_no_hints():
    assert "Hints for you" not in field_values(build(sample_tracker(), sections=("summary",)))


def test_no_hints_and_no_progression_say_none():
    values = field_values(build())
    assert (values["Hints for you"], values["Hints in your world"], values["Latest progression items"]) == ("None",) * 3


def test_free_hints_say_so():
    assert field_values(build(tracker_json(hint_cost=0)))["Hint points"] == "Hints are free"


def test_the_goal_is_shown_and_the_embed_turns_green():
    embed = build(tracker_json(goal=True))
    assert embed.description == "Timespinner — Goal reached" and embed.colour == discord.Colour(0x2E7D32)
    assert build().colour == discord.Colour(0x2F6F9F)


def test_more_than_ten_hints_are_counted_not_dropped_silently():
    hints = [hint_json(f"Item {n}", finder="Bob", location=f"Place {n}", finder_slot=2, location_id=n) for n in range(14)]
    lines = field_values(build(tracker_json(hints)))["Hints for you"].splitlines()
    assert len(lines) == 11 and lines[-1] == "…and 4 more"


def test_long_names_never_push_a_field_over_discords_limit():
    hints = [hint_json("I" * 500, "Alice", "F" * 500, "L" * 500, finder_slot=2, location_id=n) for n in range(12)]
    embed = build(tracker_json(hints, name="N" * 500, items=[received_json("P" * 500, flags=1)] * 30))
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert len(embed) <= 6000 and len(embed.title) <= 256
    assert "more" in field_values(embed)["Hints for you"]


def test_names_from_players_cannot_format_or_ping():
    hints = [hint_json("*Sword* @everyone", "Alice", "<@123>", "`Chest`")]
    text = json_text(build(tracker_json(hints, name="@here")))
    assert "@everyone" not in text.replace("@​everyone", "") and "@here" not in text.replace("@​here", "")
    assert "<@123>" not in text


def test_the_embed_follows_the_language():
    embed = build(sample_tracker(), "de")
    assert list(field_values(embed))[:2] == ["Erhaltene Items", "Geprüfte Orte"]
    assert field_values(embed)["Freigeben"] == "Nach deinem Ziel"


# --- the buttons that mark a hint -------------------------------------------------------------------------------------
def pressed(bot, user_id=1001, locale="en-US") -> FakeInteraction:
    interaction = FakeInteraction(bot, locale=locale, user_id=user_id)
    interaction.message = FakeMessage(1, FakeMessageable(2))
    return interaction


@pytest.mark.parametrize("status", [10, 20, 30])
async def test_the_id_carries_game_slot_finder_location_and_status(bot, status):
    custom_id = f"apbot:hint:7:2:5:77:{status}"
    match = HintStatusButton.__discord_ui_compiled_template__.fullmatch(custom_id)
    item = await HintStatusButton.from_custom_id(pressed(bot), None, match)
    assert (item._ids, item._status, item.custom_id) == ((7, 2, 5, 77), status, custom_id)


@pytest.mark.parametrize(
    "custom_id",
    [
        "apbot:hint:7:2:5:77",  # no status
        "apbot:hint:7:2:5:77:40",  # found is not something a player sets
        "apbot:hint:7:2:5:77:0",
        "apbot:hint:7:2:5:77:3",
        "apbot:hint:7:2:5:77:300",
        "apbot:hint:7:2:5:77:30:1",
        "apbot:hint:7:2:5:-77:30",
        "apbot:hint:7:2:-5:77:30",
        "apbot:hint:a:2:5:77:30",
        "apbot:hint:1234567890:2:5:77:30",  # a game number of more than nine digits
        "apbot:hint:7:2:5:12345678901234567:30",  # a location number of more than sixteen digits
        "apbot:hint:7:2:5:77:30 ",
        "apbot:hint:7:2:5:77:30\n",
        "apbot:hint:７:2:5:77:30",  # full-width digits
        "xapbot:hint:7:2:5:77:30",
        "apbot:ho:accept:7:2",
    ],
)
def test_a_malformed_id_does_not_match(custom_id):
    assert HintStatusButton.__discord_ui_compiled_template__.fullmatch(custom_id) is None


def test_the_largest_ids_still_fit_in_discords_hundred_characters():
    custom_id = f"apbot:hint:{'9' * 9}:{'9' * 9}:{'9' * 9}:{'9' * 16}:30"
    assert len(custom_id) <= 100
    assert HintStatusButton.__discord_ui_compiled_template__.fullmatch(custom_id)


def test_the_view_has_the_three_marks_in_order_and_never_times_out():
    view = hint_status_view(7, 2, 5, 77, "en")
    assert [c.item.label for c in view.children] == ["Mark as priority", "No priority", "Avoid"]
    assert [c.custom_id for c in view.children] == [f"apbot:hint:7:2:5:77:{s}" for s in (30, 10, 20)]
    assert [c.item.style for c in view.children] == [
        discord.ButtonStyle.primary, discord.ButtonStyle.secondary, discord.ButtonStyle.secondary,
    ]
    assert view.timeout is None


def test_the_buttons_follow_the_language():
    assert [c.item.label for c in hint_status_view(7, 2, 5, 77, "de").children] == [
        "Als Priorität markieren", "Keine Priorität", "Vermeiden",
    ]


@pytest.mark.parametrize(("status", "shown"), [(30, "priority"), (10, "no priority"), (20, "avoid")])
async def test_pressing_sends_the_status_for_that_hint_as_the_clicker_and_answers_privately(bot, manager, status, shown):
    link(bot)
    manager.add("PUT", HINT_PATH, {"status": status})
    interaction = pressed(bot)
    await HintStatusButton(7, 2, 5, 77, status).callback(interaction)
    [request] = manager.requests
    assert (request.method, request.url.path) == ("PUT", HINT_PATH)
    assert json.loads(request.content) == {"status": status}
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}" and BOT_TOKEN not in str(request.headers)
    assert interaction.response.defer_kwargs == {"ephemeral": True, "thinking": True}
    [answer] = interaction.messages
    assert answer["ephemeral"] is True and answer["embed"].description == f"The hint is marked: {shown}"
    assert interaction.message.edits == [{"view": None}]  # the buttons are gone


async def test_whoever_presses_acts_as_themselves_never_as_the_person_the_message_was_made_for(bot, manager):
    link(bot, 1001, token="apm_tokenOfTheFirstUser")
    bot.links.save(2002, 20, "bob", bot.cipher.encrypt("apm_tokenOfTheSecondUser"), "User", "en")
    manager.add("PUT", HINT_PATH, (404, {"detail": "No such slot"}))
    with pytest.raises(Exception) as error:
        await HintStatusButton(7, 2, 5, 77, 30).callback(pressed(bot, user_id=2002))
    assert manager.requests[0].headers["Authorization"] == "Bearer apm_tokenOfTheSecondUser"
    assert getattr(error.value, "status", None) == 404  # the manager refuses a slot that is not theirs


async def test_a_hint_that_was_found_meanwhile_shows_the_managers_words_and_keeps_the_buttons(bot, manager):
    link(bot)
    manager.add("PUT", HINT_PATH, (409, {"detail": "This item was found already"}))
    interaction = pressed(bot)
    with pytest.raises(Conflict):
        await HintStatusButton(7, 2, 5, 77, 30).callback(interaction)
    assert interaction.message.edits == []
    await HintStatusButton(7, 2, 5, 77, 30).on_error(interaction, Conflict(409, "This item was found already"))
    assert interaction.followup.sent[-1]["embed"].description == "The manager answered: This item was found already"
    assert interaction.followup.sent[-1]["ephemeral"] is True


async def test_pressing_without_a_link_asks_nothing_and_does_not_show_as_thinking(bot, manager):
    interaction = pressed(bot)
    with pytest.raises(NotLinkedError):
        await HintStatusButton(7, 2, 5, 77, 30).callback(interaction)
    assert manager.requests == [] and not interaction.response.deferred


async def test_a_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    manager.add("PUT", HINT_PATH, (401, {"detail": "no"}))
    with pytest.raises(UserFacingError):
        await HintStatusButton(7, 2, 5, 77, 30).callback(pressed(bot))
    assert bot.links.get(1001).status == "invalid"


async def test_a_read_only_token_is_explained(bot, manager):
    link(bot)
    manager.add("PUT", HINT_PATH, (403, {"detail": "This API token can only read"}))
    with pytest.raises(UserFacingError) as error:
        await HintStatusButton(7, 2, 5, 77, 30).callback(pressed(bot))
    assert "can only read" in error.value.message("en")


async def test_the_answer_is_in_the_pressers_language(bot, manager):
    link(bot)
    manager.add("PUT", HINT_PATH, {"status": 20})
    interaction = pressed(bot, locale="de")
    await HintStatusButton(7, 2, 5, 77, 20).callback(interaction)
    assert interaction.messages[0]["embed"].description == "Der Hinweis ist markiert: vermeiden"


# --- the picker --------------------------------------------------------------------------------------------------------
def hints_for_picker(count=3):
    raw = [hint_json(f"Item {n}", finder="Bob", location=f"Place {n}", finder_slot=5, location_id=100 + n) for n in range(count)]
    return parse_hints(tracker_json(raw))


def test_the_picker_offers_the_open_hints_for_the_person_with_their_numbers():
    picker = hint_picker(1001, "en", 7, 2, hints_for_picker())
    assert isinstance(picker, HintPickerView)
    assert [(o.label, o.value) for o in picker.select.options] == [
        (f"Item {n} — Place {n}", f"5:{100 + n}") for n in range(3)
    ]
    assert picker.timeout == 600


def test_the_picker_leaves_out_found_hints_hints_for_others_and_hints_without_numbers():
    raw = [
        hint_json("Open"),
        hint_json("Found", found=True),
        hint_json("Others", for_me=False, in_my_world=True),
        hint_json("No numbers", finder_slot=None),
    ]
    picker = hint_picker(1001, "en", 7, 2, parse_hints(tracker_json(raw)))
    assert [o.label.split(" — ")[0] for o in picker.select.options] == ["Open"]


def test_there_is_no_picker_without_a_hint_to_mark():
    assert hint_picker(1001, "en", 7, 2, ()) is None
    assert hint_picker(1001, "en", 7, 2, parse_hints(tracker_json([hint_json(found=True)]))) is None


def test_the_picker_offers_at_most_twenty_five_hints():
    picker = hint_picker(1001, "en", 7, 2, hints_for_picker(40))
    assert len(picker.select.options) == 25


def test_long_hint_names_fit_the_options_limit():
    raw = [hint_json("I" * 300, location="L" * 300)]
    option = hint_picker(1001, "en", 7, 2, parse_hints(tracker_json(raw))).select.options[0]
    assert len(option.label) <= 100 and len(option.description) <= 100


async def test_choosing_a_hint_replaces_the_message_with_the_buttons_for_exactly_that_hint():
    picker = hint_picker(1001, "en", 7, 2, hints_for_picker())
    picker.select._values = ["5:101"]
    click = FakeInteraction(user_id=1001)
    await picker.select.callback(click)
    [edit] = click.response.edited
    assert "**Item 1** at Place 1 in **Bob**'s world (no priority)" in edit["embed"].description
    assert "How important is this item to you?" in edit["embed"].description
    assert [c.custom_id for c in edit["view"].children] == [f"apbot:hint:7:2:5:101:{s}" for s in (30, 10, 20)]
    assert picker.is_finished()


async def test_someone_else_cannot_use_the_picker():
    picker = hint_picker(1001, "en", 7, 2, hints_for_picker())
    intruder = FakeInteraction(user_id=2002)
    assert await picker.interaction_check(intruder) is False
    assert intruder.messages[0]["ephemeral"] is True
    assert await picker.interaction_check(FakeInteraction(user_id=1001)) is True


# --- /tracker and /hints -----------------------------------------------------------------------------------------------
def run(bot, command: str, interaction, game="7", slot="2"):
    cog = TrackerCog(bot)
    return getattr(cog, command).callback(cog, interaction, game, slot)


async def test_tracker_asks_for_the_slot_as_the_person_and_answers_privately_with_the_embed_and_the_picker(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, sample_tracker())
    interaction = asker(bot)
    await run(bot, "tracker", interaction)
    [request] = manager.requests
    assert (request.method, request.url.path) == ("GET", TRACKER)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}" and BOT_TOKEN not in str(request.headers)
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    [message] = interaction.messages
    assert message["ephemeral"] is True
    assert list(field_values(message["embed"]))[0] == "Items received"
    assert isinstance(message["view"], HintPickerView)


async def test_hints_shows_only_the_hints_but_the_same_picker(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, sample_tracker())
    interaction = asker(bot)
    await run(bot, "hints", interaction)
    [message] = interaction.messages
    assert list(field_values(message["embed"])) == ["Hints for you", "Hints in your world"]
    assert isinstance(message["view"], HintPickerView)


async def test_without_a_hint_to_mark_there_is_no_picker(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, tracker_json())
    interaction = asker(bot)
    await run(bot, "tracker", interaction)
    assert "view" not in interaction.messages[0]


async def test_the_only_slot_is_found_when_none_is_given(bot, manager):
    link(bot)
    manager.add("GET", "/api/games/7", game_json(7, my_slots=[{"slot": 2, "name": "Alice", "game": "Timespinner"}]))
    manager.add("GET", TRACKER, tracker_json())
    await run(bot, "tracker", asker(bot), slot=None)
    assert manager.paths() == ["/api/games/7", TRACKER]
    assert all(r.headers["Authorization"] == f"Bearer {USER_TOKEN}" for r in manager.requests)


@pytest.mark.parametrize(
    ("my_slots", "text"),
    [
        ([], "You have no slot in this game"),
        ([{"slot": 1, "name": "A"}, {"slot": 2, "name": "B"}], "You have more than one slot in this game. Pick one."),
    ],
)
async def test_without_exactly_one_slot_the_person_is_asked_to_say_which(bot, manager, my_slots, text):
    link(bot)
    manager.add("GET", "/api/games/7", game_json(7, my_slots=my_slots))
    with pytest.raises(UserFacingError) as error:
        await run(bot, "tracker", asker(bot), slot=None)
    assert error.value.message("en") == text
    assert TRACKER not in manager.paths()


async def test_without_a_link_nothing_is_asked_and_nothing_shows_as_thinking(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await run(bot, "tracker", interaction)
    assert manager.requests == [] and not interaction.response.deferred


@pytest.mark.parametrize(("game", "slot"), [("abc", "2"), ("7", "abc"), ("7", "-1"), ("1234567890", "2"), ("7", "１")])
async def test_options_that_are_not_numbers_never_reach_the_manager(bot, manager, game, slot):
    link(bot)
    with pytest.raises(UserFacingError):
        await run(bot, "tracker", asker(bot), game=game, slot=slot)
    assert manager.requests == []


async def test_a_slot_the_manager_does_not_show_the_person_is_explained(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, (404, {"detail": "No such slot"}))
    with pytest.raises(UserFacingError) as error:
        await run(bot, "tracker", asker(bot))
    assert error.value.message("en") == "That game or slot was not found. Pick them from the list."


async def test_a_tracker_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, (401, {"detail": "no"}))
    with pytest.raises(UserFacingError):
        await run(bot, "hints", asker(bot))
    assert bot.links.get(1001).status == "invalid"


async def test_the_reply_is_in_the_persons_language(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, sample_tracker())
    interaction = asker(bot, locale="fr")
    await run(bot, "tracker", interaction)
    assert list(field_values(interaction.messages[0]["embed"]))[0] == "Objets reçus"
