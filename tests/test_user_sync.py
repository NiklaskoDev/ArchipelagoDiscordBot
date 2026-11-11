import pytest
from fakes import USER_TOKEN, friend_json, friends_json, game_json

from apbot.services.user_sync import (
    FRIENDS_RETRY,
    GAME_ONLINE,
    SLOT_OFFERED,
    FriendRequestEvent,
    UserSync,
    events_between,
)
from apbot.manager.views import parse_user_games


def link(bot, discord_id=1001, manager_id=10, token=USER_TOKEN):
    bot.links.save(discord_id, manager_id, "alice", bot.cipher.encrypt(token), token[-4:], "en")
    return bot.links.get(discord_id)


MINE = [{"slot": 1, "name": "Alice", "game": "Timespinner", "idle_since": None}]


def offer(slot=2, to_me=True, name="Bob"):
    return {"slot": slot, "name": name, "game": "Hollow Knight", "from": "bob", "to": "alice" if to_me else None,
            "to_me": to_me, "note": "no time", "can_accept": True}


class Heard:
    """What UserSync told its listeners."""

    def __init__(self):
        self.events, self.memberships, self.stopped = [], [], []

    async def on_events(self, events):
        self.events.append(events)

    async def on_memberships(self, discord_id, game_ids):
        self.memberships.append((discord_id, game_ids))

    async def on_token_stopped(self, link):
        self.stopped.append(link.discord_user_id)


@pytest.fixture
def sync(bot, manager):
    async def no_wait(seconds):
        pass

    heard = Heard()
    sync = UserSync(bot.links, bot.access, manager.client, sleep=no_wait)
    sync.listen(on_events=heard.on_events, on_memberships=heard.on_memberships, on_token_stopped=heard.on_token_stopped)
    sync.heard = heard
    return sync


def games(manager, *raw):
    manager.add("GET", "/api/games", list(raw))


# --- the pure part -----------------------------------------------------------------------------------------------------
def by_id(*raw):
    return {game.id: game for game in parse_user_games(list(raw))}


def test_a_game_that_goes_from_stopped_to_running_is_an_event_when_the_person_plays_in_it():
    before = by_id(game_json(7, state="stopped", my_slots=MINE))
    after = by_id(game_json(7, state="running", my_slots=MINE))
    [event] = events_between(1001, before, after)
    assert (event.kind, event.game_id, event.game_name, event.discord_user_id) == (GAME_ONLINE, 7, "Friday Run", 1001)


def test_a_game_that_was_running_already_or_that_the_person_does_not_play_in_is_no_event():
    running = by_id(game_json(7, state="running", my_slots=MINE))
    assert events_between(1001, running, running) == []
    other = by_id(game_json(7, state="stopped", my_slots=[]))
    assert events_between(1001, other, by_id(game_json(7, state="running", my_slots=[]))) == []


def test_a_game_that_appears_already_running_is_no_event():
    assert events_between(1001, {}, by_id(game_json(7, state="running", my_slots=MINE))) == []


def test_a_new_offer_to_the_person_is_an_event_with_what_is_offered():
    before = by_id(game_json(7, my_slots=MINE))
    after = by_id(game_json(7, my_slots=MINE, offered_slots=[offer(2)]))
    [event] = events_between(1001, before, after)
    assert (event.kind, event.game_id, event.slot, event.slot_name, event.world) == (SLOT_OFFERED, 7, 2, "Bob", "Hollow Knight")
    assert (event.offered_by, event.note) == ("bob", "no time")


def test_an_offer_that_was_there_before_or_is_open_to_anyone_is_no_event():
    with_offer = by_id(game_json(7, offered_slots=[offer(2)]))
    assert events_between(1001, with_offer, with_offer) == []
    open_offer = by_id(game_json(7, offered_slots=[offer(2, to_me=False)]))
    assert events_between(1001, {}, open_offer) == []


# --- looking at people -------------------------------------------------------------------------------------------------
async def test_the_first_look_at_a_person_only_remembers(bot, manager, sync):
    games(manager, game_json(7, state="running", my_slots=MINE, offered_slots=[offer()]))
    await sync.sync_user(link(bot))
    assert sync.heard.events == []
    assert sync.heard.memberships == [(1001, frozenset({7}))]
    assert manager.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"


async def test_a_game_coming_online_is_reported_on_the_next_look(bot, manager, sync):
    the_link = link(bot)
    games(manager, game_json(7, state="stopped", my_slots=MINE))
    await sync.sync_user(the_link)
    games(manager, game_json(7, state="running", my_slots=MINE))
    await sync.sync_user(the_link)
    [[event]] = sync.heard.events
    assert event.kind == GAME_ONLINE and event.game_id == 7


async def test_an_offer_to_the_person_is_reported_once(bot, manager, sync):
    the_link = link(bot)
    games(manager, game_json(7, my_slots=MINE))
    await sync.sync_user(the_link)
    games(manager, game_json(7, my_slots=MINE, offered_slots=[offer()]))
    await sync.sync_user(the_link)
    await sync.sync_user(the_link)
    assert [[e.kind for e in events] for events in sync.heard.events] == [[SLOT_OFFERED]]


async def test_roles_follow_the_games_a_person_plays_in_and_finished_games_do_not_count(bot, manager, sync):
    games(
        manager,
        game_json(7, my_slots=MINE),
        game_json(8, my_slots=MINE, finished_at=1_700_000_500.0),
        game_json(9, my_slots=[], offered_slots=[offer()]),
    )
    await sync.sync_user(link(bot))
    assert sync.heard.memberships == [(1001, frozenset({7}))]
    assert sync.memberships() == {1001: frozenset({7})}


async def test_a_refused_token_marks_the_link_invalid_and_tells_the_person_once(bot, manager, sync):
    manager.add("GET", "/api/games", (401, {"detail": "This API token is not valid (any more)"}))
    the_link = link(bot)
    await sync.sync_user(the_link)
    assert bot.links.get(1001).status == "invalid"
    assert sync.heard.stopped == [1001]
    await sync.run_once()  # an invalid link is no longer looked at, so nobody is told again
    assert sync.heard.stopped == [1001]
    assert len(manager.requests) == 1


async def test_a_refused_token_makes_the_bot_forget_what_it_knew(bot, manager, sync):
    the_link = link(bot)
    games(manager, game_json(7, my_slots=MINE))
    await sync.sync_user(the_link)
    manager.add("GET", "/api/games", (401, {"detail": "nope"}))
    await sync.sync_user(the_link)
    assert sync.participants(7) == {} and sync.memberships() == {}


async def test_a_manager_that_does_not_answer_changes_nothing(bot, manager, sync):
    the_link = link(bot)
    games(manager, game_json(7, my_slots=MINE))
    await sync.sync_user(the_link)
    manager.add("GET", "/api/games", (503, {"detail": "down"}))
    await sync.sync_user(the_link)
    assert bot.links.get(1001).status == "active"
    assert list(sync.participants(7)) == [1001]
    assert len(sync.heard.memberships) == 1


async def test_an_answer_that_is_not_a_list_is_ignored(bot, manager, sync):
    manager.add("GET", "/api/games", {"games": []})
    await sync.sync_user(link(bot))
    assert sync.heard.memberships == [] and sync.memberships() == {}


async def test_one_broken_person_does_not_stop_the_others(bot, manager, sync):
    link(bot, 1001, 10, "apm_firstUserToken")
    link(bot, 2002, 20, "apm_secondUserToken")

    async def failing(events):
        raise RuntimeError("boom")

    sync.listen(on_events=failing, on_memberships=sync.heard.on_memberships)
    games(manager, game_json(7, my_slots=MINE))
    await sync.run_once()
    assert {discord_id for discord_id, _ in sync.heard.memberships} == {1001, 2002}


async def test_people_who_unlinked_are_forgotten(bot, manager, sync):
    the_link = link(bot)
    games(manager, game_json(7, my_slots=MINE))
    await sync.sync_user(the_link)
    bot.links.delete(1001)
    await sync.run_once()
    assert sync.memberships() == {}


# --- what the others ask -----------------------------------------------------------------------------------------------
async def test_participants_slots_and_known_games_come_from_the_latest_look(bot, manager, sync):
    link(bot, 1001, 10, "apm_firstUserToken")
    link(bot, 2002, 20, "apm_secondUserToken")
    games(manager, game_json(7, name="Private run", my_slots=MINE), game_json(8, my_slots=[]))
    await sync.run_once()
    assert list(sync.participants(7)) == [1001, 2002]
    assert [(s.slot, s.name) for s in sync.slots_of(1001, 7)] == [(1, "Alice")]
    assert sync.slots_of(1001, 8) == () and sync.slots_of(9999, 7) == ()
    assert set(sync.known_games()) == {7}
    assert sync.known_games()[7].name == "Private run"
    sync.forget(1001)
    assert list(sync.participants(7)) == [2002]


# --- people whose link stops working lose their game roles --------------------------------------------------------------
async def test_a_refused_token_tells_the_listener_the_person_plays_in_nothing_now(bot, manager, sync):
    the_link = link(bot)
    games(manager, game_json(7, my_slots=MINE))
    await sync.sync_user(the_link)
    manager.add("GET", "/api/games", (401, {"detail": "nope"}))
    await sync.sync_user(the_link)
    assert sync.heard.memberships[-1] == (1001, frozenset())


async def test_a_link_deleted_outside_unlink_tells_the_listener_too(bot, manager, sync):
    the_link = link(bot)
    games(manager, game_json(7, my_slots=MINE))
    await sync.sync_user(the_link)
    bot.links.delete(1001)
    await sync.run_once()
    assert sync.heard.memberships[-1] == (1001, frozenset())


def change_key(bot):
    from cryptography.fernet import Fernet

    from apbot.crypto import TokenCipher

    bot.access._cipher = TokenCipher([Fernet.generate_key().decode()])


async def test_a_token_that_cannot_be_decrypted_is_skipped_and_the_link_kept(bot, manager, sync):
    the_link = link(bot)
    games(manager, game_json(7, my_slots=MINE))
    await sync.sync_user(the_link)
    before = bot.links.get(1001).token_ciphertext
    heard = len(sync.heard.memberships)
    change_key(bot)  # the key changed
    await sync.sync_user(bot.links.get(1001))
    assert len(sync.heard.memberships) == heard
    assert sync.heard.memberships[-1] == (1001, frozenset({7}))
    assert sync.heard.stopped == []
    assert [g.id for g in sync.games_of(1001)] == [7]
    saved = bot.links.get(1001)
    assert saved.status == "active" and saved.token_ciphertext == before


async def test_unreadable_links_are_counted_in_one_warning_per_start(bot, manager, sync, caplog):
    import logging

    link(bot, 1001)
    link(bot, 1002, manager_id=11)
    link(bot, 1003, manager_id=12)
    change_key(bot)
    bot.links.save(1003, 12, "cy", bot.access._cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")  # readable again
    games(manager)
    with caplog.at_level(logging.DEBUG):
        await sync.run_once()
        await sync.run_once()
    warnings = [r for r in caplog.records if r.name == "apbot.services.user_sync" and r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "2 linked people" in warnings[0].getMessage()
    assert USER_TOKEN not in caplog.text


async def test_no_warning_when_every_link_is_readable(bot, manager, sync, caplog):
    import logging

    link(bot)
    games(manager)
    with caplog.at_level(logging.DEBUG):
        await sync.run_once()
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


async def test_someone_never_seen_is_not_announced_as_leaving(bot, manager, sync):
    bot.links.save(1001, 10, "alice", b"cipher", "abcd", "en")
    bot.links.mark_invalid(1001)
    await sync.run_once()
    assert sync.heard.memberships == []


async def test_the_games_of_a_person_are_those_they_play_in_ongoing_ones_first(bot, manager, sync):
    games(
        manager,
        game_json(1, "Old", my_slots=MINE, finished_at=5.0),
        game_json(2, "Mine", my_slots=MINE),
        game_json(3, "Not mine"),
        game_json(4, "Newer", my_slots=MINE),
    )
    await sync.sync_user(link(bot))
    assert [g.name for g in sync.games_of(1001)] == ["Newer", "Mine", "Old"]
    assert sync.games_of(9999) == []


# --- friend requests ---------------------------------------------------------------------------------------------------
FRIENDS = "/api/friends"


class Asked:
    """What UserSync told its friend-request listener, and who has the alert on."""

    def __init__(self, *wanting: int):
        self.wanting = set(wanting)
        self.events: list[list[FriendRequestEvent]] = []

    def wants(self, discord_id: int) -> bool:
        return discord_id in self.wanting

    async def on_friend_requests(self, events):
        self.events.append(events)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def friend_sync(bot, manager):
    async def no_wait(seconds):
        pass

    clock = Clock()
    sync = UserSync(bot.links, bot.access, manager.client, sleep=no_wait, now=clock)
    sync.heard, sync.asked, sync.clock = Heard(), Asked(1001), clock
    sync.listen(
        on_events=sync.heard.on_events, on_memberships=sync.heard.on_memberships,
        on_token_stopped=sync.heard.on_token_stopped, on_friend_requests=sync.asked.on_friend_requests,
        wants_friend_requests=sync.asked.wants,
    )
    manager.add("GET", "/api/games", [])
    return sync


def asking(*people) -> dict:
    return friends_json(incoming=[friend_json(user_id, name, asked_at=100.0) for user_id, name in people])


def friend_calls(manager) -> list:
    return [r for r in manager.requests if r.url.path == FRIENDS]


async def test_friends_are_not_looked_at_for_someone_who_did_not_turn_the_alert_on(bot, manager, friend_sync):
    friend_sync.asked.wanting.clear()
    await friend_sync.sync_user(link(bot))
    assert friend_calls(manager) == [] and friend_sync.asked.events == []


async def test_without_a_listener_nothing_is_asked_either(bot, manager):
    async def no_wait(seconds):
        pass

    sync = UserSync(bot.links, bot.access, manager.client, sleep=no_wait)  # nobody called listen with the friend parts
    manager.add("GET", "/api/games", [])
    await sync.sync_user(link(bot))
    assert friend_calls(manager) == []


async def test_the_first_look_only_remembers_and_a_new_request_is_one_event(bot, manager, friend_sync):
    the_link = link(bot)
    manager.add("GET", FRIENDS, asking((21, "cy")))
    await friend_sync.sync_user(the_link)
    assert friend_sync.asked.events == []  # a restart must not bring old requests back as news
    manager.add("GET", FRIENDS, asking((21, "cy"), (22, "dee")))
    await friend_sync.sync_user(the_link)
    [[event]] = friend_sync.asked.events
    assert event == FriendRequestEvent(1001, 22, "dee")
    assert friend_calls(manager)[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"


async def test_the_same_request_next_time_is_no_event(bot, manager, friend_sync):
    the_link = link(bot)
    manager.add("GET", FRIENDS, asking())
    await friend_sync.sync_user(the_link)
    manager.add("GET", FRIENDS, asking((21, "cy")))
    await friend_sync.sync_user(the_link)
    await friend_sync.sync_user(the_link)
    assert len(friend_sync.asked.events) == 1


async def test_someone_who_asks_again_after_being_declined_is_new_again(bot, manager, friend_sync):
    the_link = link(bot)
    manager.add("GET", FRIENDS, asking())
    await friend_sync.sync_user(the_link)
    for state in (asking((21, "cy")), asking(), asking((21, "cy"))):
        manager.add("GET", FRIENDS, state)
        await friend_sync.sync_user(the_link)
    assert [[e.from_id for e in events] for events in friend_sync.asked.events] == [[21], [21]]


async def test_several_new_requests_arrive_together_in_one_call(bot, manager, friend_sync):
    the_link = link(bot)
    manager.add("GET", FRIENDS, asking())
    await friend_sync.sync_user(the_link)
    manager.add("GET", FRIENDS, asking((21, "cy"), (22, "dee")))
    await friend_sync.sync_user(the_link)
    [events] = friend_sync.asked.events
    assert [(e.from_id, e.from_name) for e in events] == [(21, "cy"), (22, "dee")]


async def test_outgoing_requests_and_friends_are_no_events(bot, manager, friend_sync):
    the_link = link(bot)
    manager.add("GET", FRIENDS, asking())
    await friend_sync.sync_user(the_link)
    manager.add(
        "GET", FRIENDS,
        friends_json(friends=[friend_json(20, "bob", since=1.0)], outgoing=[friend_json(22, "dee", asked_at=1.0)]),
    )
    await friend_sync.sync_user(the_link)
    assert friend_sync.asked.events == []


async def test_turning_the_alert_off_and_on_makes_the_first_look_quiet_again(bot, manager, friend_sync):
    the_link = link(bot)
    manager.add("GET", FRIENDS, asking())
    await friend_sync.sync_user(the_link)
    friend_sync.asked.wanting.clear()
    manager.add("GET", FRIENDS, asking((21, "cy")))
    await friend_sync.sync_user(the_link)  # off: not even asked
    assert len(friend_calls(manager)) == 1
    friend_sync.asked.wanting.add(1001)
    await friend_sync.sync_user(the_link)  # on again: the request that came meanwhile is not news
    assert friend_sync.asked.events == [] and len(friend_calls(manager)) == 2


async def test_a_token_the_manager_refuses_at_friends_ends_the_link_like_at_games(bot, manager, friend_sync):
    manager.add("GET", FRIENDS, (401, {"detail": "This API token is not valid (any more)"}))
    await friend_sync.sync_user(link(bot))
    assert bot.links.get(1001).status == "invalid"
    assert friend_sync.heard.stopped == [1001]


async def test_a_manager_without_friends_is_left_alone_for_an_hour_and_games_go_on(bot, manager, friend_sync):
    the_link = link(bot)
    games(manager, game_json(7, state="stopped", my_slots=MINE))
    await friend_sync.sync_user(the_link)  # /api/friends: 404
    assert len(friend_calls(manager)) == 1
    friend_sync.clock.now += FRIENDS_RETRY - 1
    games(manager, game_json(7, state="running", my_slots=MINE))
    await friend_sync.sync_user(the_link)
    assert len(friend_calls(manager)) == 1  # not asked again
    assert [e.kind for [e] in friend_sync.heard.events] == [GAME_ONLINE]  # the games part is not affected
    friend_sync.clock.now += 2
    manager.add("GET", FRIENDS, asking())
    await friend_sync.sync_user(the_link)
    assert len(friend_calls(manager)) == 2 and FRIENDS_RETRY == 3600


async def test_a_manager_that_fails_or_answers_oddly_changes_nothing_and_is_asked_again(bot, manager, friend_sync):
    the_link = link(bot)
    manager.add("GET", FRIENDS, asking((21, "cy")))
    await friend_sync.sync_user(the_link)
    for broken in ((503, {"detail": "down"}), {"friends": "no"}):
        manager.add("GET", FRIENDS, broken)
        await friend_sync.sync_user(the_link)
    manager.add("GET", FRIENDS, asking((21, "cy"), (22, "dee")))
    await friend_sync.sync_user(the_link)
    assert [[e.from_id for e in events] for events in friend_sync.asked.events] == [[22]]  # what was known survived
    assert bot.links.get(1001).status == "active"


async def test_friends_are_not_looked_at_when_the_games_could_not_be_read(bot, manager, friend_sync):
    manager.add("GET", "/api/games", (503, {"detail": "down"}))
    await friend_sync.sync_user(link(bot))
    assert friend_calls(manager) == []


async def test_forgetting_a_person_forgets_their_requests(bot, manager, friend_sync):
    the_link = link(bot)
    manager.add("GET", FRIENDS, asking((21, "cy")))
    await friend_sync.sync_user(the_link)
    friend_sync.forget(1001)  # unlinked, or the token stopped working
    await friend_sync.sync_user(the_link)
    assert friend_sync.asked.events == []  # the first look after that is quiet again


async def test_people_who_unlinked_are_dropped_with_their_requests(bot, manager, friend_sync):
    the_link = link(bot)
    manager.add("GET", FRIENDS, asking((21, "cy")))
    await friend_sync.sync_user(the_link)
    bot.links.delete(1001)
    await friend_sync.run_once()
    assert friend_sync._incoming == {}


async def test_every_person_is_asked_with_their_own_token(bot, manager, friend_sync):
    link(bot, 1001, 10, "apm_firstUserToken")
    link(bot, 2002, 20, "apm_secondUserToken")
    friend_sync.asked.wanting.add(2002)
    manager.add("GET", FRIENDS, asking())
    await friend_sync.run_once()
    assert sorted(r.headers["Authorization"] for r in friend_calls(manager)) == [
        "Bearer apm_firstUserToken", "Bearer apm_secondUserToken",
    ]
