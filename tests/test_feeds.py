import pytest
from fakes import BOT_TOKEN, USER_TOKEN, activity_json, by_token, entry, game_json

from apbot.db.live import FeedCursorStore
from apbot.services.feeds import ACTIVE_INTERVAL, IDLE_INTERVAL, FeedPoller
from apbot.services.user_sync import UserSync

MINE = [{"slot": 1, "name": "Alice", "game": "Timespinner", "idle_since": None}]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Setup:
    """A poller with its neighbours, and recorders for the two kinds of sink."""

    def __init__(self, bot, manager):
        async def no_wait(seconds):
            pass

        self.bot, self.manager = bot, manager
        self.clock = Clock()
        self.sync = UserSync(bot.links, bot.access, manager.client, sleep=no_wait)
        self.cursors = FeedCursorStore(bot.db)
        self.poller = self.make_poller()
        self.public: list = []
        self.private: list = []

        async def public_sink(game, entries):
            self.public.append((game, entries))

        async def every_sink(game, entries):
            self.private.append((game, entries))

        self.poller.add_sink(public_sink, public_only=True)
        self.poller.add_sink(every_sink)

    def make_poller(self) -> FeedPoller:
        bot = self.bot
        return FeedPoller(
            self.manager.client, BOT_TOKEN, bot.cache, self.sync, bot.links, bot.access, self.cursors, now=self.clock
        )

    def feed(self, game_id: int, answer) -> None:
        self.manager.add("GET", f"/api/games/{game_id}/activity", answer)

    async def load_games(self, *raw) -> None:
        self.manager.add("GET", "/api/games", by_token({BOT_TOKEN: list(raw), USER_TOKEN: list(raw)}))
        self.manager.add("GET", "/api/auth/status", {"user": {"id": 2}, "site_url": None})
        await self.bot.cache.refresh()

    def link_user(self, raw_for_user) -> None:
        self.bot.links.save(1001, 10, "alice", self.bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
        self.user_games = raw_for_user

    def activity_requests(self) -> list:
        return [r for r in self.manager.requests if r.url.path.endswith("/activity")]


@pytest.fixture
def setup(bot, manager):
    return Setup(bot, manager)


async def test_the_first_look_only_finds_out_where_the_feed_stands(setup):
    await setup.load_games(game_json(7))
    setup.feed(7, activity_json([entry(1, "goal", player="A"), entry(2, "chat", player="B", text="old")], last=2))
    await setup.poller.poll(setup.poller.games()[0])
    assert setup.public == [] and setup.private == []
    assert setup.cursors.get(7) == 2


async def test_later_entries_are_handed_on_once_and_the_cursor_moves(setup):
    await setup.load_games(game_json(7, name="Friday Run"))
    setup.cursors.set(7, 2)
    setup.feed(7, activity_json([entry(3, "goal", player="A"), entry(4, "chat", player="B", text="hi")]))
    game = setup.poller.games()[0]
    await setup.poller.poll(game)
    [(shown, entries)] = setup.public
    assert (shown.id, shown.name, shown.public) == (7, "Friday Run", True)
    assert [e.seq for e in entries] == [3, 4]
    assert setup.cursors.get(7) == 4
    await setup.poller.poll(game)  # the manager answers with the same entries again
    assert len(setup.public) == 1


async def test_the_manager_is_asked_for_what_comes_after_the_cursor(setup):
    await setup.load_games(game_json(7))
    setup.feed(7, activity_json([], last=0))
    game = setup.poller.games()[0]
    await setup.poller.poll(game)
    setup.cursors.set(7, 41)
    await setup.poller.poll(game)
    assert [r.url.params["after"] for r in setup.activity_requests()] == ["0", "41"]


async def test_the_cursor_survives_a_restart(setup):
    await setup.load_games(game_json(7))
    setup.cursors.set(7, 2)
    setup.feed(7, activity_json([entry(3, "goal", player="A")]))
    await setup.poller.poll(setup.poller.games()[0])
    restarted = setup.make_poller()
    seen = []

    async def sink(game, entries):
        seen.append(entries)

    restarted.add_sink(sink)
    await restarted.poll(restarted.games()[0])
    assert seen == []  # entry 3 was handled before the restart


async def test_a_feed_that_starts_over_resets_the_cursor_and_says_nothing(setup):
    await setup.load_games(game_json(7))
    setup.cursors.set(7, 50)
    setup.feed(7, activity_json([entry(1, "goal", player="A")], last=3))
    await setup.poller.poll(setup.poller.games()[0])
    assert setup.cursors.get(7) == 3 and setup.public == []
    setup.feed(7, activity_json([entry(4, "goal", player="B")]))
    await setup.poller.poll(setup.poller.games()[0])
    assert [e.seq for e in setup.public[0][1]] == [4]


async def test_entries_of_unknown_kinds_move_the_cursor_without_being_handed_on(setup):
    await setup.load_games(game_json(7))
    setup.cursors.set(7, 1)
    setup.feed(7, activity_json([{"seq": 2, "kind": "teleport"}], last=2))
    await setup.poller.poll(setup.poller.games()[0])
    assert setup.public == [] and setup.cursors.get(7) == 2


async def test_a_game_the_bot_sees_is_read_with_the_bots_token_and_shown_publicly(setup):
    await setup.load_games(game_json(7))
    setup.cursors.set(7, 0)
    setup.feed(7, activity_json([entry(1, "goal", player="A")]))
    await setup.poller.poll(setup.poller.games()[0])
    assert setup.manager.requests[-1].headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert len(setup.public) == 1 and len(setup.private) == 1


async def test_a_private_game_is_read_with_a_players_token_and_never_shown_publicly(setup):
    await setup.load_games()  # the bot account sees no games
    setup.link_user(None)
    setup.manager.add("GET", "/api/games", by_token({BOT_TOKEN: [], USER_TOKEN: [game_json(9, "Secret", visibility="participants", my_slots=MINE)]}))
    await setup.sync.sync_user(setup.bot.links.get(1001))
    setup.cursors.set(9, 0)
    setup.feed(9, activity_json([entry(1, "chat", player="Alice", text="psst")]))
    [game] = setup.poller.games()
    assert (game.id, game.public) == (9, False)
    await setup.poller.poll(game)
    assert setup.manager.requests[-1].headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert setup.public == []
    assert [e.seq for e in setup.private[0][1]] == [1]


async def test_a_game_the_bot_account_sees_but_that_is_not_open_to_all_is_not_shown_publicly(setup):
    await setup.load_games(game_json(7, visibility="participants"))
    [game] = setup.poller.games()
    assert game.public is False


async def test_a_private_game_nobody_linked_plays_in_is_not_read(setup):
    await setup.load_games()
    assert setup.poller.games() == []
    await setup.poller.run_once()
    assert setup.activity_requests() == []


async def test_a_game_with_no_usable_reader_is_skipped(setup):
    await setup.load_games()
    setup.link_user(None)
    setup.manager.add("GET", "/api/games", by_token({BOT_TOKEN: [], USER_TOKEN: [game_json(9, my_slots=MINE)]}))
    await setup.sync.sync_user(setup.bot.links.get(1001))
    setup.bot.links.mark_invalid(1001)  # the token is gone since the last look
    [game] = setup.poller.games()
    await setup.poller.poll(game)
    assert setup.activity_requests() == []


async def test_a_players_token_the_manager_refuses_marks_that_link_invalid(setup):
    await setup.load_games()
    setup.link_user(None)
    setup.manager.add("GET", "/api/games", by_token({BOT_TOKEN: [], USER_TOKEN: [game_json(9, my_slots=MINE)]}))
    await setup.sync.sync_user(setup.bot.links.get(1001))
    setup.feed(9, (401, {"detail": "This API token is not valid (any more)"}))
    await setup.poller.poll(setup.poller.games()[0])
    assert setup.bot.links.get(1001).status == "invalid"


async def test_finished_games_are_not_followed(setup):
    await setup.load_games(game_json(7, finished_at=1_700_000_500.0), game_json(8))
    assert [g.id for g in setup.poller.games()] == [8]


async def test_a_manager_that_fails_changes_nothing(setup):
    await setup.load_games(game_json(7))
    setup.cursors.set(7, 5)
    setup.feed(7, (503, {"detail": "down"}))
    await setup.poller.poll(setup.poller.games()[0])
    assert setup.cursors.get(7) == 5 and setup.public == []
    setup.feed(7, {"entries": "nonsense", "last": 9})
    await setup.poller.poll(setup.poller.games()[0])
    assert setup.cursors.get(7) == 5


async def test_a_failing_sink_does_not_stop_the_others_and_does_not_repeat_the_entries(setup, caplog):
    await setup.load_games(game_json(7))
    setup.cursors.set(7, 0)
    calls = []

    async def broken(game, entries):
        calls.append(1)
        raise RuntimeError("boom")

    setup.poller._sinks.insert(0, (broken, False))
    setup.feed(7, activity_json([entry(1, "goal", player="A")]))
    game = setup.poller.games()[0]
    await setup.poller.poll(game)
    await setup.poller.poll(game)
    assert calls == [1]  # not offered again after the cursor moved
    assert len(setup.public) == 1 and len(setup.private) == 1


# --- how often ---------------------------------------------------------------------------------------------------------
async def test_a_running_game_is_looked_at_every_few_seconds_and_an_idle_one_every_minute(setup):
    await setup.load_games(game_json(7, state="running"), game_json(8, state="stopped"))
    setup.feed(7, activity_json([], last=0))
    setup.feed(8, activity_json([], last=0))

    async def looks() -> dict[str, int]:
        before = len(setup.activity_requests())
        await setup.poller.run_once()
        return len(setup.activity_requests()) - before

    assert await looks() == 2  # both at the start
    setup.clock.now += ACTIVE_INTERVAL
    assert await looks() == 1  # only the running one
    setup.clock.now += ACTIVE_INTERVAL
    assert await looks() == 1
    setup.clock.now += IDLE_INTERVAL
    assert await looks() == 2
    paths = [r.url.path for r in setup.activity_requests()]
    assert paths.count("/api/games/7/activity") == 4 and paths.count("/api/games/8/activity") == 2


async def test_a_players_token_refused_while_reading_a_feed_tells_the_person_and_drops_their_roles(setup):
    told, memberships = [], []

    async def stopped(link):
        told.append(link.discord_user_id)

    async def roles(discord_id, game_ids):
        memberships.append((discord_id, game_ids))

    setup.sync.listen(on_memberships=roles, on_token_stopped=stopped)
    await setup.load_games()
    setup.link_user(None)
    setup.manager.add("GET", "/api/games", by_token({BOT_TOKEN: [], USER_TOKEN: [game_json(9, my_slots=MINE)]}))
    await setup.sync.sync_user(setup.bot.links.get(1001))
    setup.feed(9, (401, {"detail": "This API token is not valid (any more)"}))
    await setup.poller.poll(setup.poller.games()[0])
    assert told == [1001] and memberships[-1] == (1001, frozenset())
    assert setup.bot.links.get(1001).status == "invalid"
    assert setup.poller.games() == []  # nobody is left to read the private game
