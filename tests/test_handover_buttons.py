import pytest
from fakes import BOT_TOKEN, USER_TOKEN, FakeInteraction, FakeMessage, FakeMessageable, forbidden, game_json

from apbot.errors import NotLinkedError, UserFacingError
from apbot.manager.errors import Conflict
from apbot.ui.dynamic import HandoverAcceptButton, HandoverDeclineButton, handover_view

ACCEPT_PATH = "/api/games/7/slots/2/handover/accept"
DECLINE_PATH = "/api/games/7/slots/2/handover"


def link(bot, user_id=1001, manager_id=10, token=USER_TOKEN):
    bot.links.save(user_id, manager_id, "alice", bot.cipher.encrypt(token), token[-4:], "en")


def pressed(bot, user_id=1001, locale="en-US") -> FakeInteraction:
    interaction = FakeInteraction(bot, locale=locale, user_id=user_id)
    interaction.message = FakeMessage(1, FakeMessageable(2))
    return interaction


# --- the ids -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("button", "custom_id", "game", "slot"),
    [
        (HandoverAcceptButton, "apbot:ho:accept:7:2", 7, 2),
        (HandoverDeclineButton, "apbot:ho:decline:123456789:0", 123456789, 0),
    ],
)
async def test_the_ids_carry_the_game_and_slot_numbers(bot, button, custom_id, game, slot):
    match = button.__discord_ui_compiled_template__.fullmatch(custom_id)
    item = await button.from_custom_id(pressed(bot), None, match)
    assert (item._game_id, item._slot, item.custom_id) == (game, slot, custom_id)


@pytest.mark.parametrize(
    "custom_id",
    [
        "apbot:ho:accept:7",  # no slot
        "apbot:ho:accept:abc:2",
        "apbot:ho:accept:7:-2",
        "apbot:ho:accept:7:2:3",
        "apbot:ho:accept:1234567890:2",  # more than nine digits
        "apbot:ho:accept:7:2 ",
        "apbot:ho:accept:7:2\n",
        "xapbot:ho:accept:7:2",
        "apbot:ho:decline:7:2",  # the other button's id
        "apbot:ho:accept:７:２",  # full-width digits
        "apbot:ho:accept:7:apbot:ho:accept:7:2",
    ],
)
def test_a_malformed_id_does_not_match_the_accept_button(custom_id):
    assert HandoverAcceptButton.__discord_ui_compiled_template__.fullmatch(custom_id) is None


def test_the_decline_button_does_not_match_the_accept_id():
    assert HandoverDeclineButton.__discord_ui_compiled_template__.fullmatch("apbot:ho:accept:7:2") is None


def test_the_view_has_both_buttons_and_never_times_out():
    view = handover_view(7, 2, "de")
    assert [c.custom_id for c in view.children] == ["apbot:ho:accept:7:2", "apbot:ho:decline:7:2"]
    assert [c.item.label for c in view.children] == ["Slot übernehmen", "Ablehnen"]
    assert view.timeout is None


# --- taking over -------------------------------------------------------------------------------------------------------
async def test_taking_over_calls_the_manager_with_the_clickers_token_and_answers_privately(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, game_json(7, "Friday Run"))
    interaction = pressed(bot)
    await HandoverAcceptButton(7, 2).callback(interaction)
    [request] = manager.requests
    assert (request.method, request.url.path) == ("POST", ACCEPT_PATH)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}" and BOT_TOKEN not in str(request.headers)
    assert interaction.response.defer_kwargs == {"ephemeral": True, "thinking": True}
    [answer] = interaction.messages
    assert answer["ephemeral"] is True and answer["embed"].description == "You took over a slot in **Friday Run**"
    assert interaction.message.edits == [{"view": None}]  # the buttons are gone


async def test_the_answer_is_in_the_clickers_language(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, game_json(7, "Friday Run"))
    interaction = pressed(bot, locale="de")
    await HandoverAcceptButton(7, 2).callback(interaction)
    assert interaction.messages[0]["embed"].description == "Du hast einen Slot in **Friday Run** übernommen"


async def test_whoever_clicks_acts_as_themselves_never_as_the_person_the_offer_was_sent_to(bot, manager):
    link(bot, 1001, 10, "apm_tokenOfTheFirstUser")
    link(bot, 2002, 20, "apm_tokenOfTheSecondUser")
    manager.add("POST", ACCEPT_PATH, game_json(7))
    await HandoverAcceptButton(7, 2).callback(pressed(bot, user_id=2002))
    assert manager.requests[0].headers["Authorization"] == "Bearer apm_tokenOfTheSecondUser"


async def test_someone_who_is_not_linked_is_told_so_before_anything_is_deferred(bot, manager):
    interaction = pressed(bot)
    with pytest.raises(NotLinkedError):
        await HandoverAcceptButton(7, 2).callback(interaction)
    assert manager.requests == [] and not interaction.response.deferred


async def test_an_offer_that_is_gone_shows_the_managers_words_and_keeps_the_buttons(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, (409, {"detail": "This slot is no longer being handed over"}))
    interaction = pressed(bot)
    with pytest.raises(Conflict):
        await HandoverAcceptButton(7, 2).callback(interaction)
    assert interaction.message.edits == []
    await HandoverAcceptButton(7, 2).on_error(interaction, Conflict(409, "This slot is no longer being handed over"))
    assert interaction.followup.sent[-1]["embed"].description == (
        "The manager answered: This slot is no longer being handed over"
    )
    assert interaction.followup.sent[-1]["ephemeral"] is True


async def test_a_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, (401, {"detail": "no"}))
    with pytest.raises(UserFacingError):
        await HandoverAcceptButton(7, 2).callback(pressed(bot))
    assert bot.links.get(1001).status == "invalid"


async def test_a_read_only_token_is_explained(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, (403, {"detail": "This API token can only read"}))
    with pytest.raises(UserFacingError) as error:
        await HandoverAcceptButton(7, 2).callback(pressed(bot))
    assert "can only read" in error.value.message("en")


async def test_a_message_that_cannot_be_edited_does_not_hide_the_result(bot, manager):
    link(bot)
    manager.add("POST", ACCEPT_PATH, game_json(7))
    interaction = pressed(bot)

    async def refuse(**kwargs):
        raise forbidden()

    interaction.message.edit = refuse
    await HandoverAcceptButton(7, 2).callback(interaction)
    assert len(interaction.messages) == 1


# --- declining ---------------------------------------------------------------------------------------------------------
async def test_declining_deletes_the_offer_as_the_clicker_and_answers_privately(bot, manager):
    link(bot)
    manager.add("DELETE", DECLINE_PATH, {"declined": True})
    interaction = pressed(bot)
    await HandoverDeclineButton(7, 2).callback(interaction)
    [request] = manager.requests
    assert (request.method, request.url.path) == ("DELETE", DECLINE_PATH)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert interaction.messages[0]["embed"].description == "You declined the offer"
    assert interaction.messages[0]["ephemeral"] is True
    assert interaction.message.edits == [{"view": None}]


async def test_declining_without_a_link_asks_nothing(bot, manager):
    interaction = pressed(bot)
    with pytest.raises(NotLinkedError):
        await HandoverDeclineButton(7, 2).callback(interaction)
    assert manager.requests == [] and not interaction.response.deferred
