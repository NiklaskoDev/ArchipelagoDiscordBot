import json
from types import SimpleNamespace

import pytest
from fakes import BOT_TOKEN, USER_TOKEN, FakeInteraction, game_json, slot_json

from apbot.cogs.slots import SlotsCog
from apbot.errors import NotLinkedError, UserFacingError
from apbot.manager.errors import Conflict
from apbot.services.slots import SlotDirectory, number_or_none, parse_slot_id
from apbot.services.ttl_cache import TtlCache

GAME = "/api/games/7"
STATUS = f"{GAME}/slots/2/status"
CLAIM = f"{GAME}/slots/2/claim"
HANDOVER = f"{GAME}/slots/2/handover"


def link(bot, user_id=1001, manager_id=10, username="alice", token=USER_TOKEN):
    bot.links.save(user_id, manager_id, username, bot.cipher.encrypt(token), token[-4:], "en")


def asker(bot, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, **kwargs)


def detail(**overrides) -> dict:
    slots = [slot_json(1, "Alice"), slot_json(2, "Bob", "Hollow Knight")]
    return game_json(7, "Friday Run", **{"slots": slots, **overrides})


def call(bot, name: str, interaction, *args, **kwargs):
    cog = SlotsCog(bot)
    command = getattr(cog, name) if name in ("status", "claim") else cog.handover.get_command(name)
    return command.callback(cog, interaction, *args, **kwargs)


def body(manager, index=-1) -> dict:
    return json.loads(manager.requests[index].content)


# --- /status ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("choice", "sent", "text"),
    [
        ("playing", "playing", "The status of **Bob** is now: Playing"),
        ("stuck", "stuck", "The status of **Bob** is now: Stuck"),
        ("away", "away", "The status of **Bob** is now: Away"),
        ("clear", None, "The status of **Bob** is cleared"),
    ],
)
async def test_status_sets_what_the_person_says_as_the_person(bot, manager, choice, sent, text):
    link(bot)
    manager.add("PUT", STATUS, detail())
    interaction = asker(bot)
    note = None if choice == "clear" else "waiting for a key"
    await call(bot, "status", interaction, "7", choice, "2", note)
    [request] = manager.requests
    assert (request.method, request.url.path) == ("PUT", STATUS)
    assert body(manager) == {"status": sent, "note": note or ""}
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}" and BOT_TOKEN not in str(request.headers)
    [message] = interaction.messages
    assert message["embed"].description == text and message["ephemeral"] is True


async def test_a_note_with_clear_is_refused_before_anything_is_asked(bot, manager):
    link(bot)
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "status", interaction, "7", "clear", "2", "some note")
    assert error.value.message("en") == "A note goes with a status; clearing the status removes it"
    assert manager.requests == [] and not interaction.response.deferred


async def test_clear_without_a_note_is_fine(bot, manager):
    link(bot)
    manager.add("PUT", STATUS, detail())
    await call(bot, "status", asker(bot), "7", "clear", "2")
    assert body(manager) == {"status": None, "note": ""}


async def test_status_without_a_note_sends_an_empty_one(bot, manager):
    link(bot)
    manager.add("PUT", STATUS, detail())
    await call(bot, "status", asker(bot), "7", "away", "2")
    assert body(manager) == {"status": "away", "note": ""}


async def test_status_shows_the_managers_refusal(bot, manager):
    link(bot)
    manager.add("PUT", STATUS, (403, {"detail": "Only the player of this slot can say how they are doing"}))
    with pytest.raises(Exception) as error:
        await call(bot, "status", asker(bot), "7", "away", "2")
    assert error.value.detail == "Only the player of this slot can say how they are doing"


async def test_status_finds_the_only_slot_when_none_is_given(bot, manager):
    link(bot)
    manager.add("GET", GAME, game_json(7, my_slots=[{"slot": 2, "name": "Bob", "game": "x"}]))
    manager.add("PUT", STATUS, detail())
    await call(bot, "status", asker(bot), "7", "stuck")
    assert manager.paths() == [GAME, STATUS]


async def test_status_in_german(bot, manager):
    link(bot)
    manager.add("PUT", STATUS, detail())
    interaction = asker(bot, locale="de")
    await call(bot, "status", interaction, "7", "stuck", "2")
    assert interaction.messages[0]["embed"].description == "Der Status von **Bob** ist jetzt: Hängt fest"


async def test_status_without_a_link_asks_nothing(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await call(bot, "status", interaction, "7", "away", "2")
    assert manager.requests == [] and not interaction.response.deferred


# --- /claim -----------------------------------------------------------------------------------------------------------
async def test_claim_posts_as_the_person_and_names_the_slot_and_game(bot, manager):
    link(bot)
    manager.add("POST", CLAIM, {**detail(), "configs_added": 1})
    interaction = asker(bot)
    await call(bot, "claim", interaction, "7", "2")
    [request] = manager.requests
    assert (request.method, request.url.path) == ("POST", CLAIM)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert interaction.messages[0]["embed"].description == "You claimed **Bob** in **Friday Run**"
    assert interaction.messages[0]["ephemeral"] is True


async def test_claim_forgets_the_cached_list_of_claimable_slots(bot, manager):
    link(bot)
    manager.add("GET", GAME, detail(claimable=True, slots=[slot_json(2, "Bob", claimable=True)]))
    manager.add("POST", CLAIM, detail())
    assert [s.slot for s in await bot.slots.claimable(1001, USER_TOKEN, 7)] == [2]
    await bot.slots.claimable(1001, USER_TOKEN, 7)
    assert manager.paths() == [GAME]  # the second look came from the cache
    await call(bot, "claim", asker(bot), "7", "2")
    await bot.slots.claimable(1001, USER_TOKEN, 7)
    assert manager.paths() == [GAME, CLAIM, GAME]


async def test_claim_shows_the_managers_words_when_somebody_was_faster(bot, manager):
    link(bot)
    manager.add("POST", CLAIM, (409, {"detail": "Somebody else already claimed that slot"}))
    with pytest.raises(Conflict) as error:
        await call(bot, "claim", asker(bot), "7", "2")
    assert error.value.detail == "Somebody else already claimed that slot"


@pytest.mark.parametrize("slot", ["x", "-2", "", "1234567890"])
async def test_claim_needs_a_real_slot_number(bot, manager, slot):
    link(bot)
    with pytest.raises(UserFacingError):
        await call(bot, "claim", asker(bot), "7", slot)
    assert manager.requests == []


# --- /handover offer --------------------------------------------------------------------------------------------------
async def test_a_member_is_resolved_to_their_linked_manager_username(bot, manager):
    link(bot)
    link(bot, user_id=2002, manager_id=20, username="bob_manager", token="apm_bobsTokenForTests")
    manager.add("PUT", HANDOVER, detail())
    interaction = asker(bot)
    await call(bot, "offer", interaction, "7", "2", SimpleNamespace(id=2002), None, "back in a week")
    [request] = manager.requests
    assert (request.method, request.url.path) == ("PUT", HANDOVER)
    assert body(manager) == {"to": "bob_manager", "note": "back in a week"}
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"  # the offerer's token, not the member's
    assert interaction.messages[0]["embed"].description == "You offered **Bob** to **bob\\_manager**"


async def test_a_member_who_has_not_linked_is_an_error_and_nothing_is_asked(bot, manager):
    link(bot)
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await call(bot, "offer", interaction, "7", "2", SimpleNamespace(id=3003))
    assert error.value.message("en") == "<@3003> has not linked a manager account"
    assert manager.requests == [] and not interaction.response.deferred


async def test_a_typed_username_is_sent_as_it_is(bot, manager):
    link(bot)
    manager.add("PUT", HANDOVER, detail())
    await call(bot, "offer", asker(bot), "7", "2", None, " carol ")
    assert body(manager) == {"to": "carol", "note": ""}


async def test_without_member_or_username_the_offer_is_open_to_anyone(bot, manager):
    link(bot)
    manager.add("PUT", HANDOVER, detail())
    interaction = asker(bot)
    await call(bot, "offer", interaction, "7", "2")
    assert body(manager) == {"to": None, "note": ""}
    assert interaction.messages[0]["embed"].description == "You offered **Bob** to anyone who takes it"


@pytest.mark.parametrize("blank", ["   ", " 	 "])
async def test_a_blank_username_is_an_open_offer_not_an_offer_to_nobody_by_name(bot, manager, blank):
    link(bot)
    manager.add("PUT", HANDOVER, detail())
    interaction = asker(bot)
    await call(bot, "offer", interaction, "7", "2", None, blank)
    assert body(manager) == {"to": None, "note": ""}
    assert interaction.messages[0]["embed"].description == "You offered **Bob** to anyone who takes it"


async def test_a_blank_username_next_to_a_member_is_ignored(bot, manager):
    link(bot)
    link(bot, user_id=2002, manager_id=20, username="bob_manager", token="apm_bobsTokenForTests")
    manager.add("PUT", HANDOVER, detail())
    await call(bot, "offer", asker(bot), "7", "2", SimpleNamespace(id=2002), "  ")
    assert body(manager)["to"] == "bob_manager"


async def test_a_member_and_a_username_together_are_refused(bot, manager):
    link(bot)
    link(bot, user_id=2002, manager_id=20, username="bob_manager", token="apm_bobsTokenForTests")
    with pytest.raises(UserFacingError) as error:
        await call(bot, "offer", asker(bot), "7", "2", SimpleNamespace(id=2002), "carol")
    assert error.value.message("en") == "Choose either a member or a username, not both"
    assert manager.requests == []


async def test_an_unknown_username_is_explained_as_not_found(bot, manager):
    link(bot)
    manager.add("PUT", HANDOVER, (404, {"detail": "There is no user of that name"}))
    with pytest.raises(UserFacingError):  # a 404 is explained as game or slot not found
        await call(bot, "offer", asker(bot), "7", "2", None, "nobody")


# --- /handover cancel -------------------------------------------------------------------------------------------------
async def test_cancel_deletes_the_offer_as_the_person(bot, manager):
    link(bot)
    manager.add("DELETE", HANDOVER, detail())
    interaction = asker(bot)
    await call(bot, "cancel", interaction, "7", "2")
    [request] = manager.requests
    assert (request.method, request.url.path) == ("DELETE", HANDOVER)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert interaction.messages[0]["embed"].description == "The offer of **Bob** is taken back"


async def test_cancel_still_answers_when_the_manager_sends_no_game(bot, manager):
    link(bot)
    manager.add("DELETE", HANDOVER, {"declined": True})
    interaction = asker(bot)
    await call(bot, "cancel", interaction, "7", "2")
    assert interaction.messages[0]["embed"].description == "The offer of **#2** is taken back"


# --- finding slots ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("text", "number"), [("5", 5), (" 12 ", 12), ("007", 7), ("123456789", 123456789)])
def test_slot_numbers_are_read(text, number):
    assert number_or_none(text) == number == parse_slot_id(text)


@pytest.mark.parametrize("text", ["", "x", "-1", "1.5", "1234567890", "２", "1 2"])
def test_other_text_is_not_a_slot_number(text):
    assert number_or_none(text) is None
    with pytest.raises(UserFacingError):
        parse_slot_id(text)


async def test_the_directory_keeps_a_given_slot_without_asking(manager):
    assert await SlotDirectory(manager.client).resolve(USER_TOKEN, 7, 4) == 4
    assert manager.requests == []


async def test_the_claimable_list_is_kept_for_a_minute_per_person_and_game(manager):
    now = [0.0]
    directory = SlotDirectory(manager.client, now=lambda: now[0])
    manager.add("GET", GAME, detail(claimable=True, slots=[slot_json(2, "Bob", "Hollow Knight", claimable=True), slot_json(1)]))
    [slot] = await directory.claimable(1001, USER_TOKEN, 7)
    assert (slot.slot, slot.name, slot.game) == (2, "Bob", "Hollow Knight")
    now[0] = 59
    await directory.claimable(1001, USER_TOKEN, 7)
    assert len(manager.requests) == 1
    await directory.claimable(2002, "apm_other", 7)  # another person sees their own view
    assert len(manager.requests) == 2 and manager.requests[-1].headers["Authorization"] == "Bearer apm_other"
    now[0] = 61
    await directory.claimable(1001, USER_TOKEN, 7)
    assert len(manager.requests) == 3


# --- the cache --------------------------------------------------------------------------------------------------------
def test_entries_expire_and_the_oldest_goes_when_it_is_full():
    now = [0.0]
    cache = TtlCache[str](10, 2, now=lambda: now[0])
    cache.set("a", "1")
    now[0] = 1
    cache.set("b", "2")
    now[0] = 2
    cache.set("c", "3")
    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (None, "2", "3")
    now[0] = 11.5
    assert (cache.get("b"), cache.get("c")) == (None, "3")
    cache.discard("c")
    cache.discard("missing")
    assert cache.get("c") is None
