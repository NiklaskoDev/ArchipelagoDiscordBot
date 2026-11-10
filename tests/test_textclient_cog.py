import asyncio
import json

import httpx
import pytest
from fakes import BOT_TOKEN, USER_TOKEN, FakeInteraction, tracker_json

from apbot.autocomplete import LOADING_VALUE
from apbot.cogs.textclient import TextClientCog
from apbot.errors import NotLinkedError, UserFacingError
from apbot.manager.errors import ManagerUnavailable

CLIENT = "/api/games/7/slots/2/client"
SAY = f"{CLIENT}/say"
TRACKER = "/api/games/7/slots/2/tracker"
ACTION = "/api/games/7/slots/2/action"


def link(bot, user_id=1001):
    bot.links.save(user_id, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def asker(bot, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, **kwargs)


def connected(last=1, messages=()):
    return {"state": "connected", "error": None, "last": last, "messages": list(messages), "hint_points": 1, "hint_cost": 1}


def answer(seq, text, mine=True):
    return {"seq": seq, "time": 1.0, "type": "CommandResult", "parts": [{"text": text}], "mine": mine}


@pytest.fixture(autouse=True)
def instant_waits(bot):
    async def no_sleep(seconds: float) -> None:
        return None

    bot.text_client._sleep = no_sleep


def say_route(manager, *messages):
    """The text client: connected at once, then the messages are the answer."""
    manager.add("POST", CLIENT, connected(1), connected(1), connected(1 + len(messages), messages), connected(1 + len(messages)))
    manager.add("POST", SAY, {"ok": True})


def call(bot, command: str, interaction, *args, **kwargs):
    cog = TextClientCog(bot)
    return getattr(cog, command).callback(cog, interaction, *args, **kwargs)


# --- /hint, /hint_location, /remaining ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("command", "option", "said"),
    [
        ("hint", "Progressive Sword", "!hint Progressive Sword"),
        ("hint_location", "Cave  Chest", "!hint_location Cave Chest"),
    ],
)
async def test_the_hint_commands_say_the_server_command_as_the_person_and_show_its_answer(bot, manager, command, option, said):
    link(bot)
    say_route(manager, answer(2, "Your hint costs 5 points"))
    interaction = asker(bot)
    await call(bot, command, interaction, "7", option, "2")
    assert json.loads(next(r for r in manager.requests if r.url.path == SAY).content) == {"text": said}
    assert all(r.headers["Authorization"] == f"Bearer {USER_TOKEN}" for r in manager.requests)
    assert BOT_TOKEN not in str(manager.requests[0].headers)
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    [message] = interaction.messages
    assert message["ephemeral"] is True and message["embed"].description == "Your hint costs 5 points"


async def test_remaining_says_the_command_without_arguments(bot, manager):
    link(bot)
    say_route(manager, answer(2, "Remaining items: 4"))
    interaction = asker(bot)
    await call(bot, "remaining", interaction, "7", "2")
    assert json.loads(next(r for r in manager.requests if r.url.path == SAY).content) == {"text": "!remaining"}
    assert interaction.messages[0]["embed"].description == "Remaining items: 4"


async def test_a_name_with_a_newline_cannot_smuggle_in_a_second_command(bot, manager):
    link(bot)
    say_route(manager, answer(2, "ok"))
    await call(bot, "hint", asker(bot), "7", "Sword\n!release", "2")
    assert json.loads(next(r for r in manager.requests if r.url.path == SAY).content) == {"text": "!hint Sword !release"}


async def test_the_loading_suggestion_is_not_an_item_name(bot, manager):
    link(bot)
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "hint", interaction, "7", LOADING_VALUE, "2")
    assert error.value.message("en") == "The names are still loading. Try again in a moment."
    assert manager.requests == [] and not interaction.response.deferred


async def test_the_only_slot_is_used_when_none_is_given(bot, manager):
    link(bot)
    manager.add("GET", "/api/games/7", {"id": 7, "name": "G", "my_slots": [{"slot": 2, "name": "Alice"}]})
    say_route(manager, answer(2, "Remaining items: 1"))
    await call(bot, "remaining", asker(bot), "7")
    assert manager.paths()[0] == "/api/games/7" and CLIENT in manager.paths()


async def test_a_silent_server_says_so(bot, manager):
    link(bot)
    manager.add("POST", CLIENT, connected(1))
    manager.add("POST", SAY, {"ok": True})
    interaction = asker(bot)
    await call(bot, "hint", interaction, "7", "Sword", "2")
    assert interaction.messages[0]["embed"].description == "The server did not answer. Try again in a moment."


async def test_the_answer_is_in_the_persons_language(bot, manager):
    link(bot)
    manager.add("POST", CLIENT, connected(1))
    manager.add("POST", SAY, {"ok": True})
    interaction = asker(bot, locale="de")
    await call(bot, "remaining", interaction, "7", "2")
    assert interaction.messages[0]["embed"].description == "Der Server hat nicht geantwortet. Versuche es gleich noch einmal."


async def test_a_server_that_is_off_is_explained_and_nothing_is_said(bot, manager):
    link(bot)
    manager.add("POST", CLIENT, {"state": "offline", "error": None, "last": 0, "messages": []})
    with pytest.raises(UserFacingError) as error:
        await call(bot, "hint", asker(bot), "7", "Sword", "2")
    assert error.value.message("en") == "The game's server is not running"
    assert SAY not in manager.paths()


async def test_a_slot_that_is_not_the_persons_is_explained(bot, manager):
    link(bot)
    manager.add("POST", CLIENT, (404, {"detail": "No such slot"}))
    with pytest.raises(UserFacingError) as error:
        await call(bot, "remaining", asker(bot), "7", "2")
    assert error.value.message("en") == "That game or slot was not found. Pick them from the list."


async def test_a_read_only_token_is_explained_because_the_client_needs_to_change_things(bot, manager):
    link(bot)
    manager.add("POST", CLIENT, (403, {"detail": "This API token can only read"}))
    with pytest.raises(UserFacingError) as error:
        await call(bot, "remaining", asker(bot), "7", "2")
    assert "can only read" in error.value.message("en")


async def test_without_a_link_nothing_is_asked_and_nothing_shows_as_thinking(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await call(bot, "remaining", interaction, "7", "2")
    assert manager.requests == [] and not interaction.response.deferred


# --- /say -------------------------------------------------------------------------------------------------------------
async def test_say_sends_the_text_and_confirms_privately(bot, manager):
    link(bot)
    manager.add("POST", CLIENT, connected(1))
    manager.add("POST", SAY, {"ok": True})
    interaction = asker(bot)
    await call(bot, "say", interaction, "7", "Hello everyone", "2")
    assert json.loads(manager.requests[-1].content) == {"text": "Hello everyone"}
    [message] = interaction.messages
    assert message["embed"].description == "Sent" and message["ephemeral"] is True


@pytest.mark.parametrize("text", ["/hint Sword", "  /anything", "line\nbreak"])
async def test_say_refuses_text_the_server_would_refuse_before_anything_is_asked(bot, manager, text):
    link(bot)
    interaction = asker(bot)
    with pytest.raises(UserFacingError):
        await call(bot, "say", interaction, "7", text, "2")
    assert manager.requests == [] and not interaction.response.deferred


# --- /release and /collect ---------------------------------------------------------------------------------------------
async def let_tasks_run(until=lambda: False) -> None:
    for _ in range(200):
        if until():
            return
        await asyncio.sleep(0)


async def start_action(bot, command, interaction):
    """Runs the command up to the question and returns the task and the view that asks it."""
    task = asyncio.create_task(call(bot, command, interaction, "7", "2"))
    await let_tasks_run(lambda: interaction.followup.sent or task.done())
    return task


@pytest.mark.parametrize(
    ("command", "rule", "text"),
    [
        ("release", "disabled", "Releasing is turned off in this game."),
        ("release", "done", "There is nothing left to release."),
        ("release", "automatic", "Your items are released by themselves when you reach your goal."),
        ("release", "after_goal", "You can release your items once you have reached your goal."),
        ("collect", "disabled", "Collecting is turned off in this game."),
        ("collect", "done", "There is nothing left to collect."),
        ("collect", "automatic", "Your items are collected by themselves when you reach your goal."),
        ("collect", "after_goal", "You can collect your items once you have reached your goal."),
    ],
)
async def test_when_the_rules_do_not_allow_it_the_person_is_told_and_nothing_is_posted(bot, manager, command, rule, text):
    link(bot)
    manager.add("GET", TRACKER, tracker_json(**{command: rule}))
    interaction = asker(bot)
    await call(bot, command, interaction, "7", "2")
    assert manager.paths() == [TRACKER]  # no action, no text client
    [message] = interaction.messages
    assert message["embed"].description == text and "view" not in message and message["ephemeral"] is True


async def test_the_rule_of_the_other_action_does_not_matter(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, tracker_json(release="disabled", collect="available"))
    manager.add("POST", ACTION, {"done": True, "message": "collected"})
    interaction = asker(bot)
    task = await start_action(bot, "collect", interaction)
    assert interaction.followup.sent, "the question should be shown"
    interaction.followup.sent[-1]["view"].stop()
    await task


@pytest.mark.parametrize(
    ("command", "question", "button"),
    [
        ("release", "Release all remaining items from Alice? This cannot be undone.", "Release items"),
        ("collect", "Collect all items that other worlds still hold for Alice? This cannot be undone.", "Collect items"),
    ],
)
async def test_confirming_posts_the_action_as_the_person_and_shows_the_servers_answer(bot, manager, command, question, button):
    link(bot)
    manager.add("GET", TRACKER, tracker_json())
    manager.add("POST", ACTION, {"done": True, "message": "Alice released all remaining items"})
    interaction = asker(bot)
    task = await start_action(bot, command, interaction)
    [asked] = interaction.followup.sent
    assert asked["embed"].description == question and asked["ephemeral"] is True
    view = asked["view"]
    assert [b.label for b in view.children] == [button, "Cancel"]
    assert ACTION not in manager.paths()  # nothing before the person confirms
    await view.children[0].callback(FakeInteraction(user_id=1001))
    await task
    request = manager.requests[-1]
    assert (request.method, request.url.path) == ("POST", ACTION)
    assert json.loads(request.content) == {"action": command}
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    [edit] = interaction.original_edits
    assert edit["embed"].description == "Alice released all remaining items" and edit["view"] is None


async def test_cancelling_posts_nothing(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, tracker_json())
    interaction = asker(bot)
    task = await start_action(bot, "release", interaction)
    click = FakeInteraction(user_id=1001)
    await interaction.followup.sent[-1]["view"].children[1].callback(click)
    await task
    assert manager.paths() == [TRACKER]
    assert click.response.edited[0]["embed"].description == "Cancelled"


async def test_someone_else_cannot_confirm_for_the_person(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, tracker_json())
    interaction = asker(bot)
    task = await start_action(bot, "release", interaction)
    view = interaction.followup.sent[-1]["view"]
    assert await view.interaction_check(FakeInteraction(user_id=2002)) is False
    view.stop()
    await task
    assert ACTION not in manager.paths()


async def test_a_refusal_by_the_server_is_shown_as_a_warning_with_its_words(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, tracker_json())
    manager.add("POST", ACTION, {"done": False, "message": "Release is disabled."})
    interaction = asker(bot)
    task = await start_action(bot, "release", interaction)
    await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
    await task
    embed = interaction.original_edits[0]["embed"]
    assert embed.description == "Release is disabled." and embed.colour.value == 0xB26A00


async def test_a_server_that_does_not_answer_in_time_is_explained(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, tracker_json())
    manager.add("POST", ACTION, (504, {"detail": "The server did not answer in time."}))
    interaction = asker(bot)
    task = await start_action(bot, "release", interaction)
    await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
    with pytest.raises(UserFacingError) as error:
        await task
    assert "Look at /tracker in a minute" in error.value.message("en")
    assert manager.paths().count(ACTION) == 1  # never retried


async def test_any_other_unavailable_manager_keeps_its_usual_message(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, tracker_json())
    manager.add("POST", ACTION, httpx.ReadTimeout("slow"))
    interaction = asker(bot)
    task = await start_action(bot, "collect", interaction)
    await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
    with pytest.raises(ManagerUnavailable):
        await task


async def test_the_server_text_is_escaped_and_cut(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, tracker_json())
    manager.add("POST", ACTION, {"done": True, "message": "@everyone " + "x" * 900})
    interaction = asker(bot)
    task = await start_action(bot, "release", interaction)
    await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
    await task
    text = interaction.original_edits[0]["embed"].description
    assert text.startswith("@​everyone") and len(text) <= 510


async def test_without_a_message_the_result_still_says_what_happened(bot, manager):
    link(bot)
    manager.add("GET", TRACKER, tracker_json())
    manager.add("POST", ACTION, {"done": True}, {"done": False})
    for expected in ("Done", "The server did not do it"):
        interaction = asker(bot)
        task = await start_action(bot, "release", interaction)
        await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
        await task
        assert interaction.original_edits[0]["embed"].description == expected


async def test_without_a_link_nothing_is_asked_for_release(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await call(bot, "release", interaction, "7", "2")
    assert manager.requests == [] and not interaction.response.deferred
