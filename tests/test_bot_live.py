import asyncio
from types import SimpleNamespace

import pytest
from fakes import (
    BOT_TOKEN,
    USER_TOKEN,
    FakeDirectory,
    FakeDiscordClient,
    FakeGuild,
    FakeInteraction,
    activity_json,
    by_token,
    entry,
    friend_json,
    friends_json,
    game_json,
    hint_json,
    tracker_json,
)

from apbot.access import Access
from apbot.cogs.games import GamesCog
from apbot.cogs.linking import LinkingCog
from apbot.manager.views import FeedEntry
from apbot.services.feeds import FeedGame
from apbot.ui.dynamic import FriendAnswerButton, HandoverAcceptButton, HandoverDeclineButton

GUILD, CHANNEL = 5000, 100
MINE = [{"slot": 1, "name": "Alice", "game": "Timespinner", "idle_since": None}]
HEALTH = {"ok": True, "version": "1.3.0", "archipelago": "0.6.8", "ready": True, "archipelago_error": None}


def deleted_role(role_id: int, guild_id: int = GUILD):
    return SimpleNamespace(id=role_id, guild=SimpleNamespace(id=guild_id))


# --- fix: a deleted organizer role no longer locks everyone out ---------------------------------------------------------
async def test_deleting_the_organizer_role_in_discord_clears_the_setting(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=777, admin_role_id=888)
    await bot.on_guild_role_delete(deleted_role(777))
    settings = bot.guild_settings.get(GUILD)
    assert settings.organizer_role_id is None and settings.admin_role_id == 888


async def test_deleting_the_admin_role_in_discord_clears_the_setting(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=777, admin_role_id=888)
    await bot.on_guild_role_delete(deleted_role(888))
    settings = bot.guild_settings.get(GUILD)
    assert settings.admin_role_id is None and settings.organizer_role_id == 777


async def test_a_role_that_is_both_organizer_and_admin_clears_both(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=777, admin_role_id=777)
    await bot.on_guild_role_delete(deleted_role(777))
    settings = bot.guild_settings.get(GUILD)
    assert (settings.organizer_role_id, settings.admin_role_id) == (None, None)


async def test_deleting_some_other_role_changes_nothing(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=777, admin_role_id=888, language="de")
    await bot.on_guild_role_delete(deleted_role(999))
    settings = bot.guild_settings.get(GUILD)
    assert (settings.organizer_role_id, settings.admin_role_id, settings.language) == (777, 888, "de")


async def test_a_role_deleted_in_another_server_changes_nothing_here(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=777)
    bot.guild_settings.update(6000, organizer_role_id=777)
    await bot.on_guild_role_delete(deleted_role(777, guild_id=6000))
    assert bot.guild_settings.get(GUILD).organizer_role_id == 777
    assert bot.guild_settings.get(6000).organizer_role_id is None


async def test_deleting_a_role_in_a_server_with_no_settings_stores_nothing(bot):
    await bot.on_guild_role_delete(deleted_role(777, guild_id=7000))
    assert bot.db.query_all("SELECT * FROM guild_settings") == []


async def test_after_the_organizer_role_is_deleted_organizers_commands_work_again(bot, manager):
    bot.guild_settings.update(GUILD, organizer_role_id=777)
    bot.links.save(1001, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
    manager.add("POST", "/api/games/7/start", game_json(7))
    cog = GamesCog(bot)
    with pytest.raises(Exception, match="role"):
        await cog.start.callback(cog, FakeInteraction(bot, user_id=1001, guild_id=GUILD, roles=[1]), "7")
    await bot.on_guild_role_delete(deleted_role(777))
    await cog.start.callback(cog, FakeInteraction(bot, user_id=1001, guild_id=GUILD, roles=[1]), "7")
    assert len(manager.requests) == 1


# --- fix: a command resolves the person's token once --------------------------------------------------------------------
class Counting:
    """Counts what resolving a person's token does: decrypting, rotating and remembering the language."""

    def __init__(self, bot, monkeypatch):
        self.decrypts = self.rotations = self.locale_writes = self.resolutions = 0
        cipher, links, access = bot.cipher, bot.links, bot.access
        decrypt, replace, set_locale = cipher.decrypt, links.replace_ciphertext, links.set_locale
        resolve = access.stored_token

        def counting_resolve(link):
            self.resolutions += 1
            return resolve(link)

        def counting_decrypt(data):
            self.decrypts += 1
            return decrypt(data)

        def counting_replace(*args):
            self.rotations += 1
            return replace(*args)

        def counting_set_locale(*args):
            self.locale_writes += 1
            return set_locale(*args)

        monkeypatch.setattr(access, "stored_token", counting_resolve)
        monkeypatch.setattr(cipher, "decrypt", counting_decrypt)
        monkeypatch.setattr(links, "replace_ciphertext", counting_replace)
        monkeypatch.setattr(links, "set_locale", counting_set_locale)


@pytest.mark.parametrize("command", ["wake", "start", "stop", "restart"])
async def test_a_command_resolves_the_token_once_and_writes_the_language_once(bot, manager, monkeypatch, command):
    bot.links.save(1001, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
    manager.add("POST", f"/api/games/7/{command}", game_json(7))
    counts = Counting(bot, monkeypatch)
    cog = GamesCog(bot)
    await getattr(cog, command).callback(cog, FakeInteraction(bot, user_id=1001, guild_id=GUILD, locale="de"), "7")
    assert (counts.resolutions, counts.decrypts, counts.locale_writes) == (1, 1, 1)
    assert bot.links.get(1001).locale == "de"


async def test_a_token_stored_under_an_old_key_is_stored_again_once_per_command(tmp_path, manager, monkeypatch):
    from cryptography.fernet import Fernet
    from fakes import make_bot

    from apbot.crypto import TokenCipher

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    bot = make_bot(tmp_path, manager)
    try:
        bot.cipher = TokenCipher([new, old])
        bot.access = Access(bot.links, bot.guild_settings, bot.cipher)
        bot.links.save(1001, 10, "alice", TokenCipher([old]).encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
        manager.add("POST", "/api/games/7/wake", game_json(7))
        counts = Counting(bot, monkeypatch)
        cog = GamesCog(bot)
        await cog.wake.callback(cog, FakeInteraction(bot, user_id=1001, guild_id=GUILD), "7")
        assert (counts.resolutions, counts.rotations) == (1, 1)
        assert bot.cipher.decrypt(bot.links.get(1001).token_ciphertext) == USER_TOKEN
        assert not bot.cipher.needs_rotation(bot.links.get(1001).token_ciphertext)  # now under the newest key
        assert manager.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"
    finally:
        bot.db.close()


# --- how the live parts are wired --------------------------------------------------------------------------------------
async def test_setup_starts_the_live_tasks_registers_the_buttons_and_adds_the_alerts_command(bot, manager):
    async def sync(*args, **kwargs):
        return []

    bot.tree.sync = sync
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", {"user": {"id": 2, "username": "bot", "role": "user"}, "site_url": None})
    await bot.setup_hook()
    try:
        names = {task.name for task in bot._periodic}
        assert {"game-cache", "heartbeat", "user-sync", "feeds", "relay", "overview", "events", "signup-reminders"} <= names
        registered = set(bot._connection._view_store._dynamic_items)
        assert {HandoverAcceptButton.__discord_ui_compiled_template__, HandoverDeclineButton.__discord_ui_compiled_template__} <= registered
        assert FriendAnswerButton.__discord_ui_compiled_template__ in registered
        assert {"friends", "friend"} <= {command.name for command in bot.tree.get_commands()}
        assert "alerts" in {command.name for command in bot.tree.get_commands()}
    finally:
        await bot.close()


class Live:
    """The bot's services wired together, with one fake server and direct messages instead of Discord."""

    def __init__(self, bot, manager):
        self.bot, self.manager = bot, manager
        self.guild = FakeGuild(GUILD)
        self.channel = self.guild.add_channel(CHANNEL)
        self.member = self.guild.add_member(1001)
        self.directory = FakeDirectory()
        client = FakeDiscordClient(self.guild)
        bot.relay._client = client
        bot.alerts._fetch_user = self.directory
        bot.guild_settings.update(GUILD, games_channel_id=CHANNEL)
        bot.links.save(1001, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
        manager.add("GET", "/api/auth/status", {"user": {"id": 2}, "site_url": None})

    def games(self, state="running", my_slots=MINE):
        public = game_json(7, "Friday Run", state=state)
        mine = game_json(7, "Friday Run", state=state, my_slots=my_slots)
        self.manager.add("GET", "/api/games", by_token({BOT_TOKEN: [public], USER_TOKEN: [mine]}))
        self.manager.add("GET", "/api/games/7", public)

    @property
    def dms(self):
        user = self.directory.users.get(1001)
        return user.sent if user else []


@pytest.fixture
def live(bot, manager):
    return Live(bot, manager)


async def test_what_happens_in_a_game_reaches_the_thread_and_the_players_direct_messages(live, bot, manager):
    live.games()
    bot.alert_prefs.replace(1001, ["item_received"])
    await bot.cache.refresh()
    await bot.user_sync.sync_user(bot.links.get(1001))
    await bot.relay.maintain()
    manager.add("GET", "/api/games/7/activity", activity_json([], last=4))
    await bot.feeds.run_once()  # the first look: only finds out where the feed stands
    manager.add(
        "GET", "/api/games/7/activity",
        activity_json([entry(5, "chat", player="Bob", text="hello"),
                       entry(6, "item", finder="Bob", item="Sword", receiver="Alice", location="Chest")]),
    )
    bot.feeds._due.clear()
    await bot.feeds.run_once()
    [thread] = live.channel.threads
    assert [m.content for m in thread.sent[1:]] == ["**Bob**: hello"]  # items are off in a new thread
    [dm] = live.dms
    assert dm.kwargs["embed"].description == "**Bob** sent Sword to **Alice** (Chest)"


async def test_a_private_game_reaches_its_players_direct_messages_but_no_thread(live, bot, manager):
    private = game_json(9, "Secret Run", visibility="participants", my_slots=MINE)
    manager.add("GET", "/api/games", by_token({BOT_TOKEN: [], USER_TOKEN: [private]}))
    bot.alert_prefs.replace(1001, ["item_received"])
    await bot.cache.refresh()
    await bot.user_sync.sync_user(bot.links.get(1001))
    await bot.relay.maintain()
    bot.feeds._cursors.set(9, 0)
    manager.add("GET", "/api/games/9/activity", activity_json([entry(1, "item", finder="Bob", item="Sword", receiver="Alice", location="Chest")]))
    await bot.feeds.run_once()
    assert live.channel.threads == []
    assert len(live.dms) == 1
    assert manager.requests[-1].headers["Authorization"] == f"Bearer {USER_TOKEN}"


async def test_a_game_coming_online_reaches_the_player_who_asked_for_it(live, bot):
    bot.alert_prefs.replace(1001, ["game_online"])
    live.games(state="stopped")
    await bot.user_sync.sync_user(bot.links.get(1001))
    assert live.dms == []  # the first look only remembers
    live.games(state="running")
    await bot.user_sync.sync_user(bot.links.get(1001))
    [dm] = live.dms
    assert dm.kwargs["embed"].description == "The game is online"


async def test_a_slot_offered_to_the_player_reaches_them_with_buttons(live, bot, manager):
    bot.alert_prefs.replace(1001, ["slot_offered"])
    live.games()
    await bot.user_sync.sync_user(bot.links.get(1001))
    offer = {"slot": 2, "name": "Bob", "game": "Hollow Knight", "from": "bobby", "to": "alice", "to_me": True,
             "note": None, "can_accept": True}
    offered = game_json(7, "Friday Run", my_slots=MINE, offered_slots=[offer])
    manager.add("GET", "/api/games", by_token({USER_TOKEN: [offered], BOT_TOKEN: [offered]}))
    await bot.user_sync.sync_user(bot.links.get(1001))
    [dm] = live.dms
    assert [c.custom_id for c in dm.kwargs["view"].children] == ["apbot:ho:accept:7:2", "apbot:ho:decline:7:2"]


async def test_a_friend_request_reaches_the_person_who_asked_for_it_with_buttons_through_the_bots_own_wiring(live, bot, manager):
    bot.alert_prefs.replace(1001, ["friend_request"])
    live.games()
    manager.add("GET", "/api/friends", friends_json())
    await bot.user_sync.sync_user(bot.links.get(1001))
    assert live.dms == []  # the first look only remembers
    manager.add("GET", "/api/friends", friends_json(incoming=[friend_json(21, "cy", asked_at=100.0)]))
    await bot.user_sync.sync_user(bot.links.get(1001))
    [dm] = live.dms
    assert dm.kwargs["embed"].description == "**cy** wants to be your friend on the manager"
    assert [c.custom_id for c in dm.kwargs["view"].children] == ["apbot:fr:accept:21", "apbot:fr:decline:21"]
    assert [r.headers["Authorization"] for r in manager.requests if r.url.path == "/api/friends"] == [f"Bearer {USER_TOKEN}"] * 2


async def test_friends_are_not_asked_for_someone_who_did_not_turn_the_alert_on_through_the_bots_own_wiring(live, bot, manager):
    live.games()
    manager.add("GET", "/api/friends", friends_json(incoming=[friend_json(21, "cy", asked_at=100.0)]))
    await bot.user_sync.sync_user(bot.links.get(1001))
    assert "/api/friends" not in manager.paths() and live.dms == []


async def test_the_players_of_a_game_get_its_role_through_the_bots_own_wiring(live, bot):
    live.games()
    await bot.cache.refresh()
    await bot.relay.maintain()
    await bot.user_sync.sync_user(bot.links.get(1001))
    assert live.member.added == [bot.spaces.get(GUILD, 7).role_id]


async def test_a_stopped_token_is_told_to_the_person_once(live, bot, manager):
    manager.add("GET", "/api/games", (401, {"detail": "no"}))
    await bot.user_sync.sync_user(bot.links.get(1001))
    [dm] = live.dms
    assert dm.kwargs["embed"].description == "Your manager token stopped working. Make a new one and run /link again"
    assert bot.links.get(1001).status == "invalid"


async def test_unlinking_and_linking_again_makes_the_bot_read_the_persons_hints_afresh(live, bot, manager):
    game = FeedGame(7, "Friday Run", "running", True)
    chat = [FeedEntry(1, 0.0, "chat", {"player": "B", "text": "x"})]
    item_entry = FeedEntry(2, 0.0, "item", {"finder": "Bob", "item": "Sword", "receiver": "Alice", "location": "Chest"})
    live.games()
    bot.alert_prefs.replace(1001, ["hint_found"])
    manager.add("GET", "/api/games/7/slots/1/tracker", by_token({USER_TOKEN: tracker_json([])}))
    await bot.user_sync.sync_user(bot.links.get(1001))
    await bot.alerts.handle(game, tuple(chat))  # the list is read: no hints yet
    interaction = FakeInteraction(bot, user_id=1001)
    task = asyncio.create_task(LinkingCog(bot).unlink.callback(LinkingCog(bot), interaction))
    for _ in range(10):
        await asyncio.sleep(0)
    await interaction.messages[0]["view"].children[0].callback(FakeInteraction(bot, user_id=1001))
    await task
    bot.links.save(1001, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
    bot.alert_prefs.replace(1001, ["hint_found"])
    await bot.user_sync.sync_user(bot.links.get(1001))
    manager.add("GET", "/api/games/7/slots/1/tracker", by_token({USER_TOKEN: tracker_json([hint_json("Sword", "Alice", "Bob", "Chest")])}))
    await bot.alerts.handle(game, (item_entry,))
    assert len(live.dms) == 1
