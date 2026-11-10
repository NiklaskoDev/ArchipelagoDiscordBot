from types import SimpleNamespace

import discord
import pytest
from fakes import BOT_TOKEN, SITE_URL, FakeDiscordClient, FakeGuild, forbidden, json_text, signup_json

from apbot.db.reminders import ReminderStore
from apbot.manager.errors import ManagerUnavailable
from apbot.services.signup_reminders import SignupReminders

GUILD = 5000
CHANNEL = 70
NOW = 1_800_000_000.0
HOUR = 3600
DAY = 24 * HOUR


def server_error() -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "Server Error")


class World:
    def __init__(self, bot, manager):
        self.bot, self.manager = bot, manager
        self.now = NOW
        self.guild = FakeGuild(GUILD)
        self.channel = self.guild.add_channel(CHANNEL)
        self.discord = FakeDiscordClient(self.guild)
        bot.guild_settings.update(GUILD, signups_channel_id=CHANNEL)
        bot.cache.set_site_url(SITE_URL)
        self.reminders = SignupReminders(
            self.discord, bot._reminder_store, bot.guild_settings, bot.cache, manager.client, BOT_TOKEN,
            now=lambda: self.now,
        )
        self.signups(signup_json(3, "Spring Async", deadline=NOW + 20 * HOUR))

    def signups(self, *raw) -> None:
        self.manager.add("GET", "/api/signups", list(raw))

    def sent(self) -> list[tuple[float, int]]:
        rows = self.bot.db.query_all("SELECT deadline, before_seconds FROM signup_reminders ORDER BY before_seconds DESC")
        return [(row["deadline"], row["before_seconds"]) for row in rows]

    def posts(self) -> list:
        return self.channel.sent


@pytest.fixture
def world(bot, manager):
    return World(bot, manager)


# --- what is posted ----------------------------------------------------------------------------------------------------
async def test_a_round_closing_within_a_day_gets_a_reminder_in_the_signups_channel(world):
    await world.reminders.run()
    [post] = world.posts()
    embed = post.kwargs["embed"]
    assert embed.title == "Sign-ups for Spring Async close soon"
    assert f"<t:{int(NOW + 20 * HOUR)}:R>" in embed.description and f"<t:{int(NOW + 20 * HOUR)}:F>" in embed.description
    assert {field.name: field.value for field in embed.fields} == {"Entries": "5", "Players": "3", "Slots per player": "2"}
    assert post.kwargs["view"].children[0].url == f"{SITE_URL}/#/signups/3"
    assert world.sent() == [(NOW + 20 * HOUR, DAY)]


async def test_the_reminder_is_in_the_language_of_the_server(world, bot):
    bot.guild_settings.update(GUILD, language="de")
    await world.reminders.run()
    assert world.posts()[0].kwargs["embed"].title == "Anmeldung für Spring Async endet bald"


async def test_the_name_of_a_round_cannot_ping_or_format(world):
    world.signups(signup_json(3, "@everyone *hi*", deadline=NOW + HOUR))
    await world.reminders.run()
    text = json_text(world.posts()[0].kwargs["embed"])
    assert "@everyone" not in text and "\\\\*hi\\\\*" in text


async def test_without_a_link_to_the_manager_the_reminder_has_no_button(world, bot):
    bot.cache.set_site_url(None)
    await world.reminders.run()
    assert world.posts()[0].kwargs["view"] is None


async def test_nothing_is_posted_more_than_a_day_before_the_deadline(world):
    world.now = NOW - 10 * HOUR  # 30 hours to go
    await world.reminders.run()
    assert world.posts() == [] and world.sent() == []


# --- once per round, deadline and lead time ----------------------------------------------------------------------------
async def test_a_reminder_is_posted_once_per_lead_time(world):
    await world.reminders.run()
    await world.reminders.run()
    assert len(world.posts()) == 1
    world.now = NOW + 19 * HOUR + 30 * 60  # 30 minutes to go
    await world.reminders.run()
    await world.reminders.run()
    assert len(world.posts()) == 2
    assert world.sent() == [(NOW + 20 * HOUR, DAY), (NOW + 20 * HOUR, HOUR)]


async def test_when_both_are_due_at_once_only_the_urgent_one_is_posted_and_the_other_counts_as_done(world):
    world.now = NOW + 19 * HOUR + 30 * 60  # the bot was down, or the round was opened late
    await world.reminders.run()
    assert len(world.posts()) == 1
    assert world.sent() == [(NOW + 20 * HOUR, DAY), (NOW + 20 * HOUR, HOUR)]
    await world.reminders.run()
    assert len(world.posts()) == 1


async def test_a_deadline_that_moved_gets_its_reminders_again(world):
    await world.reminders.run()
    world.signups(signup_json(3, "Spring Async", deadline=NOW + 22 * HOUR))
    await world.reminders.run()
    assert len(world.posts()) == 2
    assert {deadline for deadline, _ in world.sent()} == {NOW + 20 * HOUR, NOW + 22 * HOUR}


async def test_each_server_gets_its_own_reminder(world, bot):
    other = FakeGuild(6000)
    other_channel = other.add_channel(80)
    world.discord.guilds.append(other)
    bot.guild_settings.update(6000, signups_channel_id=80)
    await world.reminders.run()
    assert len(world.posts()) == 1 and len(other_channel.sent) == 1
    await world.reminders.run()
    assert len(world.posts()) == 1 and len(other_channel.sent) == 1


# --- what is not reminded -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        signup_json(3, deadline=None),  # no deadline, nothing to remind of
        signup_json(3, deadline=NOW - 60),  # the deadline passed
        signup_json(3, deadline=NOW + HOUR, status="closed", open=False),  # closed already
    ],
    ids=["no deadline", "deadline passed", "closed"],
)
async def test_rounds_that_do_not_close_soon_get_no_reminder(world, raw):
    world.signups(raw)
    await world.reminders.run()
    assert world.posts() == [] and world.sent() == []


async def test_a_server_without_a_signups_channel_gets_nothing(world, bot):
    bot.guild_settings.update(GUILD, signups_channel_id=None)
    await world.reminders.run()
    assert world.posts() == [] and world.sent() == []


async def test_a_channel_the_bot_cannot_find_gets_nothing_and_nothing_is_marked(world):
    del world.guild.channels[CHANNEL]
    await world.reminders.run()
    assert world.sent() == []


async def test_nothing_happens_before_the_bot_is_connected(world):
    world.discord._ready = False
    await world.reminders.run()
    assert world.posts() == [] and world.manager.requests == []


# --- failures --------------------------------------------------------------------------------------------------------------
async def test_a_channel_the_bot_may_not_write_in_is_not_tried_again_and_again(world):
    world.channel.fail_with = forbidden()
    await world.reminders.run()
    world.channel.fail_with = None
    await world.reminders.run()
    assert world.posts() == []  # a late reminder after the permission was fixed would only confuse
    assert world.sent() == [(NOW + 20 * HOUR, DAY)]


async def test_a_temporary_discord_failure_is_tried_again_at_the_next_run(world):
    world.channel.fail_with = server_error()
    await world.reminders.run()
    assert world.sent() == []
    world.channel.fail_with = None
    await world.reminders.run()
    assert len(world.posts()) == 1 and len(world.sent()) == 1


async def test_one_server_that_refuses_does_not_stop_the_others(world, bot):
    other = FakeGuild(6000)
    other_channel = other.add_channel(80)
    world.discord.guilds.insert(0, other)
    bot.guild_settings.update(6000, signups_channel_id=80)
    other_channel.fail_with = forbidden()
    await world.reminders.run()
    assert len(world.posts()) == 1


async def test_when_the_manager_cannot_be_asked_nothing_is_posted_or_forgotten(world):
    await world.reminders.run()
    world.manager.add("GET", "/api/signups", ManagerUnavailable(None, "down"))
    await world.reminders.run()
    assert len(world.posts()) == 1 and len(world.sent()) == 1


# --- the store -------------------------------------------------------------------------------------------------------------
async def test_rounds_that_are_not_open_any_more_are_forgotten(world):
    await world.reminders.run()
    world.signups()  # no open round at all
    await world.reminders.run()
    assert world.sent() == []


def test_the_store_remembers_per_server_round_deadline_and_lead_time(bot):
    store = ReminderStore(bot.db, now=lambda: 5.0)
    store.mark_sent(1, 7, 100.0, 3600)
    store.mark_sent(1, 7, 100.0, 3600)  # twice is fine
    assert store.was_sent(1, 7, 100.0, 3600)
    assert not store.was_sent(2, 7, 100.0, 3600)
    assert not store.was_sent(1, 8, 100.0, 3600)
    assert not store.was_sent(1, 7, 101.0, 3600)
    assert not store.was_sent(1, 7, 100.0, 86400)


def test_forgetting_keeps_only_the_given_rounds_of_that_server(bot):
    store = ReminderStore(bot.db)
    for guild, signup in ((1, 7), (1, 8), (2, 7)):
        store.mark_sent(guild, signup, 100.0, 3600)
    store.forget_other_rounds(1, [8])
    assert not store.was_sent(1, 7, 100.0, 3600) and store.was_sent(1, 8, 100.0, 3600) and store.was_sent(2, 7, 100.0, 3600)
    store.forget_other_rounds(1, [])
    assert not store.was_sent(1, 8, 100.0, 3600) and store.was_sent(2, 7, 100.0, 3600)
