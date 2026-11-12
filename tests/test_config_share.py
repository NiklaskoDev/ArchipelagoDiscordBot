import asyncio
import json

import pytest
from fakes import USER_TOKEN, FakeInteraction, config_json, friend_json, friends_json, json_text

from apbot.cogs.configs import ConfigsCog
from apbot.errors import NotLinkedError, UserFacingError
from apbot.manager.errors import BadRequest, Forbidden
from apbot.services.friends import FriendsUnsupported
from apbot.ui.style import SUCCESS
from apbot.ui.views import MultiSelectView

FRIENDS = "/api/friends"
CONFIGS = "/api/configs"
SHARE = "/api/configs/40/shared"
BOB = friend_json(20, "bob", since=100.0)
CY = friend_json(21, "cy", since=100.0)


def link(bot, user_id=1001):
    bot.links.save(user_id, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def asker(bot, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, **kwargs)


def share(bot, interaction, config="40", audience="friends"):
    cog = ConfigsCog(bot)
    return cog.config.get_command("share").callback(cog, interaction, config, audience)


def mine(**overrides) -> dict:
    return config_json(**{"config_id": 40, "name": "alice.yaml", "user_id": 10, "owner": "alice", **overrides})


def setup_manager(manager, *, friends=(BOB, CY), configs=None):
    manager.add("GET", FRIENDS, friends_json(friends=list(friends)))
    manager.add("GET", CONFIGS, configs if configs is not None else [mine(), config_json(41, "other.yaml", user_id=10)])
    manager.add("PUT", SHARE, mine(shared=True))


def puts(manager) -> list[dict]:
    return [json.loads(r.content) for r in manager.requests if r.method == "PUT"]


async def ask(bot, interaction, *args):
    """Starts a command that asks a question; returns its task once the question is shown."""
    task = asyncio.create_task(share(bot, interaction, *args))
    for _ in range(100):
        if interaction.followup.sent or task.done():
            break
        await asyncio.sleep(0)
    return task


# --- before anything is asked ----------------------------------------------------------------------------------------------
async def test_without_a_link_nothing_is_asked_and_nothing_shows_as_thinking(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await share(bot, interaction)
    assert manager.requests == [] and not interaction.response.deferred


@pytest.mark.parametrize("config", ["abc", "4x", "-1", "١٢"])
async def test_a_config_that_is_not_a_number_is_refused_before_any_manager_call(bot, manager, config):
    link(bot)
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await share(bot, interaction, config)
    assert error.value.message("en") == "Pick one of your configs from the list"
    assert manager.requests == [] and not interaction.response.deferred


async def test_an_older_manager_without_friends_stops_everything_before_any_change(bot, manager):
    link(bot)
    manager.add("GET", CONFIGS, [mine()])  # no /api/friends route: 404, like FastAPI
    manager.add("PUT", SHARE, mine())
    for audience in ("everyone", "friends", "chosen", "stop"):
        with pytest.raises(FriendsUnsupported) as error:
            await share(bot, asker(bot), audience=audience)
    assert error.value.message("en") == "This manager has no friends feature yet. Ask an admin to update the Archipelago Manager."
    assert puts(manager) == [] and CONFIGS not in manager.paths()


async def test_a_config_that_is_not_in_the_persons_own_list_is_refused_and_nothing_is_changed(bot, manager):
    link(bot)
    setup_manager(manager, configs=[config_json(41, "other.yaml", user_id=10)])
    with pytest.raises(UserFacingError) as error:
        await share(bot, asker(bot), "40")
    assert error.value.message("en") == "Pick one of your configs from the list"
    assert puts(manager) == []


async def test_the_list_of_configs_is_asked_without_friends_so_only_their_own_can_be_shared(bot, manager):
    link(bot)
    setup_manager(manager)
    await share(bot, asker(bot))
    [request] = [r for r in manager.requests if r.url.path == CONFIGS]
    assert request.url.query == b"" and request.headers["Authorization"] == f"Bearer {USER_TOKEN}"


# --- friends, stop ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("audience", "body", "text"),
    [
        ("friends", {"shared": True, "share_with": "friends", "friends": []}, "**alice.yaml** is shared with all your friends"),
        ("stop", {"shared": False, "share_with": "everyone", "friends": []}, "**alice.yaml** is no longer shared"),
    ],
)
async def test_sharing_with_friends_and_stopping_send_the_right_body_as_the_person(bot, manager, audience, body, text):
    link(bot)
    setup_manager(manager)
    interaction = asker(bot)
    await share(bot, interaction, audience=audience)
    assert puts(manager) == [body]
    [put] = [r for r in manager.requests if r.method == "PUT"]
    assert put.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    [message] = interaction.messages
    assert (message["embed"].description, message["embed"].colour, message["ephemeral"]) == (text, SUCCESS, True)


async def test_the_confirmation_is_not_asked_for_friends_or_stop(bot, manager):
    link(bot)
    setup_manager(manager)
    interaction = asker(bot)
    await share(bot, interaction, audience="friends")
    assert all("view" not in message for message in interaction.messages)


async def test_a_config_name_cannot_format_or_ping(bot, manager):
    link(bot)
    setup_manager(manager, configs=[mine(name="@everyone **x**")])
    interaction = asker(bot)
    await share(bot, interaction)
    description = interaction.messages[0]["embed"].description
    assert "@everyone" not in description and "\\*\\*x\\*\\*" in description


async def test_the_reply_follows_the_persons_language(bot, manager):
    link(bot)
    setup_manager(manager)
    interaction = asker(bot, locale="de")
    await share(bot, interaction, audience="stop")
    assert interaction.messages[0]["embed"].description == "**alice.yaml** wird nicht mehr geteilt"


# --- everyone: asks first ------------------------------------------------------------------------------------------------------
async def test_sharing_with_everyone_asks_first_and_changes_nothing_until_confirmed(bot, manager):
    link(bot)
    setup_manager(manager)
    interaction = asker(bot)
    task = await ask(bot, interaction, "40", "everyone")
    [question] = interaction.followup.sent
    assert question["embed"].description == (
        "Share **alice.yaml** with every player? Everyone on the manager can then read it, player names included, "
        "and copy it."
    )
    assert [b.label for b in question["view"].children] == ["Share with everyone", "Cancel"]
    assert question["view"].children[0].style.name == "primary" and question["ephemeral"] is True
    assert puts(manager) == []
    await question["view"].children[0].callback(FakeInteraction(user_id=1001))
    await task
    assert puts(manager) == [{"shared": True, "share_with": "everyone", "friends": []}]
    [edit] = interaction.original_edits
    assert edit["embed"].description == "**alice.yaml** is shared with every player"
    assert edit["embed"].colour == SUCCESS and edit["view"] is None


async def test_cancelling_the_question_sends_nothing_and_leaves_the_suggestions_alone(bot, manager):
    link(bot)
    setup_manager(manager)
    await bot.friend_directory.library(1001, USER_TOKEN)
    interaction = asker(bot)
    task = await ask(bot, interaction, "40", "everyone")
    await interaction.followup.sent[-1]["view"].children[1].callback(FakeInteraction(user_id=1001))
    await task
    assert puts(manager) == [] and interaction.original_edits == []
    assert bot.friend_directory.cached_library(1001) is not None


async def test_only_the_person_who_asked_can_answer_the_question(bot, manager):
    link(bot)
    setup_manager(manager)
    interaction = asker(bot)
    task = await ask(bot, interaction, "40", "everyone")
    view = interaction.followup.sent[-1]["view"]
    stranger = FakeInteraction(user_id=2002)
    assert await view.interaction_check(stranger) is False
    assert puts(manager) == []
    await view.children[1].callback(FakeInteraction(user_id=1001))
    await task


async def test_the_question_follows_the_persons_language(bot, manager):
    link(bot)
    setup_manager(manager)
    interaction = asker(bot, locale="de")
    task = await ask(bot, interaction, "40", "everyone")
    view = interaction.followup.sent[-1]["view"]
    assert [b.label for b in view.children] == ["Mit allen teilen", "Abbrechen"]
    await view.children[1].callback(FakeInteraction(user_id=1001))
    await task


# --- chosen friends ----------------------------------------------------------------------------------------------------------------
async def chooser(bot, manager, *, friends=(BOB, CY), config=None, **kwargs):
    link(bot)
    setup_manager(manager, friends=friends, configs=[config or mine()])
    interaction = asker(bot, **kwargs)
    await share(bot, interaction, audience="chosen")
    [message] = interaction.messages
    return interaction, message


async def test_choosing_friends_offers_the_friends_with_the_current_ones_ticked(bot, manager):
    config = mine(shared=True, share_with="chosen", shared_with=[{"id": 21, "username": "cy"}])
    _, message = await chooser(bot, manager, config=config)
    view = message["view"]
    assert isinstance(view, MultiSelectView) and message["ephemeral"] is True
    assert [(o.label, o.value, o.default) for o in view.select.options] == [("bob", "20", False), ("cy", "21", True)]
    assert (view.select.placeholder, view.select.min_values, view.select.max_values) == ("Choose friends", 0, 2)
    assert message["embed"].description == "Choose the friends who may see **alice.yaml**."
    assert puts(manager) == []


async def test_choosing_saves_at_once_with_exactly_the_chosen_friends_and_names_them(bot, manager):
    _, message = await chooser(bot, manager)
    view = message["view"]
    view.select._values = ["21", "20"]
    click = FakeInteraction(bot, user_id=1001)
    await view.select.callback(click)
    assert puts(manager) == [{"shared": True, "share_with": "chosen", "friends": [21, 20]}]
    [put] = [r for r in manager.requests if r.method == "PUT"]
    assert put.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    [edit] = click.response.edited
    assert edit["embed"].description == "**alice.yaml** is shared with: cy, bob"
    assert edit["embed"].colour == SUCCESS and edit["view"] is None and view.is_finished()


async def test_choosing_nobody_is_refused_and_nothing_is_sent(bot, manager):
    _, message = await chooser(bot, manager)
    view = message["view"]
    view.select._values = []
    with pytest.raises(UserFacingError) as error:
        await view.select.callback(FakeInteraction(bot, user_id=1001))
    assert error.value.message("en") == "Choose at least one friend, or pick stop to stop sharing"
    assert puts(manager) == []


async def test_without_friends_there_is_nobody_to_choose_and_nothing_is_shown(bot, manager):
    link(bot)
    setup_manager(manager, friends=())
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await share(bot, interaction, audience="chosen")
    assert error.value.message("en") == "You have no friends to share it with yet. Add some with /friend add"
    assert interaction.messages == [] and puts(manager) == []


async def test_friend_names_in_the_menu_cannot_break_it_and_in_the_result_cannot_ping(bot, manager):
    _, message = await chooser(bot, manager, friends=(friend_json(20, "@everyone **x**", since=1.0),))
    view = message["view"]
    view.select._values = ["20"]
    click = FakeInteraction(bot, user_id=1001)
    await view.select.callback(click)
    description = click.response.edited[0]["embed"].description
    assert "@everyone" not in description and "\\*\\*x\\*\\*" in description


async def test_only_the_person_who_asked_can_use_the_menu(bot, manager):
    _, message = await chooser(bot, manager)
    stranger = FakeInteraction(bot, user_id=2002)
    assert await message["view"].interaction_check(stranger) is False
    assert puts(manager) == []


async def test_with_more_than_25_friends_the_menu_shows_25_says_so_and_keeps_the_hidden_ones_that_already_see_it(bot, manager):
    people = [friend_json(100 + n, f"friend{n:02d}", since=1.0) for n in range(30)]
    config = mine(shared=True, share_with="chosen", shared_with=[{"id": 129, "username": "friend29"}])
    _, message = await chooser(bot, manager, friends=people, config=config)
    view = message["view"]
    assert len(view.select.options) == 25 and view.select.options[-1].label == "friend24"
    assert message["embed"].description.endswith("The menu shows the first 25 friends. Use the manager for the others.")
    view.select._values = ["100"]
    await view.select.callback(FakeInteraction(bot, user_id=1001))
    assert puts(manager) == [{"shared": True, "share_with": "chosen", "friends": [100, 129]}]


async def test_with_exactly_25_friends_there_is_no_note(bot, manager):
    people = [friend_json(100 + n, f"friend{n:02d}", since=1.0) for n in range(25)]
    _, message = await chooser(bot, manager, friends=people)
    assert len(message["view"].select.options) == 25 and "first 25" not in json_text(message["embed"])


async def test_choosing_in_a_menu_older_than_the_friendship_the_manager_refuses_in_its_words(bot, manager):
    _, message = await chooser(bot, manager)
    manager.add("PUT", SHARE, (400, {"detail": "You can share a config by name only with your friends"}))
    view = message["view"]
    view.select._values = ["20"]
    with pytest.raises(BadRequest):
        await view.select.callback(FakeInteraction(bot, user_id=1001))


# --- caches ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("audience", ["friends", "stop"])
async def test_the_suggestions_of_configs_are_asked_again_after_a_change(bot, manager, audience):
    link(bot)
    setup_manager(manager)
    await bot.signup_directory.configs(1001, USER_TOKEN)
    await bot.friend_directory.library(1001, USER_TOKEN)
    await share(bot, asker(bot), audience=audience)
    assert bot.friend_directory.cached_library(1001) is None
    manager.requests.clear()
    await bot.signup_directory.configs(1001, USER_TOKEN)
    await bot.friend_directory.library(1001, USER_TOKEN)
    assert [r.url.path for r in manager.requests] == [CONFIGS, CONFIGS]


async def test_the_suggestions_are_asked_again_after_choosing_friends_and_after_confirming_everyone(bot, manager):
    _, message = await chooser(bot, manager)
    await bot.signup_directory.configs(1001, USER_TOKEN)
    await bot.friend_directory.library(1001, USER_TOKEN)
    message["view"].select._values = ["20"]
    await message["view"].select.callback(FakeInteraction(bot, user_id=1001))
    assert bot.friend_directory.cached_library(1001) is None
    manager.requests.clear()
    await bot.signup_directory.configs(1001, USER_TOKEN)
    assert [r.url.path for r in manager.requests] == [CONFIGS]

    await bot.friend_directory.library(1001, USER_TOKEN)
    interaction = asker(bot)
    task = await ask(bot, interaction, "40", "everyone")
    await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
    await task
    assert bot.friend_directory.cached_library(1001) is None


async def test_a_failed_change_leaves_the_suggestions_alone(bot, manager):
    link(bot)
    setup_manager(manager)
    manager.add("PUT", SHARE, (403, {"detail": "Only its owner can share a config"}))
    await bot.friend_directory.library(1001, USER_TOKEN)
    with pytest.raises(Forbidden):
        await share(bot, asker(bot), audience="friends")
    assert bot.friend_directory.cached_library(1001) is not None


# --- the person's token ------------------------------------------------------------------------------------------------------------------
async def test_a_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    setup_manager(manager)
    manager.add("PUT", SHARE, (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError):
        await share(bot, asker(bot), audience="friends")
    assert bot.links.get(1001).status == "invalid"


async def test_a_read_only_token_is_explained_and_nothing_else_is_used(bot, manager):
    link(bot)
    setup_manager(manager)
    manager.add("PUT", SHARE, (403, {"detail": "This API token can only read"}))
    with pytest.raises(UserFacingError) as error:
        await share(bot, asker(bot), audience="friends")
    assert "can only read" in error.value.message("en")
    assert all(r.headers["Authorization"] == f"Bearer {USER_TOKEN}" for r in manager.requests)
