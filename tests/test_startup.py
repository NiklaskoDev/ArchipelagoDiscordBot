import logging

import httpx
import pytest
from fakes import BOT_TOKEN, SITE_URL, FakeManager

from apbot.services.game_cache import GameCache
from apbot.services.manager_watch import (
    EXIT_BAD_MANAGER_TOKEN,
    MIN_MANAGER_VERSION,
    ManagerWatch,
    StartupFailure,
    check_manager,
    version_at_least,
)

HEALTH = {"ok": True, "version": "1.3.0", "archipelago": "0.6.8", "ready": True, "archipelago_error": None}


def bot_user(role: str = "user") -> dict:
    return {"user": {"id": 2, "username": "apbot", "role": role}, "site_url": SITE_URL}


def watch(manager: FakeManager) -> tuple[ManagerWatch, GameCache]:
    cache = GameCache(manager.client, BOT_TOKEN)
    return ManagerWatch(manager.client, BOT_TOKEN, cache), cache


async def test_healthy_manager_with_a_plain_user_passes_and_site_url_is_cached(manager, caplog):
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", bot_user())
    manager_watch, cache = watch(manager)
    with caplog.at_level(logging.WARNING):
        assert await manager_watch.check_once() is True
    assert cache.site_url == SITE_URL
    assert not caplog.records


async def test_unreachable_manager_does_not_stop_the_bot_and_is_tried_again(manager, caplog):
    manager.add("GET", "/api/health", httpx.ConnectError("refused"))
    manager.add("GET", "/api/auth/status", bot_user())
    manager_watch, cache = watch(manager)
    with caplog.at_level(logging.ERROR):
        assert await manager_watch.check_once() is False
    assert "could not be reached" in caplog.text
    assert cache.site_url is None
    manager.add("GET", "/api/health", HEALTH)  # the manager is up now
    assert await manager_watch.check_once() is True
    assert cache.site_url == SITE_URL


async def test_token_the_manager_does_not_know_ends_the_start_with_exit_code_2(manager):
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", {"user": None, "site_url": None})
    with pytest.raises(StartupFailure) as failure:
        await watch(manager)[0].check_once()
    assert failure.value.exit_code == EXIT_BAD_MANAGER_TOKEN == 2
    assert "MANAGER_API_TOKEN is not accepted by the manager" in str(failure.value)
    assert BOT_TOKEN not in str(failure.value)


async def test_rejected_token_401_ends_the_start_with_exit_code_2(manager):
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(StartupFailure) as failure:
        await watch(manager)[0].check_once()
    assert failure.value.exit_code == 2


@pytest.mark.parametrize("role", ["admin", "organizer"])
async def test_bot_account_with_more_than_user_rights_is_warned_about(manager, caplog, role):
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", bot_user(role))
    with caplog.at_level(logging.WARNING):
        assert await watch(manager)[0].check_once() is True
    assert "should be a plain user" in caplog.text
    assert any(record.levelno == logging.WARNING for record in caplog.records)


async def test_old_manager_version_is_warned_about_but_allowed(manager, caplog):
    manager.add("GET", "/api/health", {**HEALTH, "version": "1.2.9"})
    manager.add("GET", "/api/auth/status", bot_user())
    with caplog.at_level(logging.WARNING):
        assert await watch(manager)[0].check_once() is True
    assert "1.2.9" in caplog.text and MIN_MANAGER_VERSION in caplog.text


async def test_report_says_what_the_manager_told(manager):
    manager.add("GET", "/api/health", {**HEALTH, "ready": False})
    manager.add("GET", "/api/auth/status", bot_user("admin"))
    report = await check_manager(manager.client, BOT_TOKEN)
    assert (report.version, report.ready, report.username, report.role) == ("1.3.0", False, "apbot", "admin")
    assert report.site_url == SITE_URL


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.3.0", True), ("1.3.1", True), ("2.0.0", True), ("1.10.0", True), ("1.2.9", False), ("1.3.0-pr2", True)]
    + [("garbage", False), (None, False), (130, False)],
)
def test_version_at_least(version, expected):
    assert version_at_least(version, "1.3.0") is expected


@pytest.mark.parametrize(
    "health",
    [(404, {"detail": "Not found"}), (403, {"detail": "Blocked by proxy"}), (429, {"detail": "Slow down"}), (401, {"detail": "x"})],
)
async def test_an_unusable_health_answer_is_retried_not_fatal(manager, caplog, health):
    manager.add("GET", "/api/health", health)
    with caplog.at_level(logging.ERROR):
        assert await watch(manager)[0].check_once() is False
    assert "Trying again" in caplog.text


@pytest.mark.parametrize("body", [["not", "a", "dict"], "text", None])
async def test_a_health_answer_that_is_not_an_object_is_retried_not_fatal(manager, body):
    manager.add("GET", "/api/health", body)
    assert await watch(manager)[0].check_once() is False


async def test_a_busy_manager_at_the_account_check_is_retried_not_fatal(manager):
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", (429, {"detail": "Slow down"}))
    assert await watch(manager)[0].check_once() is False
