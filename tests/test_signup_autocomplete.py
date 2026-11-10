import pytest
from fakes import USER_TOKEN, FakeInteraction, config_json, entry_json, signup_detail_json, signup_json

from apbot.autocomplete import (
    config_choices,
    my_entry_choices,
    signup_choices_managed,
    signup_choices_open,
    signup_choices_visible,
)
from apbot.manager.errors import ManagerUnavailable
from apbot.services.signup_directory import SUGGESTION_TTL, SignupDirectory

ROUNDS = [
    signup_json(1, "Spring Async"),
    signup_json(2, "Closed Run", status="closed", open=False, can_manage=True),
    signup_json(3, "Mine Open", can_manage=True),
    signup_json(4, "Done Run", status="done", open=False, can_manage=True),
    signup_json(5, "Foreign Closed", status="closed", open=False, can_manage=False),
]


def link(bot, user_id=1001, manager_id=10):
    bot.links.save(user_id, manager_id, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def asker(bot, **namespace) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, namespace=namespace)


def names(choices) -> list[str]:
    return [choice.name for choice in choices]


# --- rounds ----------------------------------------------------------------------------------------------------------------
async def test_the_rounds_that_take_entries_are_suggested_with_their_id_as_the_value(bot, manager):
    link(bot)
    manager.add("GET", "/api/signups", ROUNDS)
    choices = await signup_choices_open(asker(bot), "")
    assert [(c.name, c.value) for c in choices] == [("Spring Async", "1"), ("Mine Open", "3")]
    assert manager.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"


async def test_what_was_typed_narrows_the_suggestions_whatever_its_case(bot, manager):
    link(bot)
    manager.add("GET", "/api/signups", ROUNDS)
    assert names(await signup_choices_open(asker(bot), " MINE ")) == ["Mine Open"]
    assert await signup_choices_open(asker(bot), "nothing like it") == []


async def test_the_rounds_a_person_runs_that_are_not_generated_yet_are_suggested_for_closing_and_testing(bot, manager):
    link(bot)
    manager.add("GET", "/api/signups", ROUNDS)
    assert names(await signup_choices_managed(asker(bot), "")) == ["Closed Run", "Mine Open"]


async def test_the_visible_rounds_are_the_open_ones_and_the_closed_ones_the_person_runs(bot, manager):
    link(bot)
    manager.add("GET", "/api/signups", ROUNDS)
    assert names(await signup_choices_visible(asker(bot), "")) == ["Spring Async", "Closed Run", "Mine Open"]


async def test_the_list_is_kept_for_a_minute_per_person(bot, manager):
    link(bot)
    link(bot, user_id=2002, manager_id=20)
    manager.add("GET", "/api/signups", ROUNDS)
    await signup_choices_open(asker(bot), "")
    await signup_choices_open(asker(bot), "sp")
    assert len(manager.requests) == 1
    await signup_choices_open(FakeInteraction(bot, user_id=2002), "")
    assert len(manager.requests) == 2  # whether someone runs a round is theirs, not shared


async def test_suggestions_are_at_most_25_and_their_names_fit_discord(bot, manager):
    link(bot)
    manager.add("GET", "/api/signups", [signup_json(i, f"Round {i} " + "x" * 150) for i in range(1, 40)])
    choices = await signup_choices_open(asker(bot), "")
    assert len(choices) == 25 and all(len(c.name) <= 100 for c in choices)


async def test_nobody_linked_means_no_suggestions_and_no_manager_call(bot, manager):
    assert await signup_choices_open(asker(bot), "") == []
    assert manager.requests == []


@pytest.mark.parametrize("answer", [(503, {"detail": "busy"}), (401, {"detail": "no"}), {"not": "a list"}])
async def test_a_manager_that_cannot_answer_means_no_suggestions_never_an_error(bot, manager, answer):
    link(bot)
    manager.add("GET", "/api/signups", answer)
    assert await signup_choices_open(asker(bot), "") == []


# --- configs ---------------------------------------------------------------------------------------------------------------
async def test_the_persons_saved_configs_are_suggested_by_name_with_their_id(bot, manager):
    link(bot)
    manager.add("GET", "/api/configs", [config_json(40, "alice.yaml"), config_json(41, "Bob's Run.yaml")])
    assert [(c.name, c.value) for c in await config_choices(asker(bot), "")] == [
        ("alice.yaml", "40"), ("Bob's Run.yaml", "41"),
    ]
    assert names(await config_choices(asker(bot), "bob")) == ["Bob's Run.yaml"]
    assert manager.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert len(manager.requests) == 1


async def test_no_configs_to_suggest_when_the_manager_fails_or_nobody_linked(bot, manager):
    assert await config_choices(asker(bot), "") == []
    link(bot)
    manager.add("GET", "/api/configs", (503, {"detail": "busy"}))
    assert await config_choices(asker(bot), "") == []


# --- entries ---------------------------------------------------------------------------------------------------------------
async def test_the_persons_own_entries_of_the_chosen_round_are_suggested(bot, manager):
    link(bot)
    entries = [entry_json(1, "alice.yaml"), entry_json(2, mine=False, name=None, username="bob"), entry_json(3, "second.yaml")]
    manager.add("GET", "/api/signups/3", signup_detail_json(3, entries=entries))
    choices = await my_entry_choices(asker(bot, round="3"), "")
    assert [(c.name, c.value) for c in choices] == [("alice.yaml", "1"), ("second.yaml", "3")]
    assert names(await my_entry_choices(asker(bot, round="3"), "SEC")) == ["second.yaml"]
    assert len(manager.requests) == 1


@pytest.mark.parametrize("round_value", [None, "x", "", "1234567890"])
async def test_without_a_usable_round_there_are_no_entries_to_suggest(bot, manager, round_value):
    link(bot)
    interaction = asker(bot) if round_value is None else asker(bot, round=round_value)
    assert await my_entry_choices(interaction, "") == []
    assert manager.requests == []


async def test_entries_of_a_round_the_manager_does_not_show_are_not_an_error(bot, manager):
    link(bot)
    manager.add("GET", "/api/signups/3", (404, {"detail": "No such round"}))
    assert await my_entry_choices(asker(bot, round="3"), "") == []


# --- the directory ----------------------------------------------------------------------------------------------------------
async def test_the_directory_reads_with_the_short_timeout_and_without_retries(bot, manager):
    link(bot)
    manager.add("GET", "/api/signups", (503, {"detail": "busy"}), [])
    directory = SignupDirectory(manager.client)
    with pytest.raises(ManagerUnavailable):
        await directory.rounds(1001, USER_TOKEN)
    assert len(manager.requests) == 1 and manager.requests[0].extensions["timeout"]["read"] == 2


async def test_the_directory_asks_again_after_the_ttl_and_when_told_to_forget(manager):
    clock = [0.0]
    directory = SignupDirectory(manager.client, now=lambda: clock[0])
    manager.add("GET", "/api/signups/3", signup_detail_json(3, entries=[entry_json(1)]))
    manager.add("GET", "/api/signups", ROUNDS)
    await directory.my_entries(1, USER_TOKEN, 3)
    await directory.rounds(1, USER_TOKEN)
    await directory.my_entries(1, USER_TOKEN, 3)
    await directory.rounds(1, USER_TOKEN)
    assert len(manager.requests) == 2
    directory.forget_entries(1, 3)
    await directory.my_entries(1, USER_TOKEN, 3)
    directory.forget_rounds(1)
    await directory.rounds(1, USER_TOKEN)
    assert len(manager.requests) == 4
    clock[0] += SUGGESTION_TTL
    await directory.rounds(1, USER_TOKEN)
    assert len(manager.requests) == 5
