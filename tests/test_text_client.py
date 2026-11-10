import json

import pytest
from fakes import USER_TOKEN, FakeManager

from apbot.errors import UserFacingError
from apbot.services.text_client import MAX_LINES, TextClientBridge
from apbot.ui.format import ZERO_WIDTH_SPACE as ZWSP

CLIENT = "/api/games/7/slots/2/client"
SAY = f"{CLIENT}/say"
NAMES = f"{CLIENT}/names"


class Clock:
    """Time that only moves when the bridge sleeps, so waits take no real time."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def page(state="connected", last=0, messages=(), error=None) -> dict:
    return {"state": state, "error": error, "last": last, "messages": list(messages), "hint_points": 3, "hint_cost": 1}


def message(seq, text, *, mine=True, kind="CommandResult", parts=None) -> dict:
    return {"seq": seq, "time": 1.0, "type": kind, "parts": parts or [{"text": text}], "mine": mine}


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def bridge(manager, clock):
    return TextClientBridge(manager.client, sleep=clock.sleep, now=clock.time)


def polled_after(manager) -> list[int]:
    return [json.loads(r.content)["after"] for r in manager.requests if r.url.path == CLIENT]


# --- connecting -------------------------------------------------------------------------------------------------------
async def test_it_waits_until_the_connection_is_up_polling_every_second(bridge, manager, clock):
    manager.add("POST", CLIENT, page("connecting"), page("connecting"), page("connected", last=4))
    manager.add("POST", SAY, {"ok": True})
    await bridge.say(USER_TOKEN, 7, 2, "hello")
    assert clock.sleeps == [1.0, 1.0]
    assert manager.paths() == [CLIENT, CLIENT, CLIENT, SAY]
    assert json.loads(manager.requests[-1].content) == {"text": "hello"}


async def test_every_request_is_made_with_the_callers_token(bridge, manager):
    manager.add("POST", CLIENT, page())
    manager.add("POST", SAY, {"ok": True})
    await bridge.say(USER_TOKEN, 7, 2, "hello")
    assert {r.headers["Authorization"] for r in manager.requests} == {f"Bearer {USER_TOKEN}"}


async def test_a_server_that_is_not_running_is_a_message_for_the_person(bridge, manager):
    manager.add("POST", CLIENT, page("offline"))
    with pytest.raises(UserFacingError) as error:
        await bridge.run(USER_TOKEN, 7, 2, "!hint Sword")
    assert error.value.message("en") == "The game's server is not running"
    assert SAY not in manager.paths()


async def test_a_refused_connection_shows_the_servers_reason_escaped(bridge, manager):
    manager.add("POST", CLIENT, page("refused", error="Invalid password *bold*"))
    with pytest.raises(UserFacingError) as error:
        await bridge.run(USER_TOKEN, 7, 2, "!hint Sword")
    assert error.value.message("en") == "The game's server refused the connection: Invalid password \\*bold\\*"


async def test_a_connection_that_never_comes_up_gives_up_after_twenty_seconds(bridge, manager, clock):
    manager.add("POST", CLIENT, page("connecting"))
    with pytest.raises(UserFacingError) as error:
        await bridge.run(USER_TOKEN, 7, 2, "!hint Sword")
    assert "did not answer in time" in error.value.message("en")
    assert 20 <= clock.now <= 21
    assert SAY not in manager.paths()


# --- what is said ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["/hint Sword", "  /x", "", "   ", "a" * 1001, "two\nlines", "tab\there"])
async def test_text_the_server_would_refuse_is_rejected_before_the_manager_is_asked(bridge, manager, text):
    with pytest.raises(UserFacingError):
        await bridge.run(USER_TOKEN, 7, 2, text)
    with pytest.raises(UserFacingError):
        await bridge.say(USER_TOKEN, 7, 2, text)
    assert manager.requests == []


async def test_a_thousand_characters_are_still_fine(bridge, manager):
    manager.add("POST", CLIENT, page())
    manager.add("POST", SAY, {"ok": True})
    await bridge.say(USER_TOKEN, 7, 2, "a" * 1000)
    assert SAY in manager.paths()


# --- the answer --------------------------------------------------------------------------------------------------------
async def test_the_answer_is_what_the_server_said_to_this_player_after_the_message(bridge, manager, clock):
    manager.add(
        "POST",
        CLIENT,
        page(last=10),  # connected; the newest message before ours is 10
        page(last=10),  # nothing yet
        page(last=11, messages=[message(11, "Remaining items: 3")]),
        page(last=12, messages=[message(12, "More lines")]),  # arrives within the quiet period
    )
    manager.add("POST", SAY, {"ok": True})
    answer = await bridge.run(USER_TOKEN, 7, 2, "!remaining")
    assert answer.lines == ("Remaining items: 3", "More lines") and answer.hidden == 0
    assert polled_after(manager) == [0, 10, 10, 11]  # each poll asks only for what is newer
    assert clock.sleeps == [1.0, 1.0, 1.5]  # one second between polls, then the quiet period after the first line


async def test_it_stops_a_second_and_a_half_after_the_first_line_not_when_the_answer_is_complete(bridge, manager, clock):
    manager.add("POST", CLIENT, page(last=1), page(last=2, messages=[message(2, "first")]), page(last=2))
    manager.add("POST", SAY, {"ok": True})
    answer = await bridge.run(USER_TOKEN, 7, 2, "!remaining")
    assert answer.lines == ("first",)
    assert clock.sleeps[-1] == 1.5 and manager.paths().count(CLIENT) == 3


async def test_it_gives_up_after_eight_seconds_without_an_answer(bridge, manager, clock):
    manager.add("POST", CLIENT, page(last=1))
    manager.add("POST", SAY, {"ok": True})
    answer = await bridge.run(USER_TOKEN, 7, 2, "!hint Sword")
    assert answer.lines == () and answer.hidden == 0
    assert 8 <= clock.now <= 9


async def test_what_does_not_concern_the_player_and_the_managers_own_notes_are_not_the_answer(bridge, manager):
    manager.add(
        "POST",
        CLIENT,
        page(last=1),
        page(
            last=4,
            messages=[
                message(2, "Bob: hello everyone", mine=False, kind="Chat"),
                message(3, "The connection to the server was lost.", kind="Client"),
                message(4, "Your hint costs 5 points"),
            ],
        ),
        page(last=4),
    )
    manager.add("POST", SAY, {"ok": True})
    answer = await bridge.run(USER_TOKEN, 7, 2, "!hint Sword")
    assert answer.lines == ("Your hint costs 5 points",)


async def test_the_parts_of_a_message_are_joined_and_every_line_is_escaped(bridge, manager):
    parts = [{"text": "Alice", "kind": "player"}, {"text": " found "}, {"text": "*Sword* @everyone <@123>", "kind": "item"}]
    manager.add("POST", CLIENT, page(last=1), page(last=2, messages=[message(2, "", kind="Hint", parts=parts)]), page(last=2))
    manager.add("POST", SAY, {"ok": True})
    [line] = (await bridge.run(USER_TOKEN, 7, 2, "!hint Sword")).lines
    assert line == rf"Alice found \*Sword\* @{ZWSP}everyone <{ZWSP}@{ZWSP}123>"


async def test_a_message_with_several_lines_becomes_several_lines_and_blank_ones_are_dropped(bridge, manager):
    manager.add("POST", CLIENT, page(last=1), page(last=2, messages=[message(2, "one\n\n  two  \nthree")]), page(last=2))
    manager.add("POST", SAY, {"ok": True})
    assert (await bridge.run(USER_TOKEN, 7, 2, "!help")).lines == ("one", "two", "three")


async def test_a_long_line_is_cut(bridge, manager):
    manager.add("POST", CLIENT, page(last=1), page(last=2, messages=[message(2, "x" * 1000)]), page(last=2))
    manager.add("POST", SAY, {"ok": True})
    [line] = (await bridge.run(USER_TOKEN, 7, 2, "!help")).lines
    assert len(line) == 240 and line.endswith("…")


async def test_at_most_fifteen_lines_are_returned_and_the_rest_is_counted(bridge, manager):
    many = [message(i + 2, f"line {i}") for i in range(20)]
    manager.add("POST", CLIENT, page(last=1), page(last=22, messages=many), page(last=22))
    manager.add("POST", SAY, {"ok": True})
    answer = await bridge.run(USER_TOKEN, 7, 2, "!help")
    assert len(answer.lines) == MAX_LINES == 15 and answer.hidden == 5
    assert answer.lines[0] == "line 0" and answer.lines[-1] == "line 14"


async def test_junk_in_the_managers_answer_is_ignored(bridge, manager):
    manager.add(
        "POST", CLIENT, page(last=1), {"state": "connected", "last": "x", "messages": ["no", {"mine": True}, None]}
    )
    manager.add("POST", SAY, {"ok": True})
    answer = await bridge.run(USER_TOKEN, 7, 2, "!help")
    assert answer.lines == ()


# --- names -------------------------------------------------------------------------------------------------------------
async def test_names_open_the_connection_then_ask_and_are_cached_for_ten_minutes(bridge, manager, clock):
    manager.add("POST", CLIENT, page("connecting"))
    manager.add("GET", NAMES, {"items": ["Sword", "Bow"], "locations": ["Chest"]})
    first = await bridge.names(1001, USER_TOKEN, 7, 2)
    assert (first.items, first.locations) == (("Sword", "Bow"), ("Chest",))
    assert manager.paths() == [CLIENT, NAMES]
    clock.now += 599
    assert await bridge.names(1001, USER_TOKEN, 7, 2) == first
    assert len(manager.requests) == 2  # from the cache
    clock.now += 2
    await bridge.names(1001, USER_TOKEN, 7, 2)
    assert len(manager.requests) == 4  # expired: asked again


async def test_names_are_none_until_the_manager_has_them_and_then_not_cached(bridge, manager, clock):
    manager.add("POST", CLIENT, page("connecting"))
    manager.add("GET", NAMES, {"items": [], "locations": []}, {"items": ["Sword"], "locations": []})
    assert await bridge.names(1001, USER_TOKEN, 7, 2) is None
    clock.now += 5
    assert (await bridge.names(1001, USER_TOKEN, 7, 2)).items == ("Sword",)


async def test_names_of_one_person_are_not_given_to_another(bridge, manager):
    manager.add("POST", CLIENT, page())
    manager.add("GET", NAMES, {"items": ["Sword"], "locations": []})
    await bridge.names(1001, USER_TOKEN, 7, 2)
    await bridge.names(2002, "apm_otherUserToken", 7, 2)
    assert manager.paths().count(NAMES) == 2
    assert manager.requests[-1].headers["Authorization"] == "Bearer apm_otherUserToken"


async def test_a_names_answer_with_junk_counts_as_not_loaded(bridge, manager):
    manager.add("POST", CLIENT, page())
    manager.add("GET", NAMES, {"items": "Sword", "locations": [1, None, ""]})
    assert await bridge.names(1001, USER_TOKEN, 7, 2) is None


def test_the_bridge_needs_no_manager_to_be_built():
    assert TextClientBridge(FakeManager().client) is not None


# --- what counts as the answer -----------------------------------------------------------------------------------------
async def test_other_messages_about_the_player_in_the_window_are_not_the_answer(bridge, manager):
    manager.add(
        "POST",
        CLIENT,
        page(last=1),
        page(
            last=5,
            messages=[
                message(2, "Alice sent Sword to Bob", kind="ItemSend"),
                message(3, "Cy joined the game", kind="Join"),
                message(4, "Alice: hi", kind="Chat"),
                message(5, "Remaining items: 3", kind="CommandResult"),
            ],
        ),
        page(last=5),
    )
    manager.add("POST", SAY, {"ok": True})
    assert (await bridge.run(USER_TOKEN, 7, 2, "!remaining")).lines == ("Remaining items: 3",)


async def test_the_hint_the_server_gives_is_part_of_the_answer(bridge, manager):
    manager.add(
        "POST",
        CLIENT,
        page(last=1),
        page(last=3, messages=[message(2, "Sword is at Chest", kind="Hint"), message(3, "Hint points: 2", kind="CommandResult")]),
        page(last=3),
    )
    manager.add("POST", SAY, {"ok": True})
    assert (await bridge.run(USER_TOKEN, 7, 2, "!hint Sword")).lines == ("Sword is at Chest", "Hint points: 2")


# --- suggestions stay fast ---------------------------------------------------------------------------------------------
async def test_names_that_are_still_loading_are_not_asked_for_again_on_every_keystroke(bridge, manager, clock):
    manager.add("POST", CLIENT, page("connecting"))
    manager.add("GET", NAMES, {"items": [], "locations": []})
    assert await bridge.names(1001, USER_TOKEN, 7, 2) is None
    for _ in range(5):
        assert await bridge.names(1001, USER_TOKEN, 7, 2) is None
    assert len(manager.requests) == 2
    assert await bridge.names(2002, "apm_other", 7, 2) is None  # another person is asked for themselves
    assert len(manager.requests) == 4
    clock.now += 5
    await bridge.names(1001, USER_TOKEN, 7, 2)
    assert len(manager.requests) == 6


async def test_suggestions_use_short_timeouts_and_no_retries(bridge, manager):
    manager.add("POST", CLIENT, page("connecting"))
    manager.add("GET", NAMES, (503, {"detail": "busy"}))
    from apbot.manager.errors import ManagerUnavailable

    with pytest.raises(ManagerUnavailable):
        await bridge.names(1001, USER_TOKEN, 7, 2)
    assert manager.paths() == [CLIENT, NAMES]  # one try each
    assert all(r.extensions["timeout"]["read"] == 2 for r in manager.requests)


async def test_a_manager_too_slow_for_discord_counts_as_still_loading(bridge, manager, monkeypatch):
    import asyncio

    from apbot.services import text_client

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(text_client, "NAMES_BUDGET", 0.05)
    monkeypatch.setattr(bridge._client, "client_poll", slow)
    assert await bridge.names(1001, USER_TOKEN, 7, 2) is None
    assert await bridge.names(1001, USER_TOKEN, 7, 2) is None  # and the next keystroke does not wait again
