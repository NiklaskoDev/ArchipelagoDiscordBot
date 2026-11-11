import json
from types import SimpleNamespace

import discord
import pytest
from fakes import USER_TOKEN, FakeInteraction, friend_json, friends_json, json_text

from apbot.cogs.friends import FriendsCog
from apbot.errors import NotLinkedError, UserFacingError
from apbot.manager.errors import Conflict, NotFound
from apbot.services.friends import FriendsUnsupported
from apbot.ui.friends import FriendActionView, FriendsView
from apbot.ui.style import ACCENT, SUCCESS

FRIENDS = "/api/friends"
BOB_TOKEN = "apm_bobsTokenForTests"


def link(bot, user_id=1001, manager_id=10, username="alice", token=USER_TOKEN):
    bot.links.save(user_id, manager_id, username, bot.cipher.encrypt(token), token[-4:], "en")


def asker(bot, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, **kwargs)


def show_friends(bot, interaction):
    cog = FriendsCog(bot)
    return cog.friends.callback(cog, interaction)


def add_friend(bot, interaction, member=None, username=None):
    cog = FriendsCog(bot)
    return cog.friend.get_command("add").callback(cog, interaction, member, username)


def overview(**lists):
    return friends_json(**lists)


FULL = overview(
    friends=[friend_json(20, "bob", since=1_700_000_100.0, shared_configs=3, games_together=1)],
    incoming=[friend_json(21, "cy", asked_at=1_700_000_200.0)],
    outgoing=[friend_json(22, "dee", asked_at=1_700_000_300.0)],
    suggestions=[friend_json(23, "eve", games_together=9)],
)


# --- /friends ---------------------------------------------------------------------------------------------------------
async def test_without_a_link_nothing_is_asked_and_nothing_shows_as_thinking(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await show_friends(bot, interaction)
    assert manager.requests == [] and not interaction.response.deferred


async def test_no_friends_says_so_and_has_no_buttons(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    interaction = asker(bot)
    await show_friends(bot, interaction)
    [message] = interaction.messages
    assert message["ephemeral"] is True and "view" not in message
    assert message["embed"].title == "Your friends"
    assert message["embed"].description == "You have no friends on the manager yet. Add someone with /friend add."
    assert interaction.response.defer_kwargs == {"ephemeral": True}


async def test_the_list_is_asked_as_the_person_and_shows_requests_first_then_friends(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, FULL)
    interaction = asker(bot)
    await show_friends(bot, interaction)
    [request] = manager.requests
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    [message] = interaction.messages
    embed = message["embed"]
    assert embed.colour == ACCENT and message["ephemeral"] is True
    assert [(f.name, f.value) for f in embed.fields] == [
        ("Requests for you", "**cy** — <t:1700000200:R>"),
        ("Your requests", "**dee** — <t:1700000300:R>"),
        ("Friends", "**bob** — <t:1700000100:R> — shares 3 configs with you"),
    ]
    assert "eve" not in json_text(embed)  # the manager's suggestions are not shown


async def test_one_shared_config_is_singular_and_none_is_not_mentioned(bot, manager):
    link(bot)
    manager.add(
        "GET", FRIENDS,
        overview(friends=[friend_json(20, "bob", since=100.0, shared_configs=1), friend_json(21, "cy", since=100.0)]),
    )
    interaction = asker(bot)
    await show_friends(bot, interaction)
    [field] = interaction.messages[0]["embed"].fields
    assert field.value.split("\n") == ["**bob** — <t:100:R> — shares 1 config with you", "**cy** — <t:100:R>"]


async def test_only_the_lists_that_have_people_are_shown(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview(incoming=[friend_json(21, "cy", asked_at=100.0)]))
    interaction = asker(bot)
    await show_friends(bot, interaction)
    assert [f.name for f in interaction.messages[0]["embed"].fields] == ["Requests for you"]


async def test_names_cannot_break_the_embed_or_ping(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview(incoming=[friend_json(21, "@everyone **x**", asked_at=100.0)]))
    interaction = asker(bot)
    await show_friends(bot, interaction)
    value = interaction.messages[0]["embed"].fields[0].value
    assert "@everyone" not in value and "\\*\\*x\\*\\*" in value


async def test_the_select_lists_requests_for_the_person_first_then_their_requests_then_friends(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, FULL)
    interaction = asker(bot)
    await show_friends(bot, interaction)
    view = interaction.messages[0]["view"]
    assert isinstance(view, FriendsView)
    assert [(o.label, o.value, o.description) for o in view.select.options] == [
        ("cy", "incoming:21", "Asks to be your friend"),
        ("dee", "outgoing:22", "Your request"),
        ("bob", "friend:20", "Friend"),
    ]
    assert view.select.placeholder == "Choose a person"


async def test_the_select_shows_at_most_25_people_but_the_embed_lists_everyone(bot, manager):
    link(bot)
    people = [friend_json(100 + n, f"friend{n:02d}", since=100.0) for n in range(30)]
    manager.add("GET", FRIENDS, overview(incoming=[friend_json(21, "cy", asked_at=100.0)], friends=people))
    interaction = asker(bot)
    await show_friends(bot, interaction)
    view = interaction.messages[0]["view"]
    options = view.select.options
    assert len(options) == 25 and options[0].label == "cy" and options[-1].label == "friend23"
    fields = {f.name: f.value for f in interaction.messages[0]["embed"].fields}
    assert len(fields["Friends"].split("\n")) == 30 and "friend29" in fields["Friends"]


async def test_the_reply_follows_the_persons_language(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, FULL)
    interaction = asker(bot, locale="de")
    await show_friends(bot, interaction)
    message = interaction.messages[0]
    assert message["embed"].title == "Deine Freunde"
    assert [f.name for f in message["embed"].fields] == ["Anfragen an dich", "Deine Anfragen", "Freunde"]
    assert message["embed"].fields[2].value.endswith("teilt 3 Configs mit dir")
    assert [o.description for o in message["view"].select.options] == [
        "Möchte mit dir befreundet sein", "Deine Anfrage", "Freund",
    ]


async def test_each_look_asks_the_manager_again_so_the_list_is_current(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    await show_friends(bot, asker(bot))
    await show_friends(bot, asker(bot))
    assert len(manager.requests) == 2


async def test_an_older_manager_without_friends_gets_the_clear_message(bot, manager):
    link(bot)  # the fake manager has no /api/friends route: 404
    interaction = asker(bot)
    with pytest.raises(FriendsUnsupported):
        await show_friends(bot, interaction)
    await bot.on_app_command_error(interaction, SimpleNamespace(original=FriendsUnsupported()))
    shown = interaction.followup.sent[-1]
    assert shown["ephemeral"] is True
    assert shown["embed"].description == "This manager has no friends feature yet. Ask an admin to update the Archipelago Manager."


async def test_a_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError):
        await show_friends(bot, asker(bot))
    assert bot.links.get(1001).status == "invalid"


# --- choosing a person and answering ---------------------------------------------------------------------------------------------
async def choose(bot, manager, value, raw=FULL, locale="en-US"):
    """Shows /friends, picks `value` in the select; returns (view of the buttons, the click that chose)."""
    link(bot)
    manager.add("GET", FRIENDS, raw)
    interaction = asker(bot, locale=locale)
    await show_friends(bot, interaction)
    view = interaction.messages[0]["view"]
    view.select._values = [value]
    click = FakeInteraction(bot, user_id=1001, locale=locale)
    await view.select.callback(click)
    [edit] = click.response.edited
    return edit, view


@pytest.mark.parametrize(
    ("value", "labels", "styles", "text"),
    [
        ("incoming:21", ["Accept", "Decline"], ["primary", "secondary"], "**cy** — Asks to be your friend"),
        ("outgoing:22", ["Take back request"], ["secondary"], "**dee** — Your request"),
        ("friend:20", ["Remove friend"], ["danger"], "**bob** — Friend"),
    ],
)
async def test_choosing_a_person_replaces_the_message_with_their_buttons(bot, manager, value, labels, styles, text):
    edit, view = await choose(bot, manager, value)
    assert edit["embed"].description == text
    action_view = edit["view"]
    assert isinstance(action_view, FriendActionView)
    assert [b.label for b in action_view.children] == labels
    assert [b.style.name for b in action_view.children] == styles
    assert view.is_finished() and manager.requests[-1].url.path == FRIENDS  # nothing was changed yet


async def press(bot, manager, value, index, *, user_id=1001, answer=None, method="DELETE", path=None):
    edit, _ = await choose(bot, manager, value)
    action_view = edit["view"]
    if path is not None:
        manager.add(method, path, answer or {"ok": True})
    click = FakeInteraction(bot, user_id=user_id)
    await action_view.children[index].callback(click)
    return click, action_view


async def test_accepting_calls_the_manager_as_the_person_and_replaces_the_buttons_with_the_result(bot, manager):
    click, view = await press(bot, manager, "incoming:21", 0, method="POST", path="/api/friends/21/accept")
    [request] = [r for r in manager.requests if r.url.path != FRIENDS]
    assert (request.method, request.url.path) == ("POST", "/api/friends/21/accept")
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert click.response.deferred
    [edit] = click.original_edits
    assert edit["view"] is None and edit["embed"].description == "You and **cy** are friends now"
    assert edit["embed"].colour == SUCCESS and view.is_finished()


@pytest.mark.parametrize(
    ("value", "index", "path", "text"),
    [
        ("incoming:21", 1, "/api/friends/21", "You declined **cy**'s request"),
        ("outgoing:22", 0, "/api/friends/22", "You took back your request to **dee**"),
        (
            "friend:20", 0, "/api/friends/20",
            "**bob** is no longer your friend. Configs you shared only with each other by name are no longer shared.",
        ),
    ],
)
async def test_declining_taking_back_and_removing_all_delete_the_friendship_entry(bot, manager, value, index, path, text):
    click, _ = await press(bot, manager, value, index, path=path, answer={"removed": "x"})
    [request] = [r for r in manager.requests if r.url.path != FRIENDS]
    assert (request.method, request.url.path) == ("DELETE", path)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert click.original_edits[0]["embed"].description == text and click.original_edits[0]["view"] is None


async def test_the_list_is_asked_again_after_a_change(bot, manager):
    await press(bot, manager, "incoming:21", 0, method="POST", path="/api/friends/21/accept")
    manager.add("GET", FRIENDS, overview())
    await bot.access.call_as(asker(bot), USER_TOKEN, lambda token: bot.friend_directory.overview(1001, token))
    # shown, checked just before the change, changed, and asked again because the change dropped the kept list
    assert [(r.method, r.url.path) for r in manager.requests] == [
        ("GET", FRIENDS), ("GET", FRIENDS), ("POST", "/api/friends/21/accept"), ("GET", FRIENDS),
    ]


async def test_the_result_is_in_the_persons_language(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, FULL)
    interaction = asker(bot, locale="nl")
    await show_friends(bot, interaction)
    view = interaction.messages[0]["view"]
    view.select._values = ["incoming:21"]
    click = FakeInteraction(bot, user_id=1001, locale="nl")
    await view.select.callback(click)
    action_view = click.response.edited[0]["view"]
    assert [b.label for b in action_view.children] == ["Accepteren", "Weigeren"]
    manager.add("POST", "/api/friends/21/accept", {"ok": True})
    done = FakeInteraction(bot, user_id=1001, locale="nl")
    await action_view.children[0].callback(done)
    assert done.original_edits[0]["embed"].description == "Jij en **cy** zijn nu vrienden"


async def test_only_the_person_who_asked_can_use_the_list_and_its_buttons(bot, manager):
    edit, view = await choose(bot, manager, "incoming:21")
    stranger = FakeInteraction(bot, user_id=2002)
    assert await view.interaction_check(stranger) is False
    assert await edit["view"].interaction_check(stranger) is False
    assert stranger.messages[0]["embed"].description == "This question is not for you."
    assert [r.url.path for r in manager.requests] == [FRIENDS]


async def test_a_request_that_is_gone_shows_the_managers_words(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, FULL)
    interaction = asker(bot)
    await show_friends(bot, interaction)
    view = interaction.messages[0]["view"]
    view.select._values = ["incoming:21"]
    click = FakeInteraction(bot, user_id=1001)
    await view.select.callback(click)
    manager.add("POST", "/api/friends/21/accept", (404, {"detail": "That friend request no longer exists"}))
    answer = FakeInteraction(bot, user_id=1001)
    with pytest.raises(NotFound):
        await click.response.edited[0]["view"].children[0].callback(answer)
    await click.response.edited[0]["view"].on_error(answer, NotFound(404, "That friend request no longer exists"), None)
    assert "The manager answered: That friend request no longer exists" in answer.followup.sent[-1]["embed"].description


async def test_a_read_only_token_is_explained_when_answering(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, FULL)
    interaction = asker(bot)
    await show_friends(bot, interaction)
    view = interaction.messages[0]["view"]
    view.select._values = ["friend:20"]
    click = FakeInteraction(bot, user_id=1001)
    await view.select.callback(click)
    manager.add("DELETE", "/api/friends/20", (403, {"detail": "This API token can only read"}))
    with pytest.raises(UserFacingError) as error:
        await click.response.edited[0]["view"].children[0].callback(FakeInteraction(bot, user_id=1001))
    assert "can only read" in error.value.message("en")


# --- /friend add --------------------------------------------------------------------------------------------------------------
def linked_bob(bot):
    link(bot, user_id=2002, manager_id=20, username="bob_manager", token=BOB_TOKEN)


async def test_a_member_is_asked_by_their_linked_manager_username_and_shown_as_a_mention(bot, manager):
    link(bot)
    linked_bob(bot)
    manager.add("GET", FRIENDS, overview())
    manager.add("POST", FRIENDS, {"status": "asked", "user": {"id": 20, "username": "bob_manager"}})
    interaction = asker(bot)
    await add_friend(bot, interaction, SimpleNamespace(id=2002))
    [ask] = [r for r in manager.requests if r.method == "POST"]
    assert json.loads(ask.content) == {"username": "bob_manager"}
    assert ask.headers["Authorization"] == f"Bearer {USER_TOKEN}"  # the asker's token, never the member's
    [message] = interaction.messages
    assert message["ephemeral"] is True and message["embed"].colour == SUCCESS
    assert message["embed"].description == "You asked **<@2002>** to be your friend. They see it in the manager."
    assert "bob_manager" not in json_text(message["embed"])  # the manager username of a member stays out of the reply


async def test_a_typed_username_is_asked_as_typed_and_shown_as_the_manager_names_it(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    manager.add("POST", FRIENDS, {"status": "asked", "user": {"id": 20, "username": "Bob_Under"}})
    interaction = asker(bot)
    await add_friend(bot, interaction, None, "  bob_under ")
    [ask] = [r for r in manager.requests if r.method == "POST"]
    assert json.loads(ask.content) == {"username": "bob_under"}
    assert interaction.messages[0]["embed"].description == "You asked **Bob\\_Under** to be your friend. They see it in the manager."


async def test_when_they_had_asked_already_the_answer_says_you_are_friends_now(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    manager.add("POST", FRIENDS, {"status": "accepted", "user": {"id": 20, "username": "bob"}})
    interaction = asker(bot)
    await add_friend(bot, interaction, None, "bob")
    assert interaction.messages[0]["embed"].description == "You and **bob** are friends now: they had asked you already."


async def test_the_answer_is_in_the_persons_language(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    manager.add("POST", FRIENDS, {"status": "asked", "user": {"id": 20, "username": "bob"}})
    interaction = asker(bot, locale="fr")
    await add_friend(bot, interaction, None, "bob")
    assert interaction.messages[0]["embed"].description == "Tu as demandé à **bob** d'être ton ami. La demande apparaît dans le manager."


async def test_an_answer_without_a_user_falls_back_to_the_name_that_was_asked(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    manager.add("POST", FRIENDS, {"status": "asked"})
    interaction = asker(bot)
    await add_friend(bot, interaction, None, "bob")
    assert "**bob**" in interaction.messages[0]["embed"].description


async def test_a_member_who_has_not_linked_is_an_error_and_nothing_is_asked(bot, manager):
    link(bot)
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await add_friend(bot, interaction, SimpleNamespace(id=3003))
    assert error.value.message("en") == "<@3003> has not linked a manager account"
    assert manager.requests == [] and not interaction.response.deferred


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_neither_a_member_nor_a_username_is_an_error_before_anything_else(bot, manager, blank):
    interaction = asker(bot)  # not even linked: the options are checked first
    with pytest.raises(UserFacingError) as error:
        await add_friend(bot, interaction, None, blank)
    assert error.value.message("en") == "Choose a member or type a manager username"
    assert manager.requests == [] and not interaction.response.deferred


async def test_both_a_member_and_a_username_are_refused(bot, manager):
    link(bot)
    linked_bob(bot)
    with pytest.raises(UserFacingError) as error:
        await add_friend(bot, asker(bot), SimpleNamespace(id=2002), "carol")
    assert error.value.message("en") == "Choose either a member or a username, not both"
    assert manager.requests == []


async def test_without_a_link_nothing_is_asked_and_nothing_shows_as_thinking_for_add(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await add_friend(bot, interaction, None, "bob")
    assert manager.requests == [] and not interaction.response.deferred


async def test_an_older_manager_stops_the_request_before_anything_is_sent(bot, manager):
    link(bot)
    manager.add("POST", FRIENDS, {"status": "asked"})  # an old manager would answer 405; nothing may even try
    with pytest.raises(FriendsUnsupported):
        await add_friend(bot, asker(bot), None, "bob")  # GET /api/friends: 404
    assert [r.method for r in manager.requests] == ["GET"]


async def test_the_managers_refusal_is_passed_on(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    manager.add("POST", FRIENDS, (409, {"detail": "You asked bob already; they have not answered yet"}))
    with pytest.raises(Conflict) as error:
        await add_friend(bot, asker(bot), None, "bob")
    assert error.value.detail == "You asked bob already; they have not answered yet"


async def test_an_unknown_manager_user_is_the_managers_not_found(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    manager.add("POST", FRIENDS, (404, {"detail": "There is no user called 'nobody'"}))
    with pytest.raises(NotFound):
        await add_friend(bot, asker(bot), None, "nobody")


async def test_a_read_only_token_is_explained(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    manager.add("POST", FRIENDS, (403, {"detail": "This API token can only read"}))
    with pytest.raises(UserFacingError) as error:
        await add_friend(bot, asker(bot), None, "bob")
    assert "can only read" in error.value.message("en")


async def test_the_cached_list_is_dropped_after_asking(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, overview())
    manager.add("POST", FRIENDS, {"status": "asked", "user": {"id": 20, "username": "bob"}})
    await add_friend(bot, asker(bot), None, "bob")
    await bot.access.call_as(asker(bot), USER_TOKEN, lambda token: bot.friend_directory.overview(1001, token))
    assert [r.method for r in manager.requests] == ["GET", "POST", "GET"]


def test_the_commands_are_there_and_the_member_option_accepts_users_and_members():
    cog = FriendsCog(SimpleNamespace())
    assert cog.friends.name == "friends" and cog.friend.name == "friend"
    [add] = cog.friend.commands
    assert add.name == "add" and {p.name for p in add.parameters} == {"member", "username"}
    assert not any(isinstance(p.required, bool) and p.required for p in add.parameters)
    assert discord.AppCommandOptionType.user in {p.type for p in add.parameters}


# --- buttons that are out of date --------------------------------------------------------------------------------------
NOW_FRIENDS = overview(friends=[friend_json(21, "cy", since=100.0), friend_json(22, "dee", since=100.0)])
NOW_NOTHING = overview()
NOW_OUTGOING = overview(outgoing=[friend_json(20, "bob", asked_at=100.0)])
NOW_REVERSED = overview(incoming=[friend_json(22, "dee", asked_at=100.0)])  # they ask the person instead


async def press_after(bot, manager, value, index, now, *, locale="en-US"):
    """Shows /friends, picks `value`, then the manager's list changes to `now`; presses button `index`."""
    edit, _ = await choose(bot, manager, value, locale=locale)
    manager.add("GET", FRIENDS, now)
    manager.add("POST", "/api/friends/21/accept", {"ok": True})
    for person in ("20", "21", "22"):
        manager.add("DELETE", f"/api/friends/{person}", {"removed": "friend"})  # would end a friendship
    click = FakeInteraction(bot, user_id=1001, locale=locale)
    await edit["view"].children[index].callback(click)
    return click, edit["view"]


@pytest.mark.parametrize(
    ("value", "index", "now"),
    [
        ("incoming:21", 0, NOW_FRIENDS),  # accept: it was accepted elsewhere
        ("incoming:21", 1, NOW_FRIENDS),  # decline: it became a friendship, which must not end
        ("incoming:21", 0, NOW_NOTHING),  # the asker took the request back
        ("incoming:21", 1, NOW_NOTHING),
        ("outgoing:22", 0, NOW_FRIENDS),  # take back: they accepted meanwhile
        ("outgoing:22", 0, NOW_NOTHING),  # take back: they declined meanwhile
        ("outgoing:22", 0, NOW_REVERSED),  # no request of the person waits for an answer any more
    ],
)
async def test_a_request_that_is_not_open_any_more_is_not_answered_and_nothing_is_changed(bot, manager, value, index, now):
    click, view = await press_after(bot, manager, value, index, now)
    assert [r for r in manager.requests if r.method != "GET"] == []
    [edit] = click.original_edits
    assert edit["embed"].description == "This friend request is no longer open" and edit["view"] is None
    assert view.is_finished()


@pytest.mark.parametrize("now", [NOW_NOTHING, NOW_OUTGOING, NOW_FRIENDS])
async def test_removing_a_friend_who_is_not_one_any_more_sends_nothing(bot, manager, now):
    click, view = await press_after(bot, manager, "friend:20", 0, now)
    assert [r for r in manager.requests if r.method != "GET"] == []
    [edit] = click.original_edits
    assert edit["embed"].description == "**bob** is not your friend any more" and edit["view"] is None
    assert view.is_finished()


async def test_the_notice_for_a_friend_who_is_gone_is_in_the_persons_language(bot, manager):
    click, _ = await press_after(bot, manager, "friend:20", 0, NOW_NOTHING, locale="de")
    assert click.original_edits[0]["embed"].description == "**bob** ist nicht mehr dein Freund"


async def test_the_list_is_read_with_the_pressers_token_before_anything_is_changed(bot, manager):
    link(bot)
    manager.add("GET", FRIENDS, FULL)
    interaction = asker(bot)
    await show_friends(bot, interaction)
    view = interaction.messages[0]["view"]
    view.select._values = ["friend:20"]
    click = FakeInteraction(bot, user_id=1001)
    await view.select.callback(click)
    manager.add("DELETE", "/api/friends/20", {"removed": "friend"})
    await click.response.edited[0]["view"].children[0].callback(FakeInteraction(bot, user_id=1001))
    check, delete = manager.requests[-2:]
    assert (check.method, check.url.path) == ("GET", FRIENDS) and (delete.method, delete.url.path) == ("DELETE", "/api/friends/20")
    assert check.headers["Authorization"] == f"Bearer {USER_TOKEN}"
