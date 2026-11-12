import pytest
from fakes import BOT_TOKEN, SITE_URL, USER_TOKEN, FakeInteraction, by_token, config_json, game_json, slot_json

from apbot.autocomplete import (
    LOADING_VALUE,
    archive_game_choices,
    claimable_slot_choices,
    delete_game_choices,
    generate_config_choices,
    game_choices_mine,
    game_choices_visible,
    is_loading_choice,
    item_choices,
    location_choices,
    slot_choices_mine,
)
from apbot.manager.errors import ManagerUnavailable
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


# --- the games the person may archive, restore or delete ----------------------------------------------------------------------
def rights(game_id, name, **extra):
    return game_json(game_id, name, **{"can_manage": False, "can_archive": False, **extra})


def archive_games(manager):
    manager.add(
        "GET",
        "/api/games",
        [
            rights(1, "Running A", can_archive=True, can_manage=True),
            rights(2, "Running B", can_archive=True),
            rights(3, "Not Mine", can_manage=True),
            rights(4, "Old Archived", can_archive=True, can_manage=True, finished_at=5.0, outcome="finished"),
            rights(5, "Blocked", can_archive=True, finished_at=6.0, outcome="finished", return_blocked="finished"),
            rights(6, "Version Gone", can_archive=True, finished_at=7.0, return_blocked="version"),
            rights(7, "Aborted", can_archive=True, finished_at=8.0, outcome="aborted"),
        ],
    )


async def test_to_archive_only_running_games_the_person_may_archive_are_offered(bot, manager):
    link(bot)
    archive_games(manager)
    choices = await archive_game_choices(typing(bot, {"action": "archive"}), "")
    assert sorted(values(choices)) == [("Running A", "1"), ("Running B", "2")]
    assert values(await archive_game_choices(typing(bot), "b")) == [("Running B", "2")]  # no action typed yet


async def test_to_restore_only_archived_games_that_may_leave_are_offered(bot, manager):
    link(bot)
    archive_games(manager)
    choices = await archive_game_choices(typing(bot, {"action": "restore"}), "")
    assert sorted(values(choices)) == [("Aborted", "7"), ("Old Archived", "4")]


async def test_the_games_are_asked_for_as_the_person_and_quickly_and_only_once_a_minute(bot, manager):
    link(bot)
    archive_games(manager)
    await archive_game_choices(typing(bot), "")
    await delete_game_choices(typing(bot), "")
    [request] = manager.requests
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}" and request.extensions["timeout"]["read"] == 2


async def test_to_delete_every_game_the_person_manages_is_offered_whatever_its_state(bot, manager):
    link(bot)
    archive_games(manager)
    choices = await delete_game_choices(typing(bot), "")
    assert sorted(values(choices)) == [("Not Mine", "3"), ("Old Archived", "4"), ("Running A", "1")]
    assert values(await delete_game_choices(typing(bot), "old")) == [("Old Archived", "4")]


@pytest.mark.parametrize("suggest", [archive_game_choices, delete_game_choices])
async def test_a_manager_that_does_not_answer_gives_no_suggestions_and_no_error(bot, manager, suggest):
    link(bot)
    manager.add("GET", "/api/games", (503, {"detail": "busy"}))
    assert await suggest(typing(bot), "") == []


@pytest.mark.parametrize("suggest", [archive_game_choices, delete_game_choices])
async def test_without_a_link_there_are_no_suggestions_and_nothing_is_asked(bot, manager, suggest):
    assert await suggest(typing(bot), "") == []
    assert manager.requests == []


async def test_when_the_manager_does_not_answer_the_last_list_kept_is_used(bot, manager):
    link(bot)

    async def fail(*args, **kwargs):
        raise ManagerUnavailable(None, "down")

    bot.my_games.games = fail
    bot.my_games.cached = lambda owner: tuple(parse_games([rights(1, "Kept", can_archive=True, can_manage=True)]))
    assert values(await archive_game_choices(typing(bot), "")) == [("Kept", "1")]
    assert values(await delete_game_choices(typing(bot), "")) == [("Kept", "1")]



# --- configs to generate with: own, and the ones friends share ------------------------------------------------------------
LIBRARY = [
    config_json(40, "alice.yaml", user_id=10, owner="alice"),
    config_json(41, "Second.yaml", user_id=10, owner="alice"),
    config_json(77, "bob-hk.yaml", user_id=20, owner="bob", shared=True, share_with="friends"),
]


def generating(bot, focused="config", locale="en-US", **namespace) -> FakeInteraction:
    """The person is typing in the option `focused` of /generate; `namespace` holds the options typed so far."""
    interaction = FakeInteraction(bot, locale=locale, user_id=1001, namespace=namespace)
    interaction.data = {"options": [{"name": name, "value": value, "focused": name == focused} for name, value in namespace.items()]}
    return interaction


async def test_own_configs_are_plain_and_a_friends_names_its_owner_in_the_persons_language(bot, manager):
    link(bot)
    manager.add("GET", "/api/configs", LIBRARY)
    assert values(await generate_config_choices(generating(bot), "")) == [
        ("alice.yaml", "40"), ("Second.yaml", "41"), ("bob-hk.yaml — from bob", "77"),
    ]
    assert values(await generate_config_choices(generating(bot, locale="de"), "hk")) == [("bob-hk.yaml — von bob", "77")]
    assert values(await generate_config_choices(generating(bot, locale="fr"), "hk")) == [("bob-hk.yaml — de bob", "77")]
    assert values(await generate_config_choices(generating(bot, locale="nl"), "hk")) == [("bob-hk.yaml — van bob", "77")]
    [request] = manager.requests  # one look for all of it, with friends one, as the person
    assert (request.url.query, request.headers["Authorization"]) == (b"friends=1", f"Bearer {USER_TOKEN}")


async def test_the_search_matches_the_configs_name_not_its_owner_and_is_not_case_sensitive(bot, manager):
    link(bot)
    manager.add("GET", "/api/configs", [config_json(40, "Second.yaml", user_id=10), config_json(77, "hk.yaml", user_id=20, owner="bob")])
    assert [v for _, v in values(await generate_config_choices(generating(bot), "SECOND"))] == ["40"]
    assert [v for _, v in values(await generate_config_choices(generating(bot), "HK"))] == ["77"]
    assert await generate_config_choices(generating(bot), "bob") == []


async def test_names_are_clipped_to_discords_limit_and_a_friends_keeps_its_suffix(bot, manager):
    link(bot)
    long_name = "x" * 150
    manager.add(
        "GET", "/api/configs",
        [
            config_json(40, long_name, user_id=10, owner="alice"),
            config_json(77, long_name, user_id=20, owner="b" * 80),
        ],
    )
    own, friends = await generate_config_choices(generating(bot), "")
    assert len(own.name) == 100 and own.name.endswith("…")
    assert len(friends.name) <= 100 and friends.name.endswith(" — from " + "b" * 31 + "…") and friends.value == "77"


async def test_configs_already_picked_in_other_options_are_left_out_but_the_text_being_typed_is_not_a_pick(bot, manager):
    link(bot)
    manager.add("GET", "/api/configs", LIBRARY)
    picked = await generate_config_choices(generating(bot, "config2", config="40", config2=""), "")
    assert [v for _, v in values(picked)] == ["41", "77"]
    manager.add("GET", "/api/configs", [config_json(41, "Level 41.yaml", user_id=10), config_json(42, "Level 42.yaml", user_id=10)])
    bot.friend_directory.forget_library(1001)
    typed = await generate_config_choices(generating(bot, "config2", config="40", config2="41"), "41")
    assert [v for _, v in values(typed)] == ["41"]  # "41" is only what is typed in this option, not a pick
    bot.friend_directory.forget_library(1001)
    manager.add("GET", "/api/configs", LIBRARY)
    both = await generate_config_choices(generating(bot, "config3", config="40", config2="77", config3=""), "")
    assert [v for _, v in values(both)] == ["41"]
    ignored = await generate_config_choices(generating(bot, "config3", config="not a number", config3=""), "")
    assert len(ignored) == 3


async def test_nothing_is_left_out_when_the_option_being_typed_in_is_not_known(bot, manager):
    link(bot)
    manager.add("GET", "/api/configs", LIBRARY)
    unknown = FakeInteraction(bot, user_id=1001, namespace={"config": "40"})
    assert len(await generate_config_choices(unknown, "")) == 3


async def test_at_most_twenty_five_configs_are_suggested(bot, manager):
    link(bot)
    manager.add("GET", "/api/configs", [config_json(n, f"c{n}.yaml", user_id=10) for n in range(40, 80)])
    assert len(await generate_config_choices(generating(bot), "")) == 25


async def test_without_a_fresh_list_the_last_one_kept_is_used_else_nothing_and_never_an_error(bot, manager):
    assert await generate_config_choices(generating(bot), "") == []  # not linked
    link(bot)
    manager.add("GET", "/api/configs", (503, {"detail": "busy"}))
    assert await generate_config_choices(generating(bot), "") == []
    manager.add("GET", "/api/configs", LIBRARY)
    await generate_config_choices(generating(bot), "")
    bot.links.mark_invalid(1001)  # the token cannot be used now, the kept list still can
    assert [v for _, v in values(await generate_config_choices(generating(bot), ""))] == ["40", "41", "77"]
    assert len(manager.requests) == 2
