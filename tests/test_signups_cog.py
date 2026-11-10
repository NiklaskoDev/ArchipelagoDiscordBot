import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import discord
import pytest
from fakes import (
    BOT_TOKEN,
    USER_TOKEN,
    FakeAttachment,
    FakeInteraction,
    check_json,
    config_json,
    entry_json,
    json_text,
    signup_detail_json,
    signup_json,
)

from apbot.cogs.signups import MAX_FILE_BYTES, TEST_POLL_INTERVAL, TEST_POLLS, SignupsCog, config_name
from apbot.errors import MissingRoleError, NotLinkedError, UserFacingError
from apbot.manager.errors import Conflict, Forbidden, TooManyRequests

GUILD = 5000
ORGANIZER_ROLE = 777
ROUND = "/api/signups/3"
ENTRIES = f"{ROUND}/entries"
CHECK = "/api/configs/check"
CONFIGS = "/api/configs/text"
FILE_TEXT = "name: Alice\ngame: Timespinner\n"


def link(bot, user_id=1001):
    bot.links.save(user_id, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def asker(bot, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, **kwargs)


def organizer(bot, **kwargs) -> FakeInteraction:
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    return FakeInteraction(bot, user_id=1001, guild_id=GUILD, roles=[ORGANIZER_ROLE], **kwargs)


@pytest.fixture
def sleeps(bot) -> list[float]:
    return []


def make_cog(bot, sleeps=None) -> SignupsCog:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return SignupsCog(bot, sleep=sleep)


def call(bot, name: str, interaction, *args, sleeps=None, **kwargs):
    cog = make_cog(bot, sleeps)
    command = cog.signups if name == "signups" else cog.signup.get_command(name)
    return command.callback(cog, interaction, *args, **kwargs)


def body(manager, index=-1):
    return json.loads(manager.requests[index].content)


def paths(manager) -> list[str]:
    return [f"{r.method} {r.url.path}" for r in manager.requests]


def entered_round(**overrides) -> dict:
    return signup_detail_json(3, "Spring Async", entries=[entry_json(1, "alice.yaml")], my_slot_count=1, **overrides)


async def let_tasks_run(until=lambda: False) -> None:
    for _ in range(200):
        if until():
            return
        await asyncio.sleep(0)


# --- /signups (unchanged: the bot's own view) ----------------------------------------------------------------------------
async def test_the_list_of_open_rounds_still_uses_the_bots_token(bot, manager):
    manager.add("GET", "/api/signups", [signup_json(3, "Spring Async")])
    interaction = asker(bot)
    await call(bot, "signups", interaction)
    assert manager.requests[0].headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert "Spring Async" in json_text(interaction.messages[0]["embed"])


# --- /signup show ---------------------------------------------------------------------------------------------------------
async def test_show_asks_as_the_person_and_replies_privately_with_their_entries(bot, manager):
    link(bot)
    manager.add("GET", ROUND, entered_round())
    bot.cache.set_site_url("https://manager.example")
    interaction = asker(bot)
    await call(bot, "show", interaction, "3")
    [request] = manager.requests
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    [message] = interaction.messages
    assert message["ephemeral"] is True
    assert message["embed"].title == "Spring Async" and "alice.yaml" in json_text(message["embed"])
    assert message["view"].children[0].url == "https://manager.example/#/signups/3"


async def test_show_in_german_follows_the_persons_language(bot, manager):
    link(bot)
    manager.add("GET", ROUND, entered_round())
    interaction = asker(bot, locale="de")
    await call(bot, "show", interaction, "3")
    assert "Deine Einträge" in json_text(interaction.messages[0]["embed"])


async def test_show_without_a_link_says_so_before_anything_is_asked(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await call(bot, "show", interaction, "3")
    assert manager.requests == [] and not interaction.response.deferred


@pytest.mark.parametrize("value", ["x", "-1", "", "1234567890", "3/../4"])
async def test_show_needs_a_real_round_number(bot, manager, value):
    link(bot)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "show", asker(bot), value)
    assert error.value.message("en") == "Pick a round from the list" and manager.requests == []


async def test_a_round_the_manager_does_not_know_is_explained(bot, manager):
    link(bot)
    manager.add("GET", ROUND, (404, {"detail": "No such round"}))
    with pytest.raises(UserFacingError) as error:
        await call(bot, "show", asker(bot), "3")
    assert "not found" in error.value.message("en")


async def test_a_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    manager.add("GET", ROUND, (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError):
        await call(bot, "show", asker(bot), "3")
    assert bot.links.get(1001).status == "invalid"


# --- /signup join with a file ----------------------------------------------------------------------------------------------
def routes_for_a_good_file(manager, **add_overrides):
    manager.add("POST", CHECK, check_json(True))
    manager.add("POST", CONFIGS, config_json(41, "alice.yaml"))
    manager.add("POST", ENTRIES, {**entered_round(), **add_overrides})


async def test_a_good_file_is_checked_saved_and_entered_in_that_order_as_the_person(bot, manager):
    link(bot)
    routes_for_a_good_file(manager)
    interaction = asker(bot)
    await call(bot, "join", interaction, "3", file=FakeAttachment("alice.yaml", FILE_TEXT.encode()))
    assert paths(manager) == [f"POST {CHECK}", f"POST {CONFIGS}", f"POST {ENTRIES}"]
    assert {r.headers["Authorization"] for r in manager.requests} == {f"Bearer {USER_TOKEN}"}
    assert body(manager, 0) == {"content": FILE_TEXT}
    assert body(manager, 1) == {"name": "alice.yaml", "content": FILE_TEXT}
    assert body(manager, 2) == {"config_id": 41}
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    check, entered = (m["embed"] for m in interaction.messages)
    assert check.title == "Check of alice.yaml" and check.description == "The config passed the check"
    assert entered.description == "Entered **alice.yaml** in **Spring Async**. You use 1 of 2 slots."
    assert all(m["ephemeral"] is True for m in interaction.messages)


async def test_the_managers_warnings_about_the_entry_are_shown(bot, manager):
    link(bot)
    routes_for_a_good_file(manager, warnings=["Not installed on this server yet: Timespinner."])
    interaction = asker(bot)
    await call(bot, "join", interaction, "3", file=FakeAttachment())
    entered = interaction.messages[-1]["embed"]
    assert "Not installed on this server yet: Timespinner." in json_text(entered)


async def test_a_file_with_problems_is_shown_and_nothing_is_saved_or_entered(bot, manager):
    link(bot)
    manager.add("POST", CHECK, check_json(False, errors=["The file is not a valid config"], players=[]))
    interaction = asker(bot)
    await call(bot, "join", interaction, "3", file=FakeAttachment())
    assert paths(manager) == [f"POST {CHECK}"]
    [message] = interaction.messages
    assert "The file is not a valid config" in json_text(message["embed"])
    assert message["embed"].description == "The config has problems. Nothing was saved or entered."


async def test_a_utf8_byte_order_mark_is_removed_before_the_manager_sees_the_text(bot, manager):
    link(bot)
    routes_for_a_good_file(manager)
    await call(bot, "join", asker(bot), "3", file=FakeAttachment("a.yml", b"\xef\xbb\xbf" + FILE_TEXT.encode()))
    assert body(manager, 0) == {"content": FILE_TEXT}


async def test_the_extension_may_be_in_capitals_and_yml_is_fine(bot, manager):
    link(bot)
    routes_for_a_good_file(manager)
    await call(bot, "join", asker(bot), "3", file=FakeAttachment("ALICE.YML"))
    assert body(manager, 1)["name"] == "ALICE.YML"


@pytest.mark.parametrize("filename", ["notes.txt", "alice.yaml.txt", "alice", "yaml", "alice.json"])
async def test_a_file_that_is_not_yaml_is_refused_before_it_is_downloaded_or_anything_is_asked(bot, manager, filename):
    link(bot)
    interaction, attachment = asker(bot), FakeAttachment(filename)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "join", interaction, "3", file=attachment)
    assert error.value.message("en") == "Only .yaml and .yml files can be entered"
    assert manager.requests == [] and attachment.reads == 0 and not interaction.response.deferred


async def test_a_file_over_one_mebibyte_is_refused_before_it_is_downloaded(bot, manager):
    link(bot)
    interaction, attachment = asker(bot), FakeAttachment("big.yaml", b"x", size=MAX_FILE_BYTES + 1)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "join", interaction, "3", file=attachment)
    assert error.value.message("en") == "The file is larger than 1 MiB"
    assert manager.requests == [] and attachment.reads == 0


async def test_a_file_of_exactly_one_mebibyte_is_taken(bot, manager):
    link(bot)
    routes_for_a_good_file(manager)
    attachment = FakeAttachment("big.yaml", b"a" * MAX_FILE_BYTES)
    await call(bot, "join", asker(bot), "3", file=attachment)
    assert len(body(manager, 0)["content"]) == MAX_FILE_BYTES


async def test_a_download_larger_than_the_size_discord_reported_is_refused_too(bot, manager):
    link(bot)
    attachment = FakeAttachment("big.yaml", b"a" * (MAX_FILE_BYTES + 1), size=10)
    with pytest.raises(UserFacingError):
        await call(bot, "join", asker(bot), "3", file=attachment)
    assert manager.requests == []


async def test_a_file_that_is_not_utf8_is_refused_before_anything_is_asked(bot, manager):
    link(bot)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "join", asker(bot), "3", file=FakeAttachment("a.yaml", b"name: \xff\xfe"))
    assert error.value.message("en") == "The file is not UTF-8 text" and manager.requests == []


async def test_a_download_that_fails_is_explained(bot, manager):
    link(bot)
    attachment = FakeAttachment()
    attachment.fail_with = discord.HTTPException(SimpleNamespace(status=500, reason="x"), "x")
    with pytest.raises(UserFacingError) as error:
        await call(bot, "join", asker(bot), "3", file=attachment)
    assert "could not be downloaded" in error.value.message("en") and manager.requests == []


async def test_a_long_file_name_is_cut_to_the_managers_limit_keeping_its_extension(bot, manager):
    link(bot)
    routes_for_a_good_file(manager)
    await call(bot, "join", asker(bot), "3", file=FakeAttachment("n" * 300 + ".yaml"))
    name = body(manager, 1)["name"]
    assert len(name) == 120 and name.endswith(".yaml")
    assert config_name("short.yaml") == "short.yaml"


async def test_when_the_round_refuses_the_saved_file_the_person_is_told_it_is_in_their_library(bot, manager):
    link(bot)
    manager.add("POST", CHECK, check_json(True))
    manager.add("POST", CONFIGS, config_json(41, "alice (2).yaml"))
    manager.add("POST", ENTRIES, (409, {"detail": "Each player can enter 1 slot in this round. Withdraw an entry first."}))
    with pytest.raises(UserFacingError) as error:
        await call(bot, "join", asker(bot), "3", file=FakeAttachment())
    message = error.value.message("en")
    assert message == (
        "Saved as **alice (2).yaml** in your manager library, but it could not be entered: "
        "Each player can enter 1 slot in this round. Withdraw an entry first."
    )


async def test_a_check_that_is_still_running_for_this_person_is_the_managers_429(bot, manager):
    link(bot)
    manager.add("POST", CHECK, (429, {"detail": "Your last check is not done yet."}))
    with pytest.raises(TooManyRequests):
        await call(bot, "join", asker(bot), "3", file=FakeAttachment())
    assert paths(manager) == [f"POST {CHECK}"]


async def test_a_read_only_token_is_explained_at_the_first_change(bot, manager):
    link(bot)
    manager.add("POST", CHECK, (403, {"detail": "This API token can only read"}))
    with pytest.raises(UserFacingError) as error:
        await call(bot, "join", asker(bot), "3", file=FakeAttachment())
    assert "can only read" in error.value.message("en")


# --- /signup join with a saved config -------------------------------------------------------------------------------------
async def test_a_saved_config_is_entered_directly_without_another_check(bot, manager):
    link(bot)
    manager.add("POST", ENTRIES, entered_round())
    interaction = asker(bot)
    await call(bot, "join", interaction, "3", config="41")
    assert paths(manager) == [f"POST {ENTRIES}"]
    assert body(manager) == {"config_id": 41}
    [message] = interaction.messages
    assert message["embed"].description == "Entered **alice.yaml** in **Spring Async**. You use 1 of 2 slots."


async def test_a_refusal_for_a_saved_config_is_the_managers_own_answer(bot, manager):
    link(bot)
    manager.add("POST", ENTRIES, (409, {"detail": "'alice.yaml' is already entered in this round"}))
    with pytest.raises(Conflict) as error:
        await call(bot, "join", asker(bot), "3", config="41")
    assert error.value.detail == "'alice.yaml' is already entered in this round"


async def test_a_config_or_round_the_manager_does_not_show_is_explained(bot, manager):
    link(bot)
    manager.add("POST", ENTRIES, (404, {"detail": "No such config"}))
    with pytest.raises(UserFacingError) as error:
        await call(bot, "join", asker(bot), "3", config="41")
    assert error.value.message("en") == "That round or config was not found. Pick them from the list."


async def test_a_mystery_round_refusal_is_shown_as_it_is(bot, manager):
    link(bot)
    manager.add("POST", ENTRIES, (409, {"detail": "This is a mystery round: join it with your player name"}))
    with pytest.raises(Conflict) as error:
        await call(bot, "join", asker(bot), "3", config="41")
    assert "mystery round" in error.value.detail


@pytest.mark.parametrize("config", ["x", "-4", "1234567890"])
async def test_a_config_must_be_one_from_the_list(bot, manager, config):
    link(bot)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "join", asker(bot), "3", config=config)
    assert error.value.message("en") == "Pick a config from the list" and manager.requests == []


# --- /signup join: the options ----------------------------------------------------------------------------------------------
async def test_exactly_one_of_file_and_config_is_needed(bot, manager):
    link(bot)
    for kwargs, text in [
        ({}, "Attach a YAML file or pick one of your saved configs"),
        ({"file": FakeAttachment(), "config": "41"}, "Attach a file or pick a saved config, not both"),
    ]:
        interaction = asker(bot)
        with pytest.raises(UserFacingError) as error:
            await call(bot, "join", interaction, "3", **kwargs)
        assert error.value.message("en") == text and not interaction.response.deferred
    assert manager.requests == []


async def test_joining_without_a_link_says_so_and_does_not_download_the_file(bot, manager):
    attachment = FakeAttachment()
    with pytest.raises(NotLinkedError):
        await call(bot, "join", asker(bot), "3", file=attachment)
    assert attachment.reads == 0 and manager.requests == []


async def test_joining_forgets_the_suggestions_that_are_now_out_of_date(bot, manager):
    link(bot)
    manager.add("GET", ROUND, signup_detail_json(3, entries=[]))
    await bot.signup_directory.my_entries(1001, USER_TOKEN, 3)
    manager.add("GET", "/api/configs", [config_json(41)])
    await bot.signup_directory.configs(1001, USER_TOKEN)
    routes_for_a_good_file(manager)
    manager.requests.clear()
    await call(bot, "join", asker(bot), "3", file=FakeAttachment())
    manager.requests.clear()
    manager.add("GET", ROUND, entered_round())
    manager.add("GET", "/api/configs", [config_json(41), config_json(42)])
    assert [e.name for e in await bot.signup_directory.my_entries(1001, USER_TOKEN, 3)] == ["alice.yaml"]
    assert len(await bot.signup_directory.configs(1001, USER_TOKEN)) == 2


# --- /signup withdraw ------------------------------------------------------------------------------------------------------
async def start_withdraw(bot, interaction, entry="1"):
    task = asyncio.create_task(call(bot, "withdraw", interaction, "3", entry))
    await let_tasks_run(lambda: interaction.followup.sent or task.done())
    return task


async def test_confirming_deletes_the_entry_as_the_person_and_says_how_many_slots_are_left(bot, manager):
    link(bot)
    manager.add("GET", ROUND, entered_round())
    manager.add("DELETE", f"{ENTRIES}/1", signup_detail_json(3, "Spring Async", entries=[], my_slot_count=0))
    interaction = asker(bot)
    task = await start_withdraw(bot, interaction)
    [asked] = interaction.followup.sent
    assert asked["embed"].description == "Withdraw **alice.yaml** from **Spring Async**?" and asked["ephemeral"] is True
    assert [b.label for b in asked["view"].children] == ["Withdraw entry", "Cancel"]
    assert asked["view"].children[0].style.name == "danger"
    assert "DELETE" not in " ".join(paths(manager))  # nothing before the person confirms
    await asked["view"].children[0].callback(FakeInteraction(user_id=1001))
    await task
    request = manager.requests[-1]
    assert (request.method, request.url.path) == ("DELETE", f"{ENTRIES}/1")
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    [edit] = interaction.original_edits
    assert edit["embed"].description == "Withdrew **alice.yaml** from **Spring Async**. You use 0 of 2 slots."
    assert edit["view"] is None


async def test_cancelling_deletes_nothing(bot, manager):
    link(bot)
    manager.add("GET", ROUND, entered_round())
    interaction = asker(bot)
    task = await start_withdraw(bot, interaction)
    click = FakeInteraction(user_id=1001)
    await interaction.followup.sent[-1]["view"].children[1].callback(click)
    await task
    assert paths(manager) == [f"GET {ROUND}"] and click.response.edited[0]["embed"].description == "Cancelled"


async def test_someone_else_cannot_confirm_the_withdrawal(bot, manager):
    link(bot)
    manager.add("GET", ROUND, entered_round())
    interaction = asker(bot)
    task = await start_withdraw(bot, interaction)
    view = interaction.followup.sent[-1]["view"]
    assert await view.interaction_check(FakeInteraction(user_id=2002)) is False
    view.stop()
    await task
    assert "DELETE" not in " ".join(paths(manager))


async def test_an_entry_that_is_not_the_persons_is_refused_before_anything_is_deleted(bot, manager):
    link(bot)
    manager.add("GET", ROUND, signup_detail_json(3, entries=[entry_json(1, mine=False, name=None, username="bob")]))
    with pytest.raises(UserFacingError) as error:
        await call(bot, "withdraw", asker(bot), "3", "1")
    assert error.value.message("en") == "You have no such entry in this round. Pick one from the list."
    assert paths(manager) == [f"GET {ROUND}"]


async def test_withdrawing_from_a_closed_round_shows_the_managers_answer(bot, manager):
    link(bot)
    manager.add("GET", ROUND, entered_round())
    manager.add("DELETE", f"{ENTRIES}/1", (409, {"detail": "Sign-ups for this round are closed"}))
    interaction = asker(bot)
    task = await start_withdraw(bot, interaction)
    await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
    with pytest.raises(Conflict) as error:
        await task
    assert error.value.detail == "Sign-ups for this round are closed"


@pytest.mark.parametrize("entry", ["x", "-1", "1234567890"])
async def test_withdrawing_needs_an_entry_from_the_list(bot, manager, entry):
    link(bot)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "withdraw", asker(bot), "3", entry)
    assert error.value.message("en") == "Pick an entry from the list" and manager.requests == []


# --- /signup open ---------------------------------------------------------------------------------------------------------
async def test_opening_a_round_reads_the_deadline_on_the_servers_clock_and_asks_as_the_person(bot, manager):
    link(bot)
    bot.guild_settings.update(GUILD, timezone="Europe/Berlin")
    manager.add("POST", "/api/signups", signup_detail_json(9, "Autumn Run", deadline=4_000_000_000.0, slots_per_user=3))
    interaction = organizer(bot)
    await call(bot, "open", interaction, "  Autumn Run ", "2099-05-17 20:00", 3, " About it ")
    [request] = manager.requests
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    expected = datetime(2099, 5, 17, 18, 0, tzinfo=UTC).timestamp()  # Berlin is UTC+2 in May
    assert body(manager) == {"name": "Autumn Run", "description": "About it", "deadline": expected, "slots_per_user": 3}
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    [message] = interaction.messages
    assert message["ephemeral"] is True and message["embed"].title == "Autumn Run"


async def test_a_round_without_a_deadline_or_description_is_opened_with_nulls_and_one_slot(bot, manager):
    link(bot)
    manager.add("POST", "/api/signups", signup_detail_json(9, "Autumn Run", deadline=None))
    await call(bot, "open", organizer(bot), "Autumn Run")
    assert body(manager) == {"name": "Autumn Run", "description": None, "deadline": None, "slots_per_user": 1}


async def test_a_blank_description_is_no_description(bot, manager):
    link(bot)
    manager.add("POST", "/api/signups", signup_detail_json(9, "Autumn Run"))
    await call(bot, "open", organizer(bot), "Autumn Run", None, 1, "   ")
    assert body(manager)["description"] is None


async def test_a_deadline_in_the_past_is_refused_before_the_manager_is_asked(bot, manager):
    link(bot)
    interaction = organizer(bot)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "open", interaction, "Old", "2001-01-01 10:00")
    assert error.value.message("en") == "The deadline is in the past"
    assert manager.requests == [] and not interaction.response.deferred


@pytest.mark.parametrize("text", ["tomorrow", "2099-05-17", "17.05.2099 20:00", "2099-02-30 10:00"])
async def test_a_deadline_in_the_wrong_format_is_refused_before_the_manager_is_asked(bot, manager, text):
    link(bot)
    with pytest.raises(UserFacingError):
        await call(bot, "open", organizer(bot), "Round", text)
    assert manager.requests == []


async def test_opening_a_round_needs_the_organizer_role_when_the_server_has_one(bot, manager):
    link(bot)
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    interaction = FakeInteraction(bot, user_id=1001, guild_id=GUILD)
    with pytest.raises(MissingRoleError):
        await call(bot, "open", interaction, "Round")
    assert manager.requests == [] and not interaction.response.deferred


async def test_a_server_without_an_organizer_role_lets_every_linked_person_try(bot, manager):
    link(bot)
    manager.add("POST", "/api/signups", signup_detail_json(9, "Round"))
    await call(bot, "open", FakeInteraction(bot, user_id=1001, guild_id=GUILD), "Round")
    assert paths(manager) == ["POST /api/signups"]


async def test_the_manager_decides_who_is_an_organizer_and_its_refusal_is_shown(bot, manager):
    link(bot)
    manager.add("POST", "/api/signups", (403, {"detail": "Only organizers and administrators can do this"}))
    with pytest.raises(Forbidden) as error:
        await call(bot, "open", organizer(bot), "Round")
    assert error.value.detail == "Only organizers and administrators can do this"


async def test_opening_a_round_outside_a_server_is_refused(bot, manager):
    link(bot)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "open", asker(bot), "Round")
    assert error.value.message("en") == "This command only works in a server" and manager.requests == []


# --- /signup close ---------------------------------------------------------------------------------------------------------
async def start_close(bot, interaction):
    task = asyncio.create_task(call(bot, "close", interaction, "3"))
    await let_tasks_run(lambda: interaction.followup.sent or task.done())
    return task


async def test_closing_asks_first_and_then_posts_as_the_person(bot, manager):
    link(bot)
    manager.add("GET", ROUND, signup_detail_json(3, "Spring Async"))
    manager.add("POST", f"{ROUND}/close", signup_detail_json(3, "Spring Async", status="closed", open=False))
    interaction = organizer(bot)
    task = await start_close(bot, interaction)
    [asked] = interaction.followup.sent
    assert asked["embed"].description == (
        "Close the sign-ups of **Spring Async**? Players can no longer enter or withdraw. "
        "It has 5 entries from 3 players."
    )
    assert [b.label for b in asked["view"].children] == ["Close sign-ups", "Cancel"]
    assert f"POST {ROUND}/close" not in paths(manager)
    await asked["view"].children[0].callback(FakeInteraction(user_id=1001))
    await task
    assert manager.requests[-1].headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert paths(manager) == [f"GET {ROUND}", f"POST {ROUND}/close"]
    [edit] = interaction.original_edits
    assert edit["embed"].description == "Closed the sign-ups of **Spring Async**. It has 5 entries from 3 players."


async def test_cancelling_the_close_posts_nothing(bot, manager):
    link(bot)
    manager.add("GET", ROUND, signup_detail_json(3))
    interaction = organizer(bot)
    task = await start_close(bot, interaction)
    await interaction.followup.sent[-1]["view"].children[1].callback(FakeInteraction(user_id=1001))
    await task
    assert paths(manager) == [f"GET {ROUND}"]


async def test_closing_needs_the_organizer_role_when_the_server_has_one(bot, manager):
    link(bot)
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    with pytest.raises(MissingRoleError):
        await call(bot, "close", FakeInteraction(bot, user_id=1001, guild_id=GUILD), "3")
    assert manager.requests == []


async def test_a_round_the_person_does_not_run_is_refused_by_the_manager_and_shown(bot, manager):
    link(bot)
    manager.add("GET", ROUND, signup_detail_json(3))
    manager.add("POST", f"{ROUND}/close", (403, {"detail": "Only the organizer of this round and administrators can do this"}))
    interaction = organizer(bot)
    task = await start_close(bot, interaction)
    await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
    with pytest.raises(Forbidden):
        await task


# --- /signup test ----------------------------------------------------------------------------------------------------------
def job(status, current=True, **extra):
    return {"job_id": 5, "status": status, "error": None, "current": current, "queue_position": None, **extra}


async def test_the_test_is_started_and_the_result_replaces_the_message_when_it_is_over(bot, manager, sleeps):
    link(bot)
    manager.add("POST", f"{ROUND}/test", signup_detail_json(3, "Spring Async", test=job("queued")))
    manager.add(
        "GET", ROUND,
        signup_detail_json(3, "Spring Async", test=job("running")),
        signup_detail_json(3, "Spring Async", test=job("running")),
        signup_detail_json(3, "Spring Async", test=job("succeeded")),
    )
    interaction = organizer(bot)
    await call(bot, "test", interaction, "3", sleeps=sleeps)
    assert paths(manager) == [f"POST {ROUND}/test"] + [f"GET {ROUND}"] * 3
    assert {r.headers["Authorization"] for r in manager.requests} == {f"Bearer {USER_TOKEN}"}
    [started] = interaction.messages
    assert started["ephemeral"] is True and "Testing **Spring Async**" in started["embed"].description
    [result] = interaction.original_edits
    assert sleeps == [TEST_POLL_INTERVAL] * 3 and TEST_POLL_INTERVAL == 10
    assert result["embed"].title == "Spring Async"
    assert {f.name: f.value for f in result["embed"].fields}["Test"] == "Succeeded"


async def test_a_failed_test_lists_what_the_generator_said_about_each_entry(bot, manager, sleeps):
    link(bot)
    manager.add("POST", f"{ROUND}/test", signup_detail_json(3, test=job("queued")))
    bad = entry_json(2, "bob.yaml", mine=False, username="bob", generation_error="Item name unknown")
    manager.add("GET", ROUND, signup_detail_json(3, entries=[bad], test=job("failed", error="Generation failed")))
    interaction = organizer(bot)
    await call(bot, "test", interaction, "3", sleeps=sleeps)
    text = {f.name: f.value for f in interaction.original_edits[0]["embed"].fields}["Test"]
    assert text.splitlines() == ["Failed", "Generation failed", "**bob.yaml** (bob): Item name unknown"]
    assert sleeps == [TEST_POLL_INTERVAL]


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
async def test_the_follow_up_stops_on_any_finished_state(bot, manager, sleeps, status):
    link(bot)
    manager.add("POST", f"{ROUND}/test", signup_detail_json(3, test=job("queued")))
    manager.add("GET", ROUND, signup_detail_json(3, test=job(status)))
    await call(bot, "test", organizer(bot), "3", sleeps=sleeps)
    assert len(sleeps) == 1


async def test_a_test_that_never_ends_is_given_up_after_ten_minutes(bot, manager, sleeps):
    link(bot)
    manager.add("POST", f"{ROUND}/test", signup_detail_json(3, test=job("queued")))
    manager.add("GET", ROUND, signup_detail_json(3, test=job("running")))
    interaction = organizer(bot)
    await call(bot, "test", interaction, "3", sleeps=sleeps)
    assert TEST_POLLS * TEST_POLL_INTERVAL == 600 and sleeps == [TEST_POLL_INTERVAL] * TEST_POLLS
    [last] = interaction.original_edits
    assert last["embed"].description == "The test is still running. Look again later with /signup show."


async def test_a_busy_manager_between_two_looks_does_not_end_the_follow_up(bot, manager, sleeps):
    link(bot)
    manager.add("POST", f"{ROUND}/test", signup_detail_json(3, test=job("queued")))
    busy = (503, {"detail": "busy"})
    manager.add("GET", ROUND, busy, busy, busy, busy, signup_detail_json(3, test=job("succeeded")))
    interaction = organizer(bot)
    await call(bot, "test", interaction, "3", sleeps=sleeps)
    assert len(sleeps) == 2 and "Succeeded" in json_text(interaction.original_edits[0]["embed"])


async def test_a_test_that_is_already_running_shows_the_managers_answer(bot, manager, sleeps):
    link(bot)
    manager.add("POST", f"{ROUND}/test", (409, {"detail": "A test of this round is already running"}))
    interaction = organizer(bot)
    with pytest.raises(Conflict):
        await call(bot, "test", interaction, "3", sleeps=sleeps)
    assert sleeps == [] and interaction.original_edits == []


async def test_testing_needs_the_organizer_role_when_the_server_has_one(bot, manager):
    link(bot)
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    with pytest.raises(MissingRoleError):
        await call(bot, "test", FakeInteraction(bot, user_id=1001, guild_id=GUILD), "3")
    assert manager.requests == []
