import asyncio

import discord
import pytest
from fakes import (
    BOT_TOKEN,
    SECRETS,
    USER_TOKEN,
    FakeDiscordClient,
    FakeGuild,
    FakeInteraction,
    forbidden,
    game_json,
    json_text,
)

from apbot.cogs.linking import LinkingCog
from apbot.db.live import DEFAULT_FEED_KINDS
from apbot.manager.views import FeedEntry
from apbot.services.feeds import FeedGame
from apbot.services.relay import MEMBER_RECHECK, Relay
from apbot.ui.format import ZERO_WIDTH_SPACE

GUILD, CHANNEL = 5000, 100


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class World:
    """One server with a games channel, a relay, and the manager's games."""

    def __init__(self, bot, manager, memberships=None):
        self.bot, self.manager = bot, manager
        self.clock = Clock()
        self.guild = FakeGuild(GUILD)
        self.channel = self.guild.add_channel(CHANNEL)
        self.discord = FakeDiscordClient(self.guild)
        self.memberships = memberships if memberships is not None else {}
        self.relay = Relay(
            self.discord, bot.spaces, bot.guild_settings, bot.cache, manager.client, BOT_TOKEN,
            lambda: self.memberships, now=self.clock,
        )
        bot.guild_settings.update(GUILD, games_channel_id=CHANNEL)

    async def games(self, *raw) -> None:
        self.manager.add("GET", "/api/games", list(raw))
        self.manager.add("GET", "/api/auth/status", {"user": {"id": 2}, "site_url": None})
        for game in raw:
            self.manager.add("GET", f"/api/games/{game['id']}", game)
        await self.bot.cache.refresh()

    @property
    def threads(self):
        return self.channel.threads


@pytest.fixture
def world(bot, manager):
    return World(bot, manager)


def feed_entry(kind: str, seq: int = 1, **fields) -> FeedEntry:
    return FeedEntry(seq=seq, at=0.0, kind=kind, fields=fields)


GAME = FeedGame(7, "Friday Run", "running", True)


# --- a thread and a role for each game ---------------------------------------------------------------------------------
async def test_a_new_public_game_gets_a_thread_a_role_and_a_first_message(world, bot):
    await world.games(game_json(7, "Friday Run"))
    await world.relay.maintain()
    [thread] = world.threads
    request = world.channel.thread_requests[0]
    assert request["name"] == "Friday Run" and request["type"] == discord.ChannelType.public_thread
    [role] = world.guild.roles.values()
    assert world.guild.created_roles[0]["name"] == "AP Friday Run"
    assert world.guild.created_roles[0]["permissions"] == discord.Permissions.none()
    assert world.guild.created_roles[0]["mentionable"] is False
    space = bot.spaces.get(GUILD, 7)
    assert (space.thread_id, space.role_id, space.kinds, space.closed_at) == (thread.id, role.id, DEFAULT_FEED_KINDS, None)
    [first] = thread.sent
    assert first.kwargs["embed"].title == "Friday Run"
    assert first.kwargs["allowed_mentions"].roles is False


async def test_the_first_message_has_the_games_facts_and_no_password(world):
    await world.games(game_json(7, "Friday Run"))
    await world.relay.maintain()
    text = json_text(world.threads[0].sent[0].kwargs["embed"])
    assert "ap.example:38281" in text
    assert not any(isinstance(secret, str) and secret in text for secret in SECRETS.values())
    assert "Timespinner" not in text  # the slot list stays in /game


async def test_the_first_message_follows_the_servers_language(world, bot):
    bot.guild_settings.update(GUILD, language="de")
    await world.games(game_json(7))
    await world.relay.maintain()
    assert "Adresse" in json_text(world.threads[0].sent[0].kwargs["embed"])


async def test_a_game_is_given_a_thread_only_once(world):
    await world.games(game_json(7))
    await world.relay.maintain()
    await world.relay.maintain()
    assert len(world.threads) == 1 and len(world.guild.created_roles) == 1


async def test_two_games_two_threads(world):
    await world.games(game_json(7, "One"), game_json(8, "Two"))
    await world.relay.maintain()
    assert [t.name for t in world.threads] == ["One", "Two"]


async def test_a_long_game_name_fits_discords_limits(world):
    await world.games(game_json(7, "x" * 300))
    await world.relay.maintain()
    assert len(world.channel.thread_requests[0]["name"]) == 100
    assert len(world.guild.created_roles[0]["name"]) == 100


async def test_a_private_game_never_gets_a_thread(world):
    await world.games(game_json(7, visibility="participants"))
    await world.relay.maintain()
    assert world.threads == [] and world.guild.created_roles == []


async def test_a_finished_game_without_a_thread_gets_none(world):
    await world.games(game_json(7, finished_at=1_700_000_500.0))
    await world.relay.maintain()
    assert world.threads == []


async def test_without_a_games_channel_nothing_is_made(world, bot):
    bot.guild_settings.update(GUILD, games_channel_id=None)
    await world.games(game_json(7))
    await world.relay.maintain()
    assert world.threads == []


async def test_a_games_channel_that_is_gone_makes_nothing_and_does_not_fail(world):
    world.guild.channels.clear()
    await world.games(game_json(7))
    await world.relay.maintain()
    assert world.guild.created_roles == []


async def test_nothing_happens_before_the_first_list_of_games(world):
    await world.relay.maintain()
    assert world.threads == []


async def test_without_a_game_detail_the_first_message_still_says_the_state(world, manager):
    await world.games(game_json(7, "Friday Run"))
    manager.add("GET", "/api/games/7", (404, {"detail": "gone"}))
    await world.relay.maintain()
    assert world.threads[0].sent[0].kwargs["embed"].title == "Friday Run"


async def test_a_thread_that_cannot_be_made_leaves_no_trace_and_is_tried_again(world, bot):
    await world.games(game_json(7))
    world.channel.fail_with = forbidden()
    await world.relay.maintain()  # logged, not raised
    assert bot.spaces.get(GUILD, 7) is None
    world.channel.fail_with = None
    await world.relay.maintain()
    assert bot.spaces.get(GUILD, 7) is not None


async def test_a_role_that_could_not_be_made_is_made_later(world, bot, monkeypatch):
    await world.games(game_json(7))
    original = world.guild.create_role

    async def refuse(**kwargs):
        raise forbidden()

    monkeypatch.setattr(world.guild, "create_role", refuse)
    await world.relay.maintain()
    assert bot.spaces.get(GUILD, 7).role_id is None and len(world.threads) == 1
    monkeypatch.setattr(world.guild, "create_role", original)
    await world.relay.maintain()
    assert bot.spaces.get(GUILD, 7).role_id is not None and len(world.threads) == 1


async def test_a_role_deleted_by_hand_is_made_again(world, bot):
    await world.games(game_json(7))
    await world.relay.maintain()
    del world.guild.roles[bot.spaces.get(GUILD, 7).role_id]
    await world.relay.maintain()
    assert bot.spaces.get(GUILD, 7).role_id in world.guild.roles


async def test_each_server_gets_its_own_thread_in_its_own_language(bot, manager):
    one, two = World(bot, manager), World(bot, manager)
    two.guild.id = 6000
    two.discord.guilds = [one.guild, two.guild]
    one.discord.guilds = [one.guild, two.guild]
    bot.guild_settings.update(6000, games_channel_id=CHANNEL, language="fr")
    await one.games(game_json(7))
    await one.relay.maintain()
    assert len(one.threads) == 1 and len(two.threads) == 1
    assert bot.spaces.get(6000, 7) is not None


# --- a game that is over -----------------------------------------------------------------------------------------------
async def test_a_finished_game_gets_a_summary_then_its_thread_is_archived_and_locked_and_its_role_deleted(world, bot):
    await world.games(game_json(7, "Friday Run"))
    await world.relay.maintain()
    thread, [role] = world.threads[0], list(world.guild.roles.values())
    await world.games(game_json(7, "Friday Run", finished_at=1_700_000_500.0, outcome="finished"))
    await world.relay.maintain()
    summary = thread.sent[-1].kwargs["embeds"]
    assert "Finished" in json_text(summary) and "Timespinner" in json_text(summary)  # the full game, slots included
    assert (thread.archived, thread.locked) == (True, True)
    assert role.deleted is True
    space = bot.spaces.get(GUILD, 7)
    assert space.closed_at is not None and space.role_id is None
    assert bot.spaces.open_for_guild(GUILD) == []


async def test_a_closed_game_is_not_closed_again_or_given_a_new_thread(world):
    await world.games(game_json(7))
    await world.relay.maintain()
    await world.games(game_json(7, finished_at=1_700_000_500.0))
    await world.relay.maintain()
    thread = world.threads[0]
    posts = len(thread.sent)
    await world.relay.maintain()
    assert len(thread.sent) == posts and len(world.threads) == 1


async def test_a_game_that_vanished_from_the_manager_is_closed_without_a_summary(world, bot, manager):
    await world.games(game_json(7))
    await world.relay.maintain()
    thread = world.threads[0]
    await world.games()  # the list is empty now
    manager.add("GET", "/api/games/7", (404, {"detail": "gone"}))
    await world.relay.maintain()
    assert len(thread.sent) == 1 and thread.archived is True  # only the first message
    assert bot.spaces.get(GUILD, 7).closed_at is not None


async def test_a_thread_that_cannot_be_archived_is_still_closed_in_the_database(world, bot):
    await world.games(game_json(7))
    await world.relay.maintain()
    world.threads[0].fail_with = forbidden()
    await world.games(game_json(7, finished_at=1_700_000_500.0))
    await world.relay.maintain()
    assert bot.spaces.get(GUILD, 7).closed_at is not None


# --- game online -------------------------------------------------------------------------------------------------------
async def test_a_game_coming_online_pings_its_role_in_its_thread(world, bot):
    await world.games(game_json(7, "Friday Run", state="stopped"))
    await world.relay.maintain()
    thread, role = world.threads[0], bot.spaces.get(GUILD, 7).role_id
    await world.games(game_json(7, "Friday Run", state="running"))
    await world.relay.maintain()
    online = thread.sent[-1]
    assert online.content.startswith(f"<@&{role}> **Friday Run** is online")
    assert "ap.example:38281" in online.content
    assert online.kwargs["allowed_mentions"].roles == [world.guild.roles[role]]
    assert online.kwargs["allowed_mentions"].everyone is False and online.kwargs["allowed_mentions"].users is False
    assert world.guild.roles[role].mentionable is False
    assert [edit["mentionable"] for edit in world.guild.roles[role].edits] == [True, False]


async def test_the_online_post_carries_no_password(world):
    await world.games(game_json(7, state="stopped"))
    await world.relay.maintain()
    await world.games(game_json(7, state="running"))
    await world.relay.maintain()
    assert not any(secret in world.threads[0].sent[-1].content for secret in SECRETS.values() if isinstance(secret, str))


async def test_a_game_that_is_online_when_the_bot_starts_is_not_announced(world):
    await world.games(game_json(7, state="running"))
    await world.relay.maintain()
    await world.relay.maintain()
    assert len(world.threads[0].sent) == 1  # only the first message


async def test_a_game_is_announced_once_per_start(world):
    await world.games(game_json(7, state="stopped"))
    await world.relay.maintain()
    await world.games(game_json(7, state="running"))
    await world.relay.maintain()
    await world.relay.maintain()
    assert len(world.threads[0].sent) == 2
    await world.games(game_json(7, state="stopped"))
    await world.relay.maintain()
    await world.games(game_json(7, state="running"))
    await world.relay.maintain()
    assert len(world.threads[0].sent) == 3


async def test_the_role_is_not_left_mentionable_when_the_post_fails(world, bot):
    await world.games(game_json(7, state="stopped"))
    await world.relay.maintain()
    world.threads[0].fail_with = forbidden()
    await world.games(game_json(7, state="running"))
    await world.relay.maintain()
    assert world.guild.roles[bot.spaces.get(GUILD, 7).role_id].mentionable is False


# --- the feed in the thread --------------------------------------------------------------------------------------------
async def prepared(world, bot, **settings):
    await world.games(game_json(7, "Friday Run"))
    await world.relay.maintain()
    if settings:
        bot.guild_settings.update(GUILD, **settings)
    return world.threads[0], len(world.threads[0].sent)


async def test_default_kinds_are_posted_and_items_joins_and_leaves_are_not(world, bot):
    thread, before = await prepared(world, bot)
    entries = tuple(
        feed_entry(kind, seq, player="Alice", text="hi", game="Timespinner", finder="B", item="I", receiver="R", location="L")
        for seq, kind in enumerate(["goal", "item", "join", "part", "chat"], start=1)
    )
    await world.relay.publish(GAME, entries)
    [message] = thread.sent[before:]
    assert message.content.split("\n") == ["**Alice** reached their goal", "**Alice**: hi"]


async def test_the_servers_choice_of_kinds_decides(world, bot):
    thread, before = await prepared(world, bot)
    bot.spaces.set_kinds(GUILD, 7, ["item"])
    await world.relay.publish(GAME, (feed_entry("goal", player="A"), feed_entry("item", 2, finder="B", item="I", receiver="R", location="L")))
    assert [m.content for m in thread.sent[before:]] == ["**B** sent I to **R** (L)"]
    bot.spaces.set_kinds(GUILD, 7, [])
    await world.relay.publish(GAME, (feed_entry("goal", player="A"),))
    assert len(thread.sent) == before + 1


async def test_a_batch_is_posted_as_few_messages_as_possible_and_never_more_than_five(world, bot):
    thread, before = await prepared(world, bot)
    long_chat = tuple(feed_entry("chat", seq, player="A", text="x" * 250) for seq in range(1, 200))
    await world.relay.publish(GAME, long_chat)
    messages = [m.content for m in thread.sent[before:]]
    assert len(messages) == 5 and all(len(m) <= 2000 for m in messages)
    assert messages[-1].split("\n")[-1].startswith("…and ") and messages[-1].endswith(" more events")


async def test_chat_cannot_ping_anyone(world, bot):
    thread, before = await prepared(world, bot)
    await world.relay.publish(GAME, (feed_entry("chat", player="@everyone", text="@everyone <@123> <@&456> hi"),))
    [message] = thread.sent[before:]
    assert "@everyone" not in message.content.replace(f"@{ZERO_WIDTH_SPACE}everyone", "")
    assert "<@123>" not in message.content and "<@&456>" not in message.content
    allowed = message.kwargs["allowed_mentions"]
    assert (allowed.everyone, allowed.users, allowed.roles) == (False, False, False)


async def test_the_posts_follow_the_servers_language(world, bot):
    thread, before = await prepared(world, bot, language="de")
    await world.relay.publish(GAME, (feed_entry("goal", player="Alice"),))
    assert thread.sent[before].content == "**Alice** hat das Ziel erreicht"


async def test_a_game_without_a_thread_posts_nothing(world):
    await world.relay.publish(GAME, (feed_entry("goal", player="A"),))
    assert world.threads == []


async def test_a_closed_space_gets_no_more_posts(world, bot):
    thread, before = await prepared(world, bot)
    bot.spaces.close(GUILD, 7)
    await world.relay.publish(GAME, (feed_entry("goal", player="A"),))
    assert len(thread.sent) == before


async def test_a_thread_that_folded_away_is_opened_again_before_posting(world, bot):
    thread, before = await prepared(world, bot)
    thread.archived = True
    await world.relay.publish(GAME, (feed_entry("goal", player="A"),))
    assert thread.edits[-1] == {"archived": False} and len(thread.sent) == before + 1


async def test_a_thread_that_refuses_posts_is_logged_and_does_not_raise(world, bot, caplog):
    thread, before = await prepared(world, bot)
    thread.fail_with = forbidden()
    await world.relay.publish(GAME, (feed_entry("goal", player="A"),))
    assert len(thread.sent) == before


# --- roles -------------------------------------------------------------------------------------------------------------
async def ready(world, bot):
    """A thread and a role for game 7, and a member (id 1001) in the server."""
    await world.games(game_json(7))
    await world.relay.maintain()
    role_id = bot.spaces.get(GUILD, 7).role_id
    return world.guild.add_member(1001), role_id


async def test_a_player_gets_the_role_of_their_game(world, bot):
    member, role_id = await ready(world, bot)
    await world.relay.sync_roles(1001, frozenset({7}))
    assert member.added == [role_id] and member.get_role(role_id)


async def test_a_person_who_stops_playing_loses_the_role_and_other_roles_stay(world, bot):
    member, role_id = await ready(world, bot)
    member._roles.add(999)  # a role of the server's own
    await world.relay.sync_roles(1001, frozenset({7}))
    await world.relay.sync_roles(1001, frozenset())
    assert member.removed == [role_id] and member.get_role(999) and not member.get_role(role_id)


async def test_a_role_is_not_given_for_a_game_the_person_does_not_play_in(world, bot):
    member, role_id = await ready(world, bot)
    await world.relay.sync_roles(1001, frozenset({8}))
    assert member.added == []


async def test_nothing_is_asked_of_discord_while_nothing_changes(world, bot, monkeypatch):
    member, role_id = await ready(world, bot)
    fetched = []
    original = world.guild.fetch_member

    async def counting(user_id):
        fetched.append(user_id)
        return await original(user_id)

    monkeypatch.setattr(world.guild, "fetch_member", counting)
    for _ in range(3):
        await world.relay.sync_roles(1001, frozenset({7}))
    assert fetched == [1001] and member.added == [role_id]


async def test_someone_who_is_not_in_the_server_is_skipped_and_looked_for_again_later(world, bot):
    await world.games(game_json(7))
    await world.relay.maintain()
    await world.relay.sync_roles(1001, frozenset({7}))  # not a member: no error
    member = world.guild.add_member(1001)
    await world.relay.sync_roles(1001, frozenset({7}))
    assert member.added == []  # not looked for yet
    world.clock.now += MEMBER_RECHECK + 1
    await world.relay.sync_roles(1001, frozenset({7}))
    assert member.added == [bot.spaces.get(GUILD, 7).role_id]


async def test_a_new_game_role_reaches_the_people_who_already_play(world, bot):
    member, role_id = await ready(world, bot)
    world.memberships.update({1001: frozenset({7, 8})})
    await world.relay.sync_roles(1001, frozenset({7, 8}))
    await world.games(game_json(7), game_json(8, "Second"))
    await world.relay.maintain()  # makes game 8's role and gives it to the people who play in it
    assert bot.spaces.get(GUILD, 8).role_id in member.added


async def test_a_game_that_ends_removes_its_role_from_the_sync_set(world, bot):
    member, role_id = await ready(world, bot)
    world.memberships.update({1001: frozenset({7})})
    await world.relay.sync_roles(1001, frozenset({7}))
    await world.games(game_json(7, finished_at=1_700_000_500.0))
    await world.relay.maintain()
    assert world.guild.roles[role_id].deleted is True
    assert bot.spaces.get(GUILD, 7).role_id is None


async def test_a_member_whose_roles_cannot_be_changed_does_not_stop_the_sync(world, bot, caplog):
    member, role_id = await ready(world, bot)

    async def refuse(*roles, reason=None):
        raise forbidden()

    member.add_roles = refuse
    await world.relay.sync_roles(1001, frozenset({7}))  # logged, not raised


async def test_roles_are_given_in_every_server_the_person_is_in(bot, manager):
    one, two = World(bot, manager), World(bot, manager)
    two.guild.id = 6000
    two.guild.channels[CHANNEL] = two.channel
    one.discord.guilds = two.discord.guilds = [one.guild, two.guild]
    bot.guild_settings.update(6000, games_channel_id=CHANNEL)
    await one.games(game_json(7))
    await one.relay.maintain()
    a, b = one.guild.add_member(1001), two.guild.add_member(1001)
    await one.relay.sync_roles(1001, frozenset({7}))
    assert a.added == [bot.spaces.get(GUILD, 7).role_id] and b.added == [bot.spaces.get(6000, 7).role_id]


# --- unlinking ---------------------------------------------------------------------------------------------------------
async def test_unlinking_removes_the_person_from_the_game_roles_in_every_server(world, bot, manager):
    member, role_id = await ready(world, bot)
    bot.links.save(1001, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
    bot.relay = world.relay  # the bot's own relay has no servers in a test
    await world.relay.sync_roles(1001, frozenset({7}))
    assert member.get_role(role_id)
    interaction = FakeInteraction(bot, user_id=1001)
    task = asyncio.create_task(LinkingCog(bot).unlink.callback(LinkingCog(bot), interaction))
    for _ in range(10):
        await asyncio.sleep(0)
    await interaction.messages[0]["view"].children[0].callback(FakeInteraction(bot, user_id=1001))
    await task
    assert bot.links.get(1001) is None
    assert member.removed == [role_id] and not member.get_role(role_id)


# --- one refusal does not stop the others ------------------------------------------------------------------------------
async def test_a_role_that_cannot_be_made_for_one_game_does_not_stop_the_next_game_getting_its_space(world, bot, monkeypatch):
    original = world.guild.create_role

    async def refuse_for_bad(**kwargs):
        if kwargs["name"] == "AP Bad":
            raise forbidden()
        return await original(**kwargs)

    monkeypatch.setattr(world.guild, "create_role", refuse_for_bad)
    await world.games(game_json(7, "Bad"), game_json(8, "Good"))
    await world.relay.maintain()
    assert bot.spaces.get(GUILD, 8).role_id is not None and len(world.threads) == 2
    assert bot.spaces.get(GUILD, 7).role_id is None  # made later, when permitted


async def test_a_thread_that_cannot_be_made_for_one_game_does_not_stop_the_next_game(world, bot, monkeypatch):
    original = world.channel.create_thread

    async def refuse_for_bad(**kwargs):
        if kwargs["name"] == "Bad":
            raise forbidden()
        return await original(**kwargs)

    monkeypatch.setattr(world.channel, "create_thread", refuse_for_bad)
    await world.games(game_json(7, "Bad"), game_json(8, "Good"))
    await world.relay.maintain()
    assert bot.spaces.get(GUILD, 7) is None and bot.spaces.get(GUILD, 8).role_id is not None


async def test_a_game_that_cannot_be_closed_does_not_stop_the_others_being_closed_or_announced(world, bot):
    await world.games(game_json(7, "One", state="stopped"), game_json(8, "Two"), game_json(9, "Three", state="stopped"))
    await world.relay.maintain()
    one = next(t for t in world.threads if t.name == "One")
    two = next(t for t in world.threads if t.name == "Two")
    three = next(t for t in world.threads if t.name == "Three")
    one.fail_with = forbidden()
    await world.games(
        game_json(7, "One", finished_at=1_700_000_500.0), game_json(8, "Two", finished_at=1_700_000_500.0),
        game_json(9, "Three", state="running"),
    )
    await world.relay.maintain()
    assert bot.spaces.get(GUILD, 8).closed_at is not None and two.archived is True
    assert three.sent[-1].content.startswith("<@&") and "**Three** is online" in three.sent[-1].content


# --- roles that cannot be changed, and people who come back ------------------------------------------------------------
async def test_roles_that_cannot_be_given_are_not_tried_and_logged_again_for_every_cycle(world, bot, caplog):
    member, role_id = await ready(world, bot)
    calls = []

    async def refuse(*roles, reason=None):
        calls.append(1)
        raise forbidden()

    member.add_roles = refuse
    for _ in range(4):
        await world.relay.sync_roles(1001, frozenset({7}))
    assert len(calls) == 1
    assert len([r for r in caplog.records if "game roles" in r.getMessage()]) == 1
    world.clock.now += MEMBER_RECHECK + 1
    await world.relay.sync_roles(1001, frozenset({7}))
    assert len(calls) == 2


async def test_a_change_of_games_is_tried_at_once_even_after_a_refusal(world, bot):
    member, role_id = await ready(world, bot)
    calls = []

    async def refuse(*roles, reason=None):
        calls.append(1)
        raise forbidden()

    member.add_roles = refuse
    await world.relay.sync_roles(1001, frozenset({7}))
    await world.relay.sync_roles(1001, frozenset())
    await world.relay.sync_roles(1001, frozenset({7}))
    assert len(calls) == 2


async def test_someone_who_left_and_came_back_gets_the_role_again(world, bot):
    member, role_id = await ready(world, bot)
    await world.relay.sync_roles(1001, frozenset({7}))
    assert member.get_role(role_id)
    returned = world.guild.add_member(1001)  # left, and joined again: no roles
    await world.relay.sync_roles(1001, frozenset({7}))
    assert returned.added == []  # not looked at yet
    world.clock.now += MEMBER_RECHECK + 1
    await world.relay.sync_roles(1001, frozenset({7}))
    assert returned.added == [role_id]
