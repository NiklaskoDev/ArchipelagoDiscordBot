import asyncio
import logging

import httpx
from fakes import BOT_TOKEN, SITE_URL, FakeManager, game_json

from apbot.heartbeat import HEARTBEAT_FILE, Heartbeat
from apbot.services.game_cache import REFRESH_INTERVAL, SITE_URL_INTERVAL, GameCache
from apbot.services.scheduler import Periodic, StopPeriodic


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


async def let_tasks_run() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


# --- Periodic ----------------------------------------------------------------------------------------------------------
async def test_periodic_runs_at_once_then_after_every_interval():
    sleeps, runs = [], []

    async def sleep(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(0)

    async def action():
        runs.append(len(runs))

    periodic = Periodic("test", 7, action, sleep=sleep)
    periodic.start()
    await let_tasks_run()
    await periodic.stop()
    assert len(runs) >= 3
    assert set(sleeps) == {7}


async def test_periodic_survives_a_failing_run_and_logs_it(caplog):
    runs = []

    async def sleep(seconds):
        await asyncio.sleep(0)

    async def action():
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("boom")

    periodic = Periodic("flaky", 1, action, sleep=sleep)
    with caplog.at_level(logging.ERROR):
        periodic.start()
        await let_tasks_run()
        await periodic.stop()
    assert len(runs) > 1
    assert "flaky" in caplog.text and "boom" in caplog.text


async def test_periodic_ends_by_itself_when_the_action_says_so():
    runs = []

    async def sleep(seconds):
        await asyncio.sleep(0)

    async def action():
        runs.append(1)
        if len(runs) == 3:
            raise StopPeriodic

    periodic = Periodic("finite", 1, action, sleep=sleep)
    periodic.start()
    await let_tasks_run()
    await periodic.stop()
    assert len(runs) == 3


async def test_stopping_a_periodic_that_never_started_is_fine():
    async def action():
        pass

    await Periodic("idle", 1, action).stop()


# --- GameCache ---------------------------------------------------------------------------------------------------------
def cache_for(manager: FakeManager, clock: Clock | None = None) -> GameCache:
    return GameCache(manager.client, BOT_TOKEN, now=clock or Clock())


async def test_cache_is_empty_and_not_loaded_before_the_first_refresh(manager):
    cache = cache_for(manager)
    assert cache.games == [] and cache.loaded is False and cache.site_url is None


async def test_refresh_reads_games_with_the_bot_token_and_the_site_url(manager):
    manager.add("GET", "/api/games", [game_json(1, "One"), game_json(2, "Two")])
    manager.add("GET", "/api/auth/status", {"user": {"id": 1}, "site_url": SITE_URL})
    cache = cache_for(manager)
    await cache.refresh()
    assert [g.name for g in cache.games] == ["One", "Two"]
    assert cache.loaded and cache.site_url == SITE_URL
    assert {r.headers["Authorization"] for r in manager.requests} == {f"Bearer {BOT_TOKEN}"}


async def test_get_loads_once_and_then_serves_from_memory(manager):
    manager.add("GET", "/api/games", [game_json(1)])
    manager.add("GET", "/api/auth/status", {"site_url": None})
    cache = cache_for(manager)
    assert [g.id for g in await cache.get()] == [1]
    await cache.get()
    assert manager.paths().count("/api/games") == 1


async def test_failed_refresh_keeps_the_old_list_and_the_background_run_only_logs(manager, caplog):
    manager.add("GET", "/api/games", [game_json(1)], (503, {"detail": "down"}))
    manager.add("GET", "/api/auth/status", {"site_url": None})
    cache = cache_for(manager)
    await cache.run_refresh()
    with caplog.at_level(logging.WARNING):
        await cache.run_refresh()
    assert [g.id for g in cache.games] == [1]
    assert "Could not refresh" in caplog.text
    assert not any(record.exc_info for record in caplog.records)


async def test_site_url_is_asked_again_only_every_ten_minutes(manager):
    manager.add("GET", "/api/games", [])
    manager.add("GET", "/api/auth/status", {"site_url": SITE_URL}, {"site_url": "https://other.example"})
    clock = Clock()
    cache = cache_for(manager, clock)
    await cache.refresh()
    clock.now += REFRESH_INTERVAL
    await cache.refresh()
    assert manager.paths().count("/api/auth/status") == 1
    assert cache.site_url == SITE_URL
    clock.now += SITE_URL_INTERVAL
    await cache.refresh()
    assert cache.site_url == "https://other.example"


async def test_site_url_that_cannot_be_read_does_not_fail_the_refresh(manager, caplog):
    manager.add("GET", "/api/games", [game_json(1)])
    manager.add("GET", "/api/auth/status", httpx.ConnectError("refused"))
    cache = cache_for(manager)
    with caplog.at_level(logging.WARNING):
        await cache.refresh()
    assert cache.loaded and cache.site_url is None


async def test_empty_site_url_means_none(manager):
    manager.add("GET", "/api/games", [])
    manager.add("GET", "/api/auth/status", {"site_url": ""})
    cache = cache_for(manager)
    await cache.refresh()
    assert cache.site_url is None


# --- Heartbeat ---------------------------------------------------------------------------------------------------------
async def test_heartbeat_writes_the_time_while_connected(tmp_path):
    clock = Clock(1_700_000_123.9)
    await Heartbeat(tmp_path, lambda: True, now=clock).beat()
    assert (tmp_path / HEARTBEAT_FILE).read_text() == "1700000123"


async def test_heartbeat_stays_silent_while_disconnected(tmp_path):
    await Heartbeat(tmp_path, lambda: False).beat()
    assert not (tmp_path / HEARTBEAT_FILE).exists()


async def test_heartbeat_creates_the_data_folder_and_overwrites(tmp_path):
    clock = Clock(100.0)
    heartbeat = Heartbeat(tmp_path / "new" / "folder", lambda: True, now=clock)
    await heartbeat.beat()
    clock.now = 200.0
    await heartbeat.beat()
    assert (tmp_path / "new" / "folder" / HEARTBEAT_FILE).read_text() == "200"
    assert [p.name for p in (tmp_path / "new" / "folder").iterdir()] == [HEARTBEAT_FILE]


async def test_heartbeat_that_cannot_write_logs_instead_of_crashing(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    with caplog.at_level(logging.ERROR):
        await Heartbeat(blocker / "data", lambda: True).beat()
    assert "heartbeat" in caplog.text
