import discord
import pytest
from fakes import BOT_TOKEN, SECRETS, FakeDiscordClient, FakeGuild, FakeMessage, forbidden, game_json, json_text, signup_json

from apbot.manager.views import parse_games, parse_signups
from apbot.services.overview import VERIFY_INTERVAL, Overview
from apbot.ui.embeds import overview_embed

GUILD, CHANNEL = 5000, 300


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class World:
    def __init__(self, bot, manager):
        self.bot, self.manager = bot, manager
        self.clock = Clock()
        self.guild = FakeGuild(GUILD)
        self.channel = self.guild.add_channel(CHANNEL)
        self.discord = FakeDiscordClient(self.guild)
        self.overview = Overview(
            self.discord, bot._overview_store, bot.guild_settings, bot.cache, manager.client, BOT_TOKEN, now=self.clock
        )
        bot.guild_settings.update(GUILD, overview_channel_id=CHANNEL)
        self.signups(signup_json(1, "Spring Async"))

    async def games(self, *raw) -> None:
        self.manager.add("GET", "/api/games", list(raw))
        self.manager.add("GET", "/api/auth/status", {"user": {"id": 2}, "site_url": None})
        await self.bot.cache.refresh()

    def signups(self, *raw) -> None:
        self.manager.add("GET", "/api/signups", list(raw))

    @property
    def posts(self) -> list[FakeMessage]:
        return self.channel.sent


@pytest.fixture
def world(bot, manager):
    return World(bot, manager)


async def test_the_overview_is_posted_and_pinned_with_the_games_and_open_signups(world, bot):
    await world.games(game_json(1, "Friday Run"), game_json(2, "Old Run", finished_at=1_700_000_500.0))
    await world.overview.run()
    [post] = world.posts
    text = json_text(post.kwargs["embed"])
    assert "Friday Run" in text and "Spring Async" in text and "Old Run" not in text
    assert post.pinned is True
    stored = bot._overview_store.get(GUILD)
    assert (stored.channel_id, stored.message_id) == (CHANNEL, post.id)


async def test_private_games_and_closed_rounds_are_not_on_the_overview(world):
    await world.games(game_json(1, "Public Run"), game_json(2, "Hidden Run", visibility="participants"))
    world.signups(signup_json(1, "Open Round"), signup_json(2, "Closed Round", open=False, status="closed"))
    await world.overview.run()
    text = json_text(world.posts[0].kwargs["embed"])
    assert "Public Run" in text and "Open Round" in text
    assert "Hidden Run" not in text and "Closed Round" not in text


async def test_the_overview_never_shows_a_secret(world):
    await world.games(game_json(1))
    await world.overview.run()
    text = json_text(world.posts[0].kwargs["embed"])
    assert not any(isinstance(secret, str) and secret in text for secret in SECRETS.values())


async def test_the_overview_follows_the_servers_language(world, bot):
    bot.guild_settings.update(GUILD, language="de")
    await world.games(game_json(1))
    await world.overview.run()
    assert world.posts[0].kwargs["embed"].title == "Übersicht"


async def test_unchanged_content_is_neither_edited_nor_posted_again(world):
    await world.games(game_json(1))
    await world.overview.run()
    await world.overview.run()
    await world.overview.run()
    [post] = world.posts
    assert post.edits == []


async def test_changed_content_edits_the_same_message(world, bot):
    await world.games(game_json(1, state="stopped"))
    await world.overview.run()
    await world.games(game_json(1, state="running"))
    await world.overview.run()
    [post] = world.posts
    assert len(post.edits) == 1 and "Running" in json_text(post.edits[0]["embed"])
    assert bot._overview_store.get(GUILD).message_id == post.id
    await world.overview.run()
    assert len(post.edits) == 1  # and now it is unchanged again


async def test_a_message_deleted_by_hand_is_posted_and_pinned_again_when_the_content_changes(world, bot):
    await world.games(game_json(1, state="stopped"))
    await world.overview.run()
    [first] = world.posts
    del world.channel.messages[first.id]
    await world.games(game_json(1, state="running"))
    await world.overview.run()
    [second] = world.posts
    assert second.id != first.id and second.pinned is True
    assert bot._overview_store.get(GUILD).message_id == second.id


async def test_a_message_deleted_by_hand_is_posted_again_even_when_the_content_is_unchanged(world, bot):
    await world.games(game_json(1))
    await world.overview.run()
    [first] = world.posts
    del world.channel.messages[first.id]
    await world.overview.run()
    assert world.posts == []  # not noticed yet: nothing is asked of Discord while the content stands
    world.clock.now += VERIFY_INTERVAL + 1
    await world.overview.run()
    [second] = world.posts
    assert second.pinned is True and bot._overview_store.get(GUILD).message_id == second.id


async def test_a_message_that_is_still_there_is_checked_now_and_then_and_left_alone(world):
    await world.games(game_json(1))
    await world.overview.run()
    [post] = world.posts
    world.clock.now += VERIFY_INTERVAL + 1
    await world.overview.run()
    assert world.posts == [post] and post.edits == []


async def test_a_new_overview_channel_gets_a_new_message_and_the_old_one_is_deleted(world, bot):
    await world.games(game_json(1))
    await world.overview.run()
    [old] = world.posts
    new_channel = world.guild.add_channel(301)
    bot.guild_settings.update(GUILD, overview_channel_id=301)
    await world.overview.run()
    assert old.deleted is True and len(new_channel.sent) == 1 and new_channel.sent[0].pinned is True
    assert bot._overview_store.get(GUILD).channel_id == 301


async def test_no_overview_channel_means_no_overview(world, bot):
    bot.guild_settings.update(GUILD, overview_channel_id=None)
    await world.games(game_json(1))
    await world.overview.run()
    assert world.posts == []


async def test_a_channel_that_is_gone_is_skipped(world):
    world.guild.channels.clear()
    await world.games(game_json(1))
    await world.overview.run()  # nothing to post into, no error


async def test_a_message_that_cannot_be_pinned_is_still_kept(world, bot, monkeypatch):
    async def refuse(self):
        raise forbidden()

    monkeypatch.setattr(FakeMessage, "pin", refuse)
    await world.games(game_json(1))
    await world.overview.run()
    assert len(world.posts) == 1 and bot._overview_store.get(GUILD) is not None


async def test_a_channel_that_refuses_posts_does_not_stop_the_run(world):
    world.channel.fail_with = forbidden()
    await world.games(game_json(1))
    await world.overview.run()


async def test_sign_ups_that_cannot_be_read_leave_the_overview_as_it_is(world, manager):
    await world.games(game_json(1))
    await world.overview.run()
    manager.add("GET", "/api/signups", (503, {"detail": "down"}))
    await world.games(game_json(1, state="stopped"))
    await world.overview.run()
    [post] = world.posts
    assert post.edits == []


async def test_nothing_happens_before_the_games_are_loaded_or_discord_is_connected(world):
    await world.overview.run()
    assert world.posts == [] and world.discord.presence == []
    await world.games(game_json(1))
    world.discord._ready = False
    await world.overview.run()
    assert world.posts == [] and world.discord.presence == []


# --- presence ----------------------------------------------------------------------------------------------------------
async def test_the_presence_says_how_many_games_are_online_and_changes_only_when_it_changes(world):
    await world.games(game_json(1, state="running"), game_json(2, state="running"), game_json(3, state="stopped"))
    await world.overview.run()
    await world.overview.run()
    [activity] = world.discord.presence
    assert (activity.type, activity.name) == (discord.ActivityType.watching, "2 games online")
    await world.games(game_json(1, state="running"), game_json(2, state="stopped"))
    await world.overview.run()
    assert world.discord.presence[-1].name == "1 game online" and len(world.discord.presence) == 2
    await world.games()
    await world.overview.run()
    assert world.discord.presence[-1].name == "0 games online"


async def test_private_games_are_not_counted_in_the_presence(world):
    await world.games(game_json(1, state="running", visibility="participants"))
    await world.overview.run()
    assert world.discord.presence[0].name == "0 games online"


# --- the embed ---------------------------------------------------------------------------------------------------------
def test_an_empty_overview_says_so_in_both_sections():
    embed = overview_embed([], [], "en")
    assert [f.name for f in embed.fields] == ["Games", "Sign-ups"]
    assert "No games are running" in embed.fields[0].value and "No sign-up rounds are open" in embed.fields[1].value


def test_a_long_overview_is_cut_inside_discords_limits_and_says_how_many_were_left_out():
    games = parse_games([game_json(i, f"Game number {i}") for i in range(1, 60)])
    signups = parse_signups([signup_json(i, f"Round number {i}") for i in range(1, 60)])
    embed = overview_embed(games, signups, "en")
    assert len(embed) <= 6000 and all(len(f.value) <= 1024 for f in embed.fields)
    assert all("more" in f.value.split("\n")[-1] for f in embed.fields)


def test_the_same_content_gives_the_same_embed():
    games = parse_games([game_json(1)])
    assert overview_embed(games, [], "en").to_dict() == overview_embed(games, [], "en").to_dict()
