import pytest
from fakes import USER_TOKEN, game_json

from apbot.services.user_sync import GAME_ONLINE, SLOT_OFFERED, UserSync, events_between
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
