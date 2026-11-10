from datetime import UTC, datetime, timedelta

import discord
import pytest
from fakes import BOT_TOKEN, SITE_URL, FakeDiscordClient, FakeGuild, forbidden, game_json, signup_json

from apbot.services.events import Events

GUILD = 5000
NOW = 1_800_000_000.0
HOUR = 3600


def at(unix: float) -> datetime:
    return datetime.fromtimestamp(unix, tz=UTC)


def planned(start: float) -> dict:
    return {"at": start, "opens_at": start - 600, "open_minutes": 10, "countdown": False, "stage": "planned"}


class World:
    def __init__(self, bot, manager):
        self.bot, self.manager = bot, manager
        self.guild = FakeGuild(GUILD)
        self.discord = FakeDiscordClient(self.guild)
        self.events = Events(
            self.discord, bot._event_store, bot.guild_settings, bot.cache, manager.client, BOT_TOKEN, now=lambda: NOW
        )
        bot.guild_settings.update(GUILD, scheduled_events=True)
        self.signups()

    async def games(self, *raw) -> None:
        self.manager.add("GET", "/api/games", list(raw))
        self.manager.add("GET", "/api/auth/status", {"user": {"id": 2}, "site_url": SITE_URL})
        await self.bot.cache.refresh()

    def signups(self, *raw) -> None:
        self.manager.add("GET", "/api/signups", list(raw))

    def rows(self) -> dict:
        return {(r.source, r.source_id): r for r in self.bot._event_store.for_guild(GUILD)}


@pytest.fixture
def world(bot, manager):
    return World(bot, manager)


async def test_a_game_with_a_planned_start_gets_an_event_that_lasts_three_hours(world):
    await world.games(game_json(7, "Friday Run", start=planned(NOW + 2 * HOUR)))
    await world.events.sync()
    [created] = world.guild.created_events
    assert created["name"] == "Start: Friday Run"
    assert created["start_time"] == at(NOW + 2 * HOUR) and created["end_time"] == at(NOW + 5 * HOUR)
    assert created["entity_type"] == discord.EntityType.external
    assert created["privacy_level"] == discord.PrivacyLevel.guild_only
    assert created["location"] == "ap.example:38281"
    row = world.rows()[("game_start", 7)]
    assert (row.event_id, row.starts_at) == (next(iter(world.guild.events)), NOW + 2 * HOUR)


async def test_an_open_signup_with_a_future_deadline_gets_an_event_that_lasts_fifteen_minutes(world):
    await world.games()
    world.signups(signup_json(3, "Spring Async", deadline=NOW + HOUR))
    await world.events.sync()
    [created] = world.guild.created_events
    assert created["name"] == "Sign-ups close: Spring Async"
    assert created["start_time"] == at(NOW + HOUR) and created["end_time"] == at(NOW + HOUR) + timedelta(minutes=15)
    assert created["location"] == SITE_URL
    assert ("signup_deadline", 3) in world.rows()


async def test_without_an_address_or_web_address_the_location_is_still_set(world, bot):
    await world.games(game_json(7, address=None, start=planned(NOW + HOUR)))
    bot.cache.set_site_url(None)
    bot.cache._site_url_checked_at = NOW + 10_000  # not asked again
    await world.events.sync()
    assert world.guild.created_events[0]["location"] == "Archipelago"


async def test_names_follow_the_servers_language(world, bot):
    bot.guild_settings.update(GUILD, language="de")
    await world.games(game_json(7, "Friday Run", start=planned(NOW + HOUR)))
    await world.events.sync()
    assert world.guild.created_events[0]["name"] == "Spielstart: Friday Run"


async def test_long_names_fit_discords_limit(world):
    await world.games(game_json(7, "x" * 300, start=planned(NOW + HOUR)))
    await world.events.sync()
    assert len(world.guild.created_events[0]["name"]) == 100


async def test_an_unchanged_start_is_left_alone(world):
    await world.games(game_json(7, start=planned(NOW + HOUR)))
    await world.events.sync()
    await world.events.sync()
    [event] = world.guild.events.values()
    assert len(world.guild.created_events) == 1 and event.edits == [] and not event.deleted


async def test_a_moved_start_moves_the_event(world):
    await world.games(game_json(7, "Friday Run", start=planned(NOW + HOUR)))
    await world.events.sync()
    await world.games(game_json(7, "Friday Run", start=planned(NOW + 3 * HOUR)))
    await world.events.sync()
    [event] = world.guild.events.values()
    assert len(world.guild.created_events) == 1
    assert event.edits == [{"name": "Start: Friday Run", "start_time": at(NOW + 3 * HOUR), "end_time": at(NOW + 6 * HOUR)}]
    assert world.rows()[("game_start", 7)].starts_at == NOW + 3 * HOUR


async def test_a_moved_start_whose_event_was_deleted_by_hand_makes_a_new_event(world):
    await world.games(game_json(7, start=planned(NOW + HOUR)))
    await world.events.sync()
    world.guild.events.clear()
    await world.games(game_json(7, start=planned(NOW + 2 * HOUR)))
    await world.events.sync()
    assert len(world.guild.events) == 1
    assert world.rows()[("game_start", 7)].event_id in world.guild.events


async def test_a_start_that_has_passed_removes_the_event(world):
    await world.games(game_json(7, start=planned(NOW + HOUR)))
    await world.events.sync()
    [event] = world.guild.events.values()
    await world.games(game_json(7, start=planned(NOW - 60)))
    await world.events.sync()
    assert event.deleted is True and world.rows() == {}


async def test_a_game_or_round_that_is_gone_removes_its_event(world):
    await world.games(game_json(7, start=planned(NOW + HOUR)))
    world.signups(signup_json(3, deadline=NOW + HOUR))
    await world.events.sync()
    events = list(world.guild.events.values())
    assert len(events) == 2
    await world.games()
    world.signups()
    await world.events.sync()
    assert all(event.deleted for event in events) and world.rows() == {}


async def test_an_event_deleted_by_hand_is_forgotten_when_its_source_is_gone(world):
    await world.games(game_json(7, start=planned(NOW + HOUR)))
    await world.events.sync()
    world.guild.events.clear()
    await world.games()
    await world.events.sync()  # no error
    assert world.rows() == {}


async def test_a_round_that_closed_or_has_no_deadline_gets_no_event(world):
    await world.games()
    world.signups(
        signup_json(1, open=False, status="closed", deadline=NOW + HOUR),
        signup_json(2, deadline=None),
        signup_json(3, deadline=NOW - 5),
    )
    await world.events.sync()
    assert world.guild.created_events == []


async def test_games_without_a_start_finished_ones_and_private_ones_get_no_event(world):
    await world.games(
        game_json(1),
        game_json(2, start=planned(NOW + HOUR), finished_at=NOW - 10),
        game_json(3, start=planned(NOW + HOUR), visibility="participants"),
    )
    await world.events.sync()
    assert world.guild.created_events == []


async def test_a_server_that_did_not_turn_events_on_gets_none(world, bot):
    bot.guild_settings.update(GUILD, scheduled_events=False)
    await world.games(game_json(7, start=planned(NOW + HOUR)))
    await world.events.sync()
    assert world.guild.created_events == [] and world.rows() == {}


async def test_turning_events_off_removes_the_ones_the_bot_made(world, bot):
    await world.games(game_json(7, start=planned(NOW + HOUR)))
    await world.events.sync()
    [event] = world.guild.events.values()
    bot.guild_settings.update(GUILD, scheduled_events=False)
    await world.events.sync()
    assert event.deleted is True and world.rows() == {}


async def test_sign_ups_that_cannot_be_read_remove_nothing(world, manager):
    await world.games()
    world.signups(signup_json(3, deadline=NOW + HOUR))
    await world.events.sync()
    manager.add("GET", "/api/signups", (503, {"detail": "down"}))
    await world.events.sync()
    [event] = world.guild.events.values()
    assert not event.deleted and ("signup_deadline", 3) in world.rows()


async def test_a_server_that_refuses_events_does_not_stop_the_sync(world):
    async def refuse(**kwargs):
        raise forbidden()

    world.guild.create_scheduled_event = refuse
    await world.games(game_json(7, start=planned(NOW + HOUR)))
    await world.events.sync()  # logged, not raised
    assert world.rows() == {}


async def test_nothing_happens_before_the_games_are_loaded_or_discord_is_connected(world):
    await world.events.sync()
    assert world.guild.created_events == []
    await world.games(game_json(7, start=planned(NOW + HOUR)))
    world.discord._ready = False
    await world.events.sync()
    assert world.guild.created_events == []


async def test_an_event_discord_refuses_does_not_keep_the_others_of_the_server_from_syncing(world):
    real_create = world.guild.create_scheduled_event

    async def refuse_the_first_game(**kwargs):
        if kwargs["name"] == "Start: First":
            raise forbidden()
        return await real_create(**kwargs)

    world.guild.create_scheduled_event = refuse_the_first_game
    await world.games(
        game_json(1, "First", start=planned(NOW + HOUR)), game_json(2, "Second", start=planned(NOW + 2 * HOUR))
    )
    world.signups(signup_json(3, "Spring Async", deadline=NOW + HOUR))
    await world.events.sync()
    assert [event["name"] for event in world.guild.created_events] == ["Start: Second", "Sign-ups close: Spring Async"]
    assert set(world.rows()) == {("game_start", 2), ("signup_deadline", 3)}


async def test_a_refused_move_keeps_the_stored_time_so_it_is_tried_again_and_the_others_still_move(world):
    await world.games(
        game_json(1, "First", start=planned(NOW + HOUR)), game_json(2, "Second", start=planned(NOW + 2 * HOUR))
    )
    await world.events.sync()
    first, second = world.guild.events[min(world.guild.events)], world.guild.events[max(world.guild.events)]

    async def refuse(**kwargs):
        raise forbidden()

    first.edit = refuse
    await world.games(
        game_json(1, "First", start=planned(NOW + 4 * HOUR)), game_json(2, "Second", start=planned(NOW + 5 * HOUR))
    )
    await world.events.sync()
    assert second.edits and second.edits[0]["start_time"] == at(NOW + 5 * HOUR)
    assert world.rows()[("game_start", 1)].starts_at == NOW + HOUR  # not stored, so the next sync tries again
    assert world.rows()[("game_start", 2)].starts_at == NOW + 5 * HOUR


async def test_a_refused_delete_does_not_keep_other_events_from_being_deleted_or_created(world):
    await world.games(game_json(1, "First", start=planned(NOW + HOUR)), game_json(2, "Second", start=planned(NOW + 2 * HOUR)))
    await world.events.sync()
    first = world.guild.events[min(world.guild.events)]
    second = world.guild.events[max(world.guild.events)]

    async def refuse():
        raise forbidden()

    first.delete = refuse
    await world.games(game_json(3, "Third", start=planned(NOW + 3 * HOUR)))
    await world.events.sync()
    assert second.deleted is True and ("game_start", 2) not in world.rows()
    assert ("game_start", 1) in world.rows()  # kept, so the delete is tried again
    assert ("game_start", 3) in world.rows()
