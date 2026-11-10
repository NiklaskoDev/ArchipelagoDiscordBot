import pytest
from fakes import BOT_TOKEN, SITE_URL, USER_TOKEN, FakeInteraction, by_token, game_json, slot_json

from apbot.autocomplete import (
    LOADING_VALUE,
    claimable_slot_choices,
    game_choices_mine,
    game_choices_visible,
    is_loading_choice,
    item_choices,
    location_choices,
    slot_choices_mine,
)
from apbot.manager.views import parse_games, parse_user_games

CLIENT = "/api/games/7/slots/2/client"
NAMES = f"{CLIENT}/names"


def link(bot, user_id=1001):
    bot.links.save(user_id, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def know(bot, *raw, user_id=1001):
    bot.user_sync._states[user_id] = {g.id: g for g in parse_user_games(list(raw))}


def mine(game_id, name, *slots, **extra):
    return game_json(game_id, name, my_slots=[{"slot": n, "name": s, "game": "x"} for n, s in slots], **extra)


def typing(bot, namespace=None, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, namespace=namespace, **kwargs)


def values(choices) -> list[tuple[str, str]]:
    return [(c.name, c.value) for c in choices]


# --- the person's games and slots --------------------------------------------------------------------------------------
async def test_games_are_the_ones_the_person_plays_in_ongoing_first(bot):
    know(bot, mine(1, "Old Run", (1, "A"), finished_at=5.0), mine(2, "Friday Run", (1, "A")), game_json(3, "Not Mine"))
    assert values(await game_choices_mine(typing(bot), "")) == [("Friday Run", "2"), ("Old Run", "1")]
    assert values(await game_choices_mine(typing(bot), "fri")) == [("Friday Run", "2")]


async def test_before_the_first_look_at_the_person_every_game_is_offered(bot):
    bot.cache._games = parse_games([game_json(4, "Public Run")])
    assert values(await game_choices_mine(typing(bot), "")) == [("Public Run", "4")]


async def test_slots_are_the_persons_in_the_chosen_game(bot):
    know(bot, mine(7, "G", (1, "Alice"), (2, "Bob")), mine(8, "H", (3, "Other")))
    assert values(await slot_choices_mine(typing(bot, {"game": "7"}), "")) == [("Alice", "1"), ("Bob", "2")]
    assert values(await slot_choices_mine(typing(bot, {"game": "7"}), "bo")) == [("Bob", "2")]


@pytest.mark.parametrize("namespace", [{}, {"game": "abc"}, {"game": None}, {"game": "9"}])
async def test_without_a_known_game_there_are_no_slots_to_suggest(bot, namespace):
    know(bot, mine(7, "G", (1, "Alice")))
    assert await slot_choices_mine(typing(bot, namespace), "") == []


async def test_other_peoples_slots_are_never_suggested(bot):
    know(bot, mine(7, "G", (1, "Alice")), user_id=2002)
    assert await slot_choices_mine(typing(bot, {"game": "7"}), "") == []


# --- claimable slots ---------------------------------------------------------------------------------------------------
async def test_claimable_slots_come_from_the_persons_own_view_with_their_world(bot, manager):
    link(bot)
    slots = [slot_json(1, "Taken"), slot_json(2, "Free", "Hollow Knight", claimable=True)]
    manager.add("GET", "/api/games/7", game_json(7, slots=slots))
    assert values(await claimable_slot_choices(typing(bot, {"game": "7"}), "")) == [("Free (Hollow Knight)", "2")]
    assert manager.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"


@pytest.mark.parametrize("namespace", [{}, {"game": "x"}])
async def test_claimable_slots_need_a_chosen_game(bot, manager, namespace):
    link(bot)
    assert await claimable_slot_choices(typing(bot, namespace), "") == []
    assert manager.requests == []


async def test_a_person_who_is_not_linked_or_a_manager_that_fails_gets_no_suggestions_and_no_error(bot, manager):
    assert await claimable_slot_choices(typing(bot, {"game": "7"}), "") == []
    link(bot)
    manager.add("GET", "/api/games/7", (503, {"detail": "down"}))
    assert await claimable_slot_choices(typing(bot, {"game": "7"}), "") == []


# --- names -------------------------------------------------------------------------------------------------------------
def names_routes(manager, items=("Progressive Sword", "Bow", "Sword Beam"), locations=("Cave Chest",)):
    manager.add("POST", CLIENT, {"state": "connecting", "last": 0, "messages": []})
    manager.add("GET", NAMES, {"items": list(items), "locations": list(locations)})


async def test_items_are_suggested_for_the_chosen_game_and_slot_and_filtered_by_what_was_typed(bot, manager):
    link(bot)
    names_routes(manager)
    namespace = {"game": "7", "slot": "2"}
    assert values(await item_choices(typing(bot, namespace), "sword")) == [
        ("Progressive Sword", "Progressive Sword"),
        ("Sword Beam", "Sword Beam"),
    ]
    assert values(await location_choices(typing(bot, namespace), "")) == [("Cave Chest", "Cave Chest")]


async def test_the_only_slot_is_used_when_none_is_chosen(bot, manager):
    link(bot)
    know(bot, mine(7, "G", (2, "Bob")))
    names_routes(manager)
    assert len(await item_choices(typing(bot, {"game": "7"}), "")) == 3


async def test_with_several_slots_and_none_chosen_nothing_is_suggested(bot, manager):
    link(bot)
    know(bot, mine(7, "G", (1, "A"), (2, "Bob")))
    assert await item_choices(typing(bot, {"game": "7"}), "") == []
    assert manager.requests == []


async def test_while_the_names_load_the_only_choice_says_so_in_the_persons_language(bot, manager):
    link(bot)
    names_routes(manager, items=(), locations=())
    namespace = {"game": "7", "slot": "2"}
    [choice] = await item_choices(typing(bot, namespace), "sw")
    assert (choice.name, choice.value) == ("Loading names… try again in a moment", LOADING_VALUE)
    assert is_loading_choice(choice.value) and not is_loading_choice("Sword")
    [german] = await item_choices(typing(bot, namespace, locale="de"), "sw")
    assert german.name == "Namen werden geladen… gleich nochmal versuchen"


async def test_at_most_25_names_and_none_longer_than_discord_allows(bot, manager):
    link(bot)
    names_routes(manager, items=[f"Item {n}" for n in range(60)] + ["x" * 101])
    choices = await item_choices(typing(bot, {"game": "7", "slot": "2"}), "")
    assert len(choices) == 25 and all(len(c.value) <= 100 for c in choices)
    assert await item_choices(typing(bot, {"game": "7", "slot": "2"}), "xxxx") == []


async def test_names_for_a_person_who_is_not_linked_or_when_the_manager_fails_are_just_missing(bot, manager):
    namespace = {"game": "7", "slot": "2"}
    assert await item_choices(typing(bot, namespace), "") == []
    link(bot)
    manager.add("POST", CLIENT, (404, {"detail": "No such slot"}))
    assert await item_choices(typing(bot, namespace), "") == []


# --- game_choices_visible ------------------------------------------------------------------------------------------------
async def two_views(bot, manager, *, user, public=None):
    """The bot's account sees `public`; the linked person's token sees `user`."""
    public = [game_json(1, "Friday Run")] if public is None else public
    manager.add("GET", "/api/games", by_token({BOT_TOKEN: public, USER_TOKEN: user}))
    manager.add("GET", "/api/auth/status", {"site_url": SITE_URL})
    await bot.cache.refresh()
    manager.requests.clear()


def hidden(game_id=9, name="Hidden Gem", **extra):
    return game_json(game_id, name, visibility="participants", **extra)


async def test_an_unlinked_person_gets_the_public_games_as_before(bot, manager):
    await two_views(bot, manager, user=[hidden()])
    choices = await game_choices_visible(FakeInteraction(bot), "")
    assert [(c.name, c.value) for c in choices] == [("Friday Run", "1")]
    assert manager.requests == []


async def test_a_linked_person_also_gets_their_private_games_marked_private(bot, manager):
    link(bot)
    await two_views(bot, manager, user=[game_json(1, "Friday Run"), hidden()])
    choices = await game_choices_visible(FakeInteraction(bot), "")
    assert [(c.name, c.value) for c in choices] == [("Hidden Gem — Private", "9"), ("Friday Run", "1")]
    assert manager.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert manager.requests[0].extensions["timeout"]["read"] == 2  # quick


async def test_the_public_game_is_listed_once_even_though_both_accounts_see_it(bot, manager):
    link(bot)
    await two_views(bot, manager, user=[game_json(1, "Friday Run"), hidden()])
    values = [c.value for c in await game_choices_visible(FakeInteraction(bot), "")]
    assert sorted(values) == ["1", "9"]


async def test_the_typed_text_narrows_private_games_too(bot, manager):
    link(bot)
    await two_views(bot, manager, user=[game_json(1, "Friday Run"), hidden()])
    assert [c.value for c in await game_choices_visible(FakeInteraction(bot), "GEM")] == ["9"]
    assert await game_choices_visible(FakeInteraction(bot), "nothing like it") == []


async def test_the_suffix_follows_the_language_and_the_name_is_clipped_to_100_characters(bot, manager):
    link(bot)
    await two_views(bot, manager, user=[hidden(9, "n" * 150)], public=[])
    [choice] = await game_choices_visible(FakeInteraction(bot, locale="de"), "")
    assert choice.name.endswith(" — Privat") and len(choice.name) == 100


async def test_the_french_and_dutch_suffix(bot, manager):
    link(bot)
    await two_views(bot, manager, user=[hidden()], public=[])
    assert (await game_choices_visible(FakeInteraction(bot, locale="fr"), ""))[0].name == "Hidden Gem — Privée"
    bot.my_games.forget(1001)
    assert (await game_choices_visible(FakeInteraction(bot, locale="nl"), ""))[0].name == "Hidden Gem — Privé"


async def test_a_finished_private_game_comes_after_the_listed_ones(bot, manager):
    link(bot)
    await two_views(bot, manager, user=[hidden(9, "Done One", finished_at=5.0, outcome="finished"), hidden(8, "Live One")], public=[])
    assert [c.value for c in await game_choices_visible(FakeInteraction(bot), "")] == ["8", "9"]


@pytest.mark.parametrize("answer", [(503, {"detail": "busy"}), (401, {"detail": "no"}), {"not": "a list"}])
async def test_a_manager_that_cannot_answer_means_public_choices_only_never_an_error(bot, manager, answer):
    link(bot)
    await two_views(bot, manager, user=answer)
    choices = await game_choices_visible(FakeInteraction(bot), "")
    assert [(c.name, c.value) for c in choices] == [("Friday Run", "1")]


async def test_when_the_manager_fails_later_the_kept_list_is_used(bot, manager):
    link(bot)
    await two_views(bot, manager, user=[game_json(1, "Friday Run"), hidden()])
    await bot.my_games.games(1001, USER_TOKEN)
    manager.add("GET", "/api/games", (503, {"detail": "busy"}))
    assert "9" in [c.value for c in await game_choices_visible(FakeInteraction(bot), "")]


async def test_a_broken_link_means_public_choices_only(bot, manager):
    link(bot)
    bot.links.mark_invalid(1001)
    await two_views(bot, manager, user=[hidden()])
    assert [c.value for c in await game_choices_visible(FakeInteraction(bot), "")] == ["1"]
    assert manager.requests == []


async def test_the_personal_list_for_suggestions_is_asked_quick(bot, manager):
    link(bot)
    await two_views(bot, manager, user=[hidden()])
    asked = []
    real = bot.my_games.games

    async def spy(owner, token, *, quick=False):
        asked.append(quick)
        return await real(owner, token, quick=quick)

    bot.my_games.games = spy
    await game_choices_visible(FakeInteraction(bot), "")
    assert asked == [True]
