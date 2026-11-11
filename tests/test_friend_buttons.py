import pytest
from fakes import (
    BOT_TOKEN,
    USER_TOKEN,
    FakeInteraction,
    FakeMessage,
    FakeMessageable,
    friend_json,
    friends_json,
)

from apbot.errors import NotLinkedError, UserFacingError
from apbot.manager.errors import NotFound
from apbot.ui.dynamic import FriendAnswerButton, friend_request_view
from apbot.ui.style import SUCCESS

FRIENDS = "/api/friends"
ACCEPT_PATH = "/api/friends/21/accept"
DECLINE_PATH = "/api/friends/21"


def link(bot, user_id=1001, manager_id=10, token=USER_TOKEN):
    bot.links.save(user_id, manager_id, "alice", bot.cipher.encrypt(token), token[-4:], "en")


def pressed(bot, user_id=1001, locale="en-US") -> FakeInteraction:
    interaction = FakeInteraction(bot, locale=locale, user_id=user_id)
    interaction.message = FakeMessage(1, FakeMessageable(2))
    return interaction


@pytest.fixture(autouse=True)
def the_request_is_open(manager):
    """By default cy's request is waiting for the person; tests for a request that is not set the list themselves."""
    manager.add("GET", FRIENDS, friends_json(incoming=[friend_json(21, "cy", asked_at=100.0)]))


def changes(manager) -> list:
    """What was sent to the manager apart from looking at the friend list."""
    return [r for r in manager.requests if r.method != "GET"]


def template():
    return FriendAnswerButton.__discord_ui_compiled_template__


# --- the ids -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("custom_id", "action", "user"),
    [("apbot:fr:accept:21", "accept", 21), ("apbot:fr:decline:123456789", "decline", 123456789), ("apbot:fr:accept:0", "accept", 0)],
)
async def test_the_ids_carry_the_action_and_the_askers_number(bot, custom_id, action, user):
    match = template().fullmatch(custom_id)
    item = await FriendAnswerButton.from_custom_id(pressed(bot), None, match)
    assert (item._accepting, item._other_id, item.custom_id) == (action == "accept", user, custom_id)


@pytest.mark.parametrize(
    "custom_id",
    [
        "apbot:fr:accept",  # no user
        "apbot:fr:accept:abc",
        "apbot:fr:accept:-2",
        "apbot:fr:accept:21:3",
        "apbot:fr:accept:1234567890",  # more than nine digits
        "apbot:fr:accept:21 ",
        "apbot:fr:accept:21\n",
        "xapbot:fr:accept:21",
        "apbot:fr:remove:21",  # an action that does not exist
        "apbot:fr:Accept:21",
        "apbot:fr:accept:２１",  # full-width digits
        "apbot:fr:accept:apbot:fr:accept:21",
        "apbot:ho:accept:21:2",  # the handover button's id
    ],
)
def test_a_malformed_id_does_not_match(custom_id):
    assert template().fullmatch(custom_id) is None


def test_the_view_has_both_buttons_and_never_times_out():
    view = friend_request_view(21, "de")
    assert [c.custom_id for c in view.children] == ["apbot:fr:accept:21", "apbot:fr:decline:21"]
    assert [c.item.label for c in view.children] == ["Annehmen", "Ablehnen"]
    assert [c.item.style.name for c in view.children] == ["primary", "secondary"]
    assert view.timeout is None


# --- answering ---------------------------------------------------------------------------------------------------------
async def test_accepting_calls_the_manager_with_the_clickers_token_and_answers_privately(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, {"ok": True})
    interaction = pressed(bot)
    await FriendAnswerButton("accept", 21).callback(interaction)
    [request] = changes(manager)
    assert (request.method, request.url.path, request.content) == ("POST", ACCEPT_PATH, b"")
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}" and BOT_TOKEN not in str(request.headers)
    assert interaction.response.defer_kwargs == {"ephemeral": True, "thinking": True}
    [answer] = interaction.messages
    assert answer["ephemeral"] is True and answer["embed"].description == "You are friends now"
    assert answer["embed"].colour == SUCCESS
    assert interaction.message.edits == [{"view": None}]  # the buttons are gone


async def test_declining_deletes_the_request_and_removes_the_buttons(bot, manager):
    link(bot)
    manager.add("DELETE", DECLINE_PATH, {"removed": "declined"})
    interaction = pressed(bot)
    await FriendAnswerButton("decline", 21).callback(interaction)
    [request] = changes(manager)
    assert (request.method, request.url.path) == ("DELETE", DECLINE_PATH)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert interaction.messages[0]["embed"].description == "You declined the friend request"
    assert interaction.messages[0]["ephemeral"] is True
    assert interaction.message.edits == [{"view": None}]


async def test_the_answer_is_in_the_clickers_language(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, {"ok": True})
    interaction = pressed(bot, locale="de")
    await FriendAnswerButton("accept", 21).callback(interaction)
    assert interaction.messages[0]["embed"].description == "Ihr seid jetzt befreundet"


async def test_whoever_clicks_acts_as_themselves_never_as_the_person_the_message_was_sent_to(bot, manager):
    link(bot, 1001, 10, "apm_tokenOfTheFirstUser")
    link(bot, 2002, 20, "apm_tokenOfTheSecondUser")
    manager.add("POST", ACCEPT_PATH, {"ok": True})
    await FriendAnswerButton("accept", 21).callback(pressed(bot, user_id=2002))
    assert manager.requests[0].headers["Authorization"] == "Bearer apm_tokenOfTheSecondUser"


async def test_someone_who_is_not_linked_is_told_so_before_anything_is_deferred(bot, manager):
    interaction = pressed(bot)
    with pytest.raises(NotLinkedError):
        await FriendAnswerButton("accept", 21).callback(interaction)
    assert manager.requests == [] and not interaction.response.deferred


async def test_a_request_that_is_gone_shows_the_managers_words_and_keeps_the_buttons(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, (404, {"detail": "That friend request no longer exists"}))
    interaction = pressed(bot)
    button = FriendAnswerButton("accept", 21)
    with pytest.raises(NotFound):
        await button.callback(interaction)
    assert interaction.message.edits == [] and interaction.messages == []
    await button.on_error(interaction, NotFound(404, "That friend request no longer exists"))  # what discord.py does
    assert "The manager answered: That friend request no longer exists" in interaction.followup.sent[-1]["embed"].description


async def test_a_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError):
        await FriendAnswerButton("accept", 21).callback(pressed(bot))
    assert bot.links.get(1001).status == "invalid"


async def test_the_clickers_cached_friend_list_is_dropped(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, {"ok": True})
    await bot.friend_directory.overview(1001, USER_TOKEN)
    await FriendAnswerButton("accept", 21).callback(pressed(bot))
    await bot.friend_directory.overview(1001, USER_TOKEN)
    assert [r.method for r in manager.requests] == ["GET", "GET", "POST", "GET"]


# --- a message that is out of date -------------------------------------------------------------------------------------
LISTS = {
    "already friends": friends_json(friends=[friend_json(21, "cy", since=100.0)]),
    "request withdrawn": friends_json(),
    "asked by me instead": friends_json(outgoing=[friend_json(21, "cy", asked_at=100.0)]),
    "someone else asks": friends_json(incoming=[friend_json(22, "dee", asked_at=100.0)]),
}


@pytest.mark.parametrize("situation", LISTS)
@pytest.mark.parametrize("action", ["accept", "decline"])
async def test_a_request_that_is_not_open_any_more_is_not_answered_and_nothing_is_changed(bot, manager, situation, action):
    link(bot)
    manager.add("GET", FRIENDS, LISTS[situation])
    manager.add("POST", ACCEPT_PATH, {"ok": True})
    manager.add("DELETE", DECLINE_PATH, {"removed": "friend"})  # would end the friendship: must never be sent
    interaction = pressed(bot)
    await FriendAnswerButton(action, 21).callback(interaction)
    assert changes(manager) == []
    [answer] = interaction.messages
    assert answer["ephemeral"] is True and answer["embed"].description == "This friend request is no longer open"
    assert interaction.message.edits == [{"view": None}]  # the buttons are gone


async def test_the_current_list_is_read_even_when_an_old_one_is_cached(bot, manager):
    link(bot)
    await bot.friend_directory.overview(1001, USER_TOKEN)  # the request is open in what the bot remembers
    manager.add("GET", FRIENDS, LISTS["already friends"])  # ...but it was accepted in the manager since
    manager.add("DELETE", DECLINE_PATH, {"removed": "friend"})
    await FriendAnswerButton("decline", 21).callback(pressed(bot))
    assert changes(manager) == []


async def test_the_list_is_read_with_the_clickers_token_before_anything_is_changed(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, {"ok": True})
    await FriendAnswerButton("accept", 21).callback(pressed(bot))
    assert [(r.method, r.url.path) for r in manager.requests] == [("GET", FRIENDS), ("POST", ACCEPT_PATH)]
    assert manager.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"


async def test_the_notice_for_a_request_that_is_not_open_is_in_the_clickers_language(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, LISTS["request withdrawn"])
    interaction = pressed(bot, locale="de")
    await FriendAnswerButton("decline", 21).callback(interaction)
    assert interaction.messages[0]["embed"].description == "Diese Freundschaftsanfrage ist nicht mehr offen"


async def test_an_older_manager_without_friends_is_explained_and_nothing_is_changed(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, (404, {"detail": "Not Found"}))
    with pytest.raises(UserFacingError):
        await FriendAnswerButton("decline", 21).callback(pressed(bot))
    assert changes(manager) == []
