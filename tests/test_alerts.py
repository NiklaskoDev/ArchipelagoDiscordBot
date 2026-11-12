from types import SimpleNamespace

import discord
import pytest
from fakes import (
    USER_TOKEN,
    FakeDirectory,
    by_token,
    forbidden,
    game_json,
    hint_json,
    json_text,
    tracker_json,
)

from apbot.manager.views import FeedEntry, HintView
from apbot.services.alerts import (
    MAX_STUCK_ITEMS,
    MUTE_SECONDS,
    AlertContext,
    AlertEngine,
    alert_for_event,
    rules_for,
)
from apbot.services.feeds import FeedGame
from apbot.services.user_sync import GAME_ONLINE, SLOT_OFFERED, FriendRequestEvent, UserGameEvent, UserSync

GAME = FeedGame(7, "Friday Run", "running", True)
ALL = frozenset({"item_received", "hint_found", "stuck_in_my_world", "game_online", "slot_offered"})
MINE = [{"slot": 1, "name": "Alice", "game": "Timespinner", "idle_since": None}]


def feed_entry(kind: str, seq: int = 1, **fields) -> FeedEntry:
    return FeedEntry(seq=seq, at=0.0, kind=kind, fields=fields)


def item(finder="Bob", receiver="Alice", name="Sword", location="Chest", seq=1) -> FeedEntry:
    return feed_entry("item", seq, finder=finder, item=name, receiver=receiver, location=location)


def context(*enabled, names=("Alice",), hints=(), waiting=()) -> AlertContext:
    return AlertContext(frozenset(enabled), frozenset(names), frozenset(hints), tuple(waiting))


def waiting_item(name="Sword", location="Chest") -> HintView:
    return HintView(receiver="Cy", item=name, location=location, finder="Alice", found=False, for_me=False, in_my_world=True)


def texts(alerts, lang="en") -> list[str]:
    return [alert.text(lang) for alert in alerts]


# --- the rules: item_received ------------------------------------------------------------------------------------------
def test_an_item_for_one_of_my_slots_is_an_alert():
    alerts = rules_for(item(), GAME, context("item_received"))
    assert [a.kind for a in alerts] == ["item_received"]
    assert texts(alerts) == ["**Bob** sent Sword to **Alice** (Chest)"]


@pytest.mark.parametrize(
    ("entry", "ctx"),
    [
        (item(receiver="Cy"), context("item_received")),  # for someone else
        (item(finder="Alice"), context("item_received")),  # found in my own world, for me
        (item(), context()),  # the person did not ask for it
        (item(), context("hint_found", "game_online")),
        (feed_entry("chat", player="Bob", text="Sword"), context("item_received")),  # not an item
    ],
)
def test_other_items_are_no_alert(entry, ctx):
    assert rules_for(entry, GAME, ctx) == []


def test_an_alert_text_follows_the_language_and_cannot_format_or_ping():
    alerts = rules_for(item(finder="*Bob*", name="@everyone"), GAME, context("item_received"))
    assert texts(alerts, "de") == [f"**\\*Bob\\*** hat @{chr(0x200b)}everyone an **Alice** geschickt (Chest)"]


# --- hint_found --------------------------------------------------------------------------------------------------------
def test_an_item_that_fulfils_an_open_hint_is_an_alert():
    key = ("Alice", "Sword", "Bob", "Chest")
    alerts = rules_for(item(), GAME, context("hint_found", hints=[key]))
    assert [a.kind for a in alerts] == ["hint_found"]
    assert "Sword" in alerts[0].text("en") and "Chest" in alerts[0].text("en")


@pytest.mark.parametrize(
    ("entry", "ctx"),
    [
        (item(), context("hint_found")),  # no such hint
        (item(name="Bow"), context("hint_found", hints=[("Alice", "Sword", "Bob", "Chest")])),  # another item
        (item(location="Cave"), context("hint_found", hints=[("Alice", "Sword", "Bob", "Chest")])),
        (item(), context("item_received", hints=[("Alice", "Sword", "Bob", "Chest")])),  # not asked for
    ],
)
def test_an_item_that_matches_no_open_hint_is_no_hint_alert(entry, ctx):
    assert [a for a in rules_for(entry, GAME, ctx) if a.kind == "hint_found"] == []


def test_one_item_can_be_both_received_and_a_found_hint():
    ctx = context("item_received", "hint_found", hints=[("Alice", "Sword", "Bob", "Chest")])
    assert [a.kind for a in rules_for(item(), GAME, ctx)] == ["item_received", "hint_found"]


async def test_a_tracker_is_not_read_when_the_token_cannot_be_decrypted_and_the_link_is_kept(bot, make_engine):
    from cryptography.fernet import Fernet

    from apbot.crypto import TokenCipher

    live = make_engine("hint_found")
    await live.play()
    foreign = TokenCipher([Fernet.generate_key().decode()])
    bot.links.save(1001, 10, "alice", foreign.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
    before = bot.links.get(1001).token_ciphertext
    live.tracker(hint_json("Sword", "Alice", "Bob", "Chest"))
    await live.engine.handle(GAME, (item(),))
    assert live.dms == [] and live.tracker_requests() == []
    saved = bot.links.get(1001)
    assert saved.status == "active" and saved.token_ciphertext == before


# --- stuck_in_my_world -------------------------------------------------------------------------------------------------
def stuck(player="Cy") -> FeedEntry:
    return feed_entry("status", player=player, status="stuck")


def test_a_player_who_is_stuck_while_my_world_holds_items_for_them_is_an_alert():
    alerts = rules_for(stuck(), GAME, context("stuck_in_my_world", waiting=[waiting_item("Sword", "Chest")]))
    assert [a.kind for a in alerts] == ["stuck_in_my_world"]
    assert texts(alerts) == ["**Cy** is stuck and waits for items in your world: Sword (Chest)"]


def test_a_stuck_player_with_nothing_of_theirs_in_my_world_is_no_alert():
    assert rules_for(stuck(), GAME, context("stuck_in_my_world")) == []


@pytest.mark.parametrize(
    ("entry", "ctx"),
    [
        (stuck("Alice"), context("stuck_in_my_world", waiting=[waiting_item()])),  # I am the one who is stuck
        (feed_entry("status", player="Cy", status="away"), context("stuck_in_my_world", waiting=[waiting_item()])),
        (feed_entry("status", player="Cy"), context("stuck_in_my_world", waiting=[waiting_item()])),
        (stuck(), context("item_received", waiting=[waiting_item()])),  # not asked for
    ],
)
def test_other_status_changes_are_no_alert(entry, ctx):
    assert rules_for(entry, GAME, ctx) == []


def test_at_most_eight_items_are_listed_and_the_rest_counted():
    waiting = [waiting_item(f"Item {i}", f"Loc {i}") for i in range(MAX_STUCK_ITEMS + 3)]
    [alert] = rules_for(stuck(), GAME, context("stuck_in_my_world", waiting=waiting))
    text = alert.text("en")
    assert "Item 7 (Loc 7)" in text and "Item 8" not in text and text.endswith(", and 3 more")


# --- events from UserSync ----------------------------------------------------------------------------------------------
def test_a_game_going_online_is_an_alert_when_asked_for():
    event = UserGameEvent(GAME_ONLINE, 1001, 7, "Friday Run")
    alert = alert_for_event(event, ALL)
    assert (alert.kind, alert.game_name, alert.slot) == ("game_online", "Friday Run", None)
    assert alert.text("de") == "Das Spiel ist online"
    assert alert_for_event(event, frozenset({"item_received"})) is None


def test_an_offered_slot_is_an_alert_that_carries_the_slot_and_the_note():
    event = UserGameEvent(SLOT_OFFERED, 1001, 7, "Friday Run", 2, "Bob", "Hollow Knight", "bobby", "*no time*")
    alert = alert_for_event(event, ALL)
    assert alert.slot == 2
    assert alert.text("en") == "**bobby** offers you **Bob** (Hollow Knight) — \\*no time\\*"
    assert alert_for_event(event, frozenset({"game_online"})) is None


def test_an_event_of_a_kind_nobody_asked_for_is_no_alert():
    assert alert_for_event(UserGameEvent("teleport", 1, 7, "G"), ALL) is None


# --- the engine --------------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Engine:
    """The engine, a linked person who plays in game 7 as Alice, and their direct messages."""

    def __init__(self, bot, manager, *enabled, locale="en"):
        async def no_wait(seconds):
            pass

        self.bot, self.manager = bot, manager
        self.clock = Clock()
        self.directory = FakeDirectory()
        self.sync = UserSync(bot.links, bot.access, manager.client, sleep=no_wait)
        bot.links.save(1001, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], locale)
        bot.alert_prefs.replace(1001, enabled)
        self.engine = AlertEngine(
            bot.alert_prefs, bot.links, bot.access, self.sync, manager.client, self.directory, now=self.clock
        )

    async def play(self, *raw) -> None:
        """The person plays in game 7 (and whatever else `raw` lists), as the manager shows it to them."""
        games = list(raw) or [game_json(7, "Friday Run", my_slots=MINE)]
        self.manager.add("GET", "/api/games", by_token({USER_TOKEN: games}))
        await self.sync.sync_user(self.bot.links.get(1001))

    def tracker(self, *hints, slot=1, game=7) -> None:
        self.manager.add("GET", f"/api/games/{game}/slots/{slot}/tracker", by_token({USER_TOKEN: tracker_json(hints)}))

    @property
    def dms(self):
        user = self.directory.users.get(1001)
        return user.sent if user else []

    def tracker_requests(self) -> list:
        return [r for r in self.manager.requests if r.url.path.endswith("/tracker")]


@pytest.fixture
def make_engine(bot, manager):
    return lambda *enabled, **kwargs: Engine(bot, manager, *enabled, **kwargs)


async def test_an_item_for_the_person_arrives_as_one_direct_message(make_engine):
    live = make_engine("item_received")
    await live.play()
    await live.engine.handle(GAME, (item(), item(name="Bow", seq=2)))
    [message] = live.dms
    embed = message.kwargs["embed"]
    assert embed.title == "Friday Run"
    assert embed.description.split("\n") == [
        "**Bob** sent Sword to **Alice** (Chest)", "**Bob** sent Bow to **Alice** (Chest)",
    ]
    assert message.kwargs["allowed_mentions"].everyone is False


async def test_the_message_is_in_the_language_stored_for_the_person(make_engine):
    live = make_engine("item_received", locale="de")
    await live.play()
    await live.engine.handle(GAME, (item(),))
    assert live.dms[0].kwargs["embed"].description == "**Bob** hat Sword an **Alice** geschickt (Chest)"


async def test_nothing_is_sent_for_what_was_not_asked_for_and_the_manager_is_not_asked_either(make_engine):
    live = make_engine("game_online")
    await live.play()
    await live.engine.handle(GAME, (item(), stuck()))
    assert live.dms == [] and live.tracker_requests() == []


async def test_a_person_who_asked_for_nothing_gets_nothing(make_engine):
    live = make_engine()
    await live.play()
    await live.engine.handle(GAME, (item(),))
    assert live.dms == []


async def test_people_who_do_not_play_in_the_game_get_nothing(make_engine):
    live = make_engine("item_received")
    await live.play(game_json(8, my_slots=MINE))
    await live.engine.handle(GAME, (item(),))
    assert live.dms == []


async def test_an_entry_that_is_nothing_to_the_person_sends_nothing(make_engine):
    live = make_engine("item_received")
    await live.play()
    await live.engine.handle(GAME, (item(receiver="Cy"),))
    assert live.dms == []


# --- hint_found --------------------------------------------------------------------------------------------------------
async def test_an_item_that_was_hinted_is_reported_once_it_is_found(make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.tracker(hint_json("Sword", "Alice", "Bob", "Chest"))
    await live.engine.handle(GAME, (item(),))
    [message] = live.dms
    assert "Your hint was found: Sword for **Alice** at Chest in **Bob**'s world" in message.kwargs["embed"].description
    await live.engine.handle(GAME, (item(seq=2),))  # the same hint cannot be found twice
    assert len(live.dms) == 1


async def test_the_hints_are_read_from_the_tracker_once_and_then_kept_current_from_the_feed(make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.tracker()
    await live.engine.handle(GAME, (feed_entry("chat", player="B", text="hi"),))
    new_hint = feed_entry("hint", 2, receiver="Alice", item="Bow", location="Cave", finder="Bob", status="not found")
    await live.engine.handle(GAME, (new_hint,))
    await live.engine.handle(GAME, (item(name="Bow", location="Cave", seq=3),))
    assert len(live.tracker_requests()) == 1
    assert len(live.dms) == 1 and "Bow" in live.dms[0].kwargs["embed"].description


async def test_a_hint_that_the_feed_says_is_found_is_closed(make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.tracker(hint_json("Sword", "Alice", "Bob", "Chest"))
    found = feed_entry("hint", 2, receiver="Alice", item="Sword", location="Chest", finder="Bob", status="found")
    await live.engine.handle(GAME, (found,))
    await live.engine.handle(GAME, (item(seq=3),))
    assert live.dms == []


async def test_hints_for_other_people_and_found_ones_are_not_open_hints(make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.tracker(
        hint_json("Sword", "Alice", "Bob", "Chest", found=True),
        hint_json("Bow", "Alice", "Bob", "Cave", for_me=False, in_my_world=True),
    )
    await live.engine.handle(GAME, (feed_entry("chat", player="B", text="hi"),))  # the list is read
    await live.engine.handle(GAME, (item(seq=2), item(name="Bow", location="Cave", seq=3)))
    assert live.dms == []


async def test_a_tracker_that_cannot_be_read_gives_no_hint_alert_and_is_tried_again(make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.manager.add("GET", "/api/games/7/slots/1/tracker", (503, {"detail": "busy"}))
    await live.engine.handle(GAME, (item(),))
    assert live.dms == []
    live.tracker(hint_json("Sword", "Alice", "Bob", "Chest"))
    await live.engine.handle(GAME, (item(seq=2),))
    assert len(live.dms) == 1


async def test_a_tracker_the_manager_refuses_marks_the_link_invalid(bot, make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.manager.add("GET", "/api/games/7/slots/1/tracker", (401, {"detail": "no"}))
    await live.engine.handle(GAME, (item(),))
    assert bot.links.get(1001).status == "invalid"


# --- stuck_in_my_world -------------------------------------------------------------------------------------------------
async def test_a_stuck_player_is_reported_with_the_items_in_my_world_they_wait_for(make_engine):
    live = make_engine("stuck_in_my_world")
    await live.play()
    live.tracker(
        hint_json("Sword", "Cy", "Alice", "Chest", for_me=False, in_my_world=True),
        hint_json("Bow", "Dee", "Alice", "Cave", for_me=False, in_my_world=True),  # for somebody else
        hint_json("Key", "Cy", "Alice", "Door", for_me=False, in_my_world=True, found=True),
    )
    await live.engine.handle(GAME, (stuck("Cy"),))
    [message] = live.dms
    assert message.kwargs["embed"].description == "**Cy** is stuck and waits for items in your world: Sword (Chest)"


async def test_a_stuck_player_with_nothing_in_my_world_sends_no_message(make_engine):
    live = make_engine("stuck_in_my_world")
    await live.play()
    live.tracker(hint_json("Bow", "Dee", "Alice", "Cave", for_me=False, in_my_world=True))
    await live.engine.handle(GAME, (stuck("Cy"),))
    assert live.dms == [] and len(live.tracker_requests()) == 1


async def test_my_own_stuck_status_does_not_ask_the_manager(make_engine):
    live = make_engine("stuck_in_my_world")
    await live.play()
    await live.engine.handle(GAME, (stuck("Alice"),))
    assert live.tracker_requests() == [] and live.dms == []


async def test_every_slot_of_the_person_is_looked_at(make_engine):
    live = make_engine("stuck_in_my_world")
    two_slots = MINE + [{"slot": 4, "name": "Alice2", "game": "Zelda", "idle_since": None}]
    await live.play(game_json(7, my_slots=two_slots))
    live.tracker(hint_json("Sword", "Cy", "Alice", "Chest", for_me=False, in_my_world=True))
    live.tracker(hint_json("Bow", "Cy", "Alice2", "Cave", for_me=False, in_my_world=True), slot=4)
    await live.engine.handle(GAME, (stuck("Cy"),))
    assert "Sword (Chest), Bow (Cave)" in live.dms[0].kwargs["embed"].description


# --- closed direct messages --------------------------------------------------------------------------------------------
async def test_someone_who_closed_their_dms_is_left_alone_for_a_day(make_engine):
    live = make_engine("item_received")
    await live.play()
    (await live.directory(1001)).fail_with = forbidden()
    await live.engine.handle(GAME, (item(),))
    (await live.directory(1001)).fail_with = None
    await live.engine.handle(GAME, (item(seq=2),))
    assert live.dms == []  # muted: not even tried
    live.clock.now += MUTE_SECONDS - 1
    await live.engine.handle(GAME, (item(seq=3),))
    assert live.dms == []
    live.clock.now += 2
    await live.engine.handle(GAME, (item(seq=4),))
    assert len(live.dms) == 1


async def test_a_direct_message_that_fails_for_another_reason_does_not_mute_or_raise(make_engine):
    live = make_engine("item_received")
    await live.play()
    (await live.directory(1001)).fail_with = discord.HTTPException(SimpleNamespace(status=500, reason="oops"), "oops")
    await live.engine.handle(GAME, (item(),))
    (await live.directory(1001)).fail_with = None
    await live.engine.handle(GAME, (item(seq=2),))
    assert len(live.dms) == 1


# --- events ------------------------------------------------------------------------------------------------------------
async def test_a_game_going_online_is_sent_when_asked_for(make_engine):
    live = make_engine("game_online")
    await live.engine.handle_events([UserGameEvent(GAME_ONLINE, 1001, 7, "Friday Run")])
    [message] = live.dms
    assert (message.kwargs["embed"].title, message.kwargs["embed"].description) == ("Friday Run", "The game is online")
    assert "view" not in message.kwargs


async def test_a_game_going_online_is_not_sent_when_not_asked_for(make_engine):
    live = make_engine("item_received")
    await live.engine.handle_events([UserGameEvent(GAME_ONLINE, 1001, 7, "Friday Run")])
    assert live.dms == []


async def test_an_offered_slot_comes_with_buttons_to_take_or_decline_it(make_engine):
    live = make_engine("slot_offered")
    event = UserGameEvent(SLOT_OFFERED, 1001, 7, "Friday Run", 2, "Bob", "Hollow Knight", "bobby", None)
    await live.engine.handle_events([event])
    [message] = live.dms
    view = message.kwargs["view"]
    assert [child.custom_id for child in view.children] == ["apbot:ho:accept:7:2", "apbot:ho:decline:7:2"]
    assert [child.item.label for child in view.children] == ["Take over slot", "Decline"]
    assert "offers you **Bob**" in message.kwargs["embed"].description
    assert view.timeout is None


async def test_every_offer_gets_its_own_message_and_games_are_not_mixed(make_engine):
    live = make_engine("slot_offered", "game_online")
    events = [
        UserGameEvent(GAME_ONLINE, 1001, 7, "One"),
        UserGameEvent(GAME_ONLINE, 1001, 8, "Two"),
        UserGameEvent(SLOT_OFFERED, 1001, 7, "One", 2, "Bob", "W", "b", None),
        UserGameEvent(SLOT_OFFERED, 1001, 7, "One", 3, "Cy", "W", "c", None),
    ]
    await live.engine.handle_events(events)
    assert [m.kwargs["embed"].title for m in live.dms] == ["One", "Two", "One", "One"]
    assert [bool(m.kwargs.get("view")) for m in live.dms] == [False, False, True, True]


async def test_events_for_someone_who_unlinked_send_nothing(bot, make_engine):
    live = make_engine("game_online")
    bot.links.delete(1001)
    await live.engine.handle_events([UserGameEvent(GAME_ONLINE, 1001, 7, "Friday Run")])
    assert live.dms == []


async def test_a_stopped_token_is_told_once_in_the_persons_language_whatever_they_asked_for(bot, make_engine):
    live = make_engine(locale="fr")
    await live.engine.token_stopped(bot.links.get(1001))
    [message] = live.dms
    assert message.kwargs["embed"].description == "Ton jeton du manager ne fonctionne plus. Crée-en un nouveau et relance /link"
    assert USER_TOKEN not in json_text(message.kwargs["embed"])


# --- the open-hint list is kept honest ---------------------------------------------------------------------------------
async def test_a_hint_made_while_hint_alerts_were_off_is_not_missed_after_turning_them_on(bot, make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.tracker()
    await live.engine.handle(GAME, (feed_entry("chat", player="B", text="hi"),))  # the list is read: no hints
    bot.alert_prefs.replace(1001, ["item_received"])
    await live.engine.handle(GAME, (feed_entry("chat", seq=2, player="B", text="hi"),))
    live.tracker(hint_json("Sword", "Alice", "Bob", "Chest"))  # a hint is made while the alert is off
    bot.alert_prefs.replace(1001, ["hint_found"])
    await live.engine.handle(GAME, (feed_entry("chat", seq=3, player="B", text="hi"),))
    await live.engine.handle(GAME, (item(seq=4),))
    assert [m.kwargs["embed"].description for m in live.dms] == [
        "Your hint was found: Sword for **Alice** at Chest in **Bob**'s world"
    ]


async def test_a_slot_the_person_gains_later_is_read_too(make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.tracker()
    await live.engine.handle(GAME, (feed_entry("chat", player="B", text="hi"),))
    both = MINE + [{"slot": 4, "name": "Alice2", "game": "Zelda", "idle_since": None}]
    await live.play(game_json(7, "Friday Run", my_slots=both))  # e.g. after taking a slot over
    live.tracker(hint_json("Bow", "Alice2", "Bob", "Cave"), slot=4)
    await live.engine.handle(GAME, (item(receiver="Alice2", name="Bow", location="Cave", seq=2),))
    assert len(live.dms) == 1 and "Bow for **Alice2**" in live.dms[0].kwargs["embed"].description


async def test_forgetting_a_person_makes_the_next_batch_read_their_hints_again(make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.tracker()
    await live.engine.handle(GAME, (feed_entry("chat", player="B", text="hi"),))
    live.tracker(hint_json("Sword", "Alice", "Bob", "Chest"))
    await live.engine.handle(GAME, (item(seq=2),))
    assert live.dms == []  # still the old list
    live.engine.forget(1001)
    await live.engine.handle(GAME, (item(seq=3),))
    assert len(live.dms) == 1


async def test_a_hint_that_is_fulfilled_in_the_batch_that_seeds_the_list_still_alerts(make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.tracker(hint_json("Sword", "Alice", "Bob", "Chest", found=True))  # the tracker is read after the batch
    await live.engine.handle(GAME, (item(),))
    assert len(live.dms) == 1


async def test_a_hint_that_was_found_long_before_does_not_alert_in_the_seeding_batch(make_engine):
    live = make_engine("hint_found")
    await live.play()
    live.tracker(hint_json("Sword", "Alice", "Bob", "Chest", found=True))
    await live.engine.handle(GAME, (feed_entry("chat", player="B", text="hi"), item(name="Bow", location="Cave", seq=2)))
    assert live.dms == []


# --- friend requests ---------------------------------------------------------------------------------------------------
async def test_a_friend_request_comes_with_buttons_to_accept_or_decline_it(make_engine):
    live = make_engine("friend_request")
    await live.engine.handle_friend_requests([FriendRequestEvent(1001, 21, "cy")])
    [message] = live.dms
    embed, view = message.kwargs["embed"], message.kwargs["view"]
    assert (embed.title, embed.description) == ("Friend request", "**cy** wants to be your friend on the manager")
    assert [child.custom_id for child in view.children] == ["apbot:fr:accept:21", "apbot:fr:decline:21"]
    assert [child.item.label for child in view.children] == ["Accept", "Decline"]
    assert view.timeout is None and message.kwargs["allowed_mentions"].everyone is False


async def test_the_askers_name_cannot_format_or_ping(make_engine):
    live = make_engine("friend_request")
    await live.engine.handle_friend_requests([FriendRequestEvent(1001, 21, "@everyone **x**")])
    description = live.dms[0].kwargs["embed"].description
    assert "@everyone" not in description and "\\*\\*x\\*\\*" in description


async def test_every_request_gets_its_own_message(make_engine):
    live = make_engine("friend_request")
    await live.engine.handle_friend_requests([FriendRequestEvent(1001, 21, "cy"), FriendRequestEvent(1001, 22, "dee")])
    assert [m.kwargs["view"].children[0].custom_id for m in live.dms] == ["apbot:fr:accept:21", "apbot:fr:accept:22"]


async def test_the_request_message_is_in_the_language_stored_for_the_person(make_engine):
    live = make_engine("friend_request", locale="de")
    await live.engine.handle_friend_requests([FriendRequestEvent(1001, 21, "cy")])
    embed = live.dms[0].kwargs["embed"]
    assert (embed.title, embed.description) == ("Freundschaftsanfrage", "**cy** möchte im Manager mit dir befreundet sein")
    assert [c.item.label for c in live.dms[0].kwargs["view"].children] == ["Annehmen", "Ablehnen"]


async def test_nothing_is_sent_when_the_alert_is_off(make_engine):
    live = make_engine("game_online")
    await live.engine.handle_friend_requests([FriendRequestEvent(1001, 21, "cy")])
    assert live.dms == []


async def test_a_person_who_unlinked_gets_no_friend_request_message(bot, make_engine):
    live = make_engine("friend_request")
    bot.links.delete(1001)
    await live.engine.handle_friend_requests([FriendRequestEvent(1001, 21, "cy")])
    assert live.dms == []


async def test_a_person_who_closed_their_dms_is_left_alone_for_a_day(make_engine):
    live = make_engine("friend_request")
    (await live.directory(1001)).fail_with = forbidden()
    await live.engine.handle_friend_requests([FriendRequestEvent(1001, 21, "cy"), FriendRequestEvent(1001, 22, "dee")])
    (await live.directory(1001)).fail_with = None
    await live.engine.handle_friend_requests([FriendRequestEvent(1001, 23, "eve")])
    assert live.dms == []  # muted: not even tried again
    live.clock.now += MUTE_SECONDS + 1
    await live.engine.handle_friend_requests([FriendRequestEvent(1001, 23, "eve")])
    assert len(live.dms) == 1



# --- a friend generates a game with the person's config --------------------------------------------------------------------
async def tell(live, bot, **kwargs):
    values = {"by": "bob", "game": "Friday Run", "configs": ["alice"], **kwargs}
    await live.engine.friend_game(bot.links.get(1001), **values)


async def test_a_friend_generating_a_game_with_the_persons_config_is_one_direct_message(bot, make_engine):
    live = make_engine("friend_game")
    await tell(live, bot, configs=["alice", "alice 2"])
    [message] = live.dms
    embed = message.kwargs["embed"]
    assert embed.title == "A friend puts you in a game"
    assert embed.description == (
        "**bob** is generating a game with your config alice, alice 2: **Friday Run**. "
        "Once it is ready it is among your games."
    )
    assert message.kwargs["allowed_mentions"].everyone is False and "view" not in message.kwargs


async def test_a_game_without_a_name_is_called_a_new_game(bot, make_engine):
    live = make_engine("friend_game")
    await tell(live, bot, game=None)
    await tell(live, bot, game="")
    assert [m.kwargs["embed"].description.split(": ")[1].split(".")[0] for m in live.dms] == ["**a new game**"] * 2


async def test_the_friend_game_message_is_in_the_language_stored_for_the_person(bot, make_engine):
    live = make_engine("friend_game", locale="de")
    await tell(live, bot, game=None)
    embed = live.dms[0].kwargs["embed"]
    assert embed.title == "Ein Freund nimmt dich in ein Spiel auf"
    assert embed.description == (
        "**bob** generiert ein Spiel mit deiner Config alice: **ein neues Spiel**. "
        "Sobald es fertig ist, gehört es zu deinen Spielen."
    )


async def test_names_cannot_format_or_ping(bot, make_engine):
    live = make_engine("friend_game")
    await tell(live, bot, by="@everyone **x**", game="*run* @here", configs=["@everyone", "_a_"])
    description = live.dms[0].kwargs["embed"].description
    assert "@everyone" not in description and "@here" not in description
    assert "\\*\\*x\\*\\*" in description and "\\*run\\*" in description and "\\_a\\_" in description


async def test_nothing_is_sent_when_the_alert_is_off_even_with_other_alerts_on(bot, make_engine):
    live = make_engine("friend_request", "game_online")
    await tell(live, bot)
    assert live.dms == []


async def test_a_person_who_closed_their_dms_is_left_alone_for_a_day_for_this_alert_too(bot, make_engine):
    live = make_engine("friend_game")
    (await live.directory(1001)).fail_with = forbidden()
    await tell(live, bot)
    (await live.directory(1001)).fail_with = None
    await tell(live, bot)
    assert live.dms == []
    live.clock.now += MUTE_SECONDS + 1
    await tell(live, bot)
    assert len(live.dms) == 1
