import json

import httpx
import pytest
from fakes import BOT_TOKEN, FakeManager

from apbot.manager.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    ManagerError,
    ManagerUnavailable,
    NotFound,
    ReadOnlyToken,
    TokenInvalid,
    TooManyRequests,
)


async def test_token_goes_in_the_authorization_header_only(manager: FakeManager):
    manager.add("GET", "/api/games", [])
    await manager.client.list_games(BOT_TOKEN)
    request = manager.requests[0]
    assert request.headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert BOT_TOKEN not in str(request.url)


async def test_health_is_asked_without_a_token(manager: FakeManager):
    manager.add("GET", "/api/health", {"ok": True, "version": "1.3.0"})
    assert (await manager.client.health())["version"] == "1.3.0"
    assert "Authorization" not in manager.requests[0].headers


async def test_endpoints_return_the_manager_json(manager: FakeManager):
    manager.add("GET", "/api/auth/status", {"user": {"id": 1}})
    manager.add("GET", "/api/games/7", {"id": 7})
    manager.add("GET", "/api/signups", [{"id": 2}])
    assert await manager.client.auth_status(BOT_TOKEN) == {"user": {"id": 1}}
    assert await manager.client.game(BOT_TOKEN, 7) == {"id": 7}
    assert await manager.client.list_signups(BOT_TOKEN) == [{"id": 2}]


@pytest.mark.parametrize(
    ("status", "detail", "error"),
    [
        (401, "This API token is not valid (any more)", TokenInvalid),
        (403, "This API token can only read", ReadOnlyToken),
        (403, "You may not do this", Forbidden),
        (404, "Game not found", NotFound),
        (409, "The game is already running", Conflict),
        (429, "Too many requests", TooManyRequests),
        (400, "Bad input", BadRequest),
        (413, "Too large", BadRequest),
    ],
)
async def test_error_status_maps_to_its_exception_and_keeps_the_detail(manager: FakeManager, status, detail, error):
    manager.add("GET", "/api/games/1", (status, {"detail": detail}))
    with pytest.raises(error) as raised:
        await manager.client.game(BOT_TOKEN, 1)
    assert type(raised.value) is error
    assert raised.value.detail == detail
    assert raised.value.status == status


async def test_validation_errors_are_joined(manager: FakeManager):
    manager.add("GET", "/api/games/1", (422, {"detail": [{"msg": "field required"}, {"msg": "too short"}]}))
    with pytest.raises(BadRequest) as raised:
        await manager.client.game(BOT_TOKEN, 1)
    assert raised.value.detail == "field required; too short"


async def test_error_without_json_body_still_names_the_status(manager: FakeManager):
    manager.add("GET", "/api/games/1", httpx.Response(418, text="teapot"))
    with pytest.raises(ManagerError) as raised:
        await manager.client.game(BOT_TOKEN, 1)
    assert raised.value.detail == "HTTP 418"


async def test_read_is_retried_when_the_manager_is_unavailable_then_succeeds(manager: FakeManager):
    manager.add("GET", "/api/games", (503, {"detail": "starting"}), (502, {"detail": "starting"}), [{"id": 1}])
    assert await manager.client.list_games(BOT_TOKEN) == [{"id": 1}]
    assert len(manager.requests) == 3
    assert manager.sleeps == [0.5, 1.0]


async def test_read_gives_up_after_three_retries(manager: FakeManager):
    manager.add("GET", "/api/games", (503, {"detail": "down"}))
    with pytest.raises(ManagerUnavailable):
        await manager.client.list_games(BOT_TOKEN)
    assert len(manager.requests) == 4
    assert manager.sleeps == [0.5, 1.0, 2.0]


async def test_client_errors_are_not_retried(manager: FakeManager):
    manager.add("GET", "/api/games", (401, {"detail": "no"}))
    with pytest.raises(TokenInvalid):
        await manager.client.list_games(BOT_TOKEN)
    assert len(manager.requests) == 1


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_network_failure_is_unavailable_and_does_not_leak_the_token(manager: FakeManager, failure):
    manager.add("GET", "/api/games", failure)
    with pytest.raises(ManagerUnavailable) as raised:
        await manager.client.list_games(BOT_TOKEN)
    assert raised.value.status is None
    assert BOT_TOKEN not in str(raised.value)
    assert len(manager.requests) == 4


async def test_answer_that_is_not_json_is_unavailable(manager: FakeManager):
    manager.add("GET", "/api/games", httpx.Response(200, text="<html>proxy error</html>"))
    with pytest.raises(ManagerUnavailable):
        await manager.client.list_games(BOT_TOKEN)


# --- changes (POST) ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("action", ["wake", "start", "stop", "restart"])
async def test_game_control_posts_to_the_games_endpoint_with_the_token_in_the_header(manager: FakeManager, action):
    manager.add("POST", f"/api/games/7/{action}", {"id": 7, "state": "running"})
    assert await getattr(manager.client, action)(BOT_TOKEN, 7) == {"id": 7, "state": "running"}
    [request] = manager.requests
    assert (request.method, request.url.path) == ("POST", f"/api/games/7/{action}")
    assert request.headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert BOT_TOKEN not in str(request.url)
    assert request.content == b""


async def test_game_control_allows_a_server_that_takes_time_to_stop(manager: FakeManager):
    manager.add("POST", "/api/games/7/stop", {"id": 7})
    manager.add("GET", "/api/games", [])
    await manager.client.stop(BOT_TOKEN, 7)
    await manager.client.list_games(BOT_TOKEN)
    stop, read = manager.requests
    assert stop.extensions["timeout"]["read"] == 60
    assert read.extensions["timeout"]["read"] == 10


@pytest.mark.parametrize("status", [502, 503, 504, 500])
async def test_a_change_is_not_retried_when_the_manager_is_unavailable(manager: FakeManager, status):
    manager.add("POST", "/api/games/7/start", (status, {"detail": "busy"}), {"id": 7})
    with pytest.raises(ManagerUnavailable):
        await manager.client.start(BOT_TOKEN, 7)
    assert len(manager.requests) == 1
    assert manager.sleeps == []


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_a_change_is_not_retried_after_a_network_failure(manager: FakeManager, failure):
    manager.add("POST", "/api/games/7/restart", failure, {"id": 7})
    with pytest.raises(ManagerUnavailable) as raised:
        await manager.client.restart(BOT_TOKEN, 7)
    assert len(manager.requests) == 1
    assert BOT_TOKEN not in str(raised.value)


async def test_a_change_keeps_the_409_detail_and_maps_the_other_errors(manager: FakeManager):
    manager.add("POST", "/api/games/7/start", (409, {"detail": "The game is already running"}))
    with pytest.raises(Conflict) as raised:
        await manager.client.start(BOT_TOKEN, 7)
    assert raised.value.detail == "The game is already running"
    manager.add("POST", "/api/games/7/start", (403, {"detail": "This API token can only read"}))
    with pytest.raises(ReadOnlyToken):
        await manager.client.start(BOT_TOKEN, 7)
    manager.add("POST", "/api/games/7/start", (401, {"detail": "no"}))
    with pytest.raises(TokenInvalid):
        await manager.client.start(BOT_TOKEN, 7)


async def test_the_game_id_is_a_number_in_the_path(manager: FakeManager):
    with pytest.raises(ValueError):
        await manager.client.start(BOT_TOKEN, "7/../../tokens")


async def test_a_quick_game_list_gives_up_after_two_seconds_and_is_not_retried(manager: FakeManager):
    manager.add("GET", "/api/games", (503, {"detail": "busy"}), [])
    with pytest.raises(ManagerUnavailable):
        await manager.client.list_games(BOT_TOKEN, quick=True)
    assert len(manager.requests) == 1 and manager.requests[0].extensions["timeout"]["read"] == 2


async def test_a_plain_game_list_is_retried_when_the_manager_is_busy(manager: FakeManager):
    manager.add("GET", "/api/games", (503, {"detail": "busy"}), [])
    assert await manager.client.list_games(BOT_TOKEN) == []
    assert len(manager.requests) == 2


# --- generating, archiving and deleting --------------------------------------------------------------------------------
async def test_a_job_is_started_with_exactly_these_fields_and_never_retried(manager: FakeManager):
    manager.add("POST", "/api/jobs", (503, {"detail": "busy"}), {"id": 9})
    call = manager.client.start_job(
        BOT_TOKEN, [40, "41"], name="Friday", spoiler=2, race=True, seed=123, host_after=False
    )
    with pytest.raises(ManagerUnavailable):
        await call
    [request] = manager.requests
    assert (request.method, request.url.path) == ("POST", "/api/jobs")
    assert request.headers["Authorization"] == f"Bearer {BOT_TOKEN}" and BOT_TOKEN not in str(request.url)
    assert json.loads(request.content) == {
        "config_ids": [40, 41], "name": "Friday", "spoiler": 2, "race": True, "seed": 123, "host_after": False,
    }


async def test_a_job_without_name_or_seed_sends_nulls(manager: FakeManager):
    manager.add("POST", "/api/jobs", {"id": 9})
    await manager.client.start_job(BOT_TOKEN, [40], name=None, spoiler=1, race=False, seed=None, host_after=True)
    assert json.loads(manager.requests[0].content) == {
        "config_ids": [40], "name": None, "spoiler": 1, "race": False, "seed": None, "host_after": True,
    }


async def test_a_job_is_read_by_its_number_and_a_quick_look_gives_up_after_two_seconds(manager: FakeManager):
    manager.add("GET", "/api/jobs/9", {"id": 9})
    assert await manager.client.job(BOT_TOKEN, 9) == {"id": 9}
    await manager.client.job(BOT_TOKEN, 9, quick=True)
    plain, quick = manager.requests
    assert (plain.method, plain.url.path) == ("GET", "/api/jobs/9") and plain.extensions["timeout"]["read"] == 10
    assert quick.extensions["timeout"]["read"] == 2


async def test_cancelling_a_job_is_a_post_without_a_body_and_keeps_the_409_detail(manager: FakeManager):
    manager.add("POST", "/api/jobs/9/cancel", (409, {"detail": "That job is not running or queued"}), {"id": 9})
    with pytest.raises(Conflict) as raised:
        await manager.client.cancel_job(BOT_TOKEN, 9)
    assert raised.value.detail == "That job is not running or queued"
    [request] = manager.requests
    assert (request.method, request.url.path, request.content) == ("POST", "/api/jobs/9/cancel", b"")
    assert len(manager.requests) == 1


@pytest.mark.parametrize("archived", [True, False])
async def test_archiving_posts_the_flag_and_waits_for_the_server_to_stop(manager: FakeManager, archived):
    manager.add("POST", "/api/games/7/archive", (503, {"detail": "busy"}), {"id": 7})
    with pytest.raises(ManagerUnavailable):
        await manager.client.archive_game(BOT_TOKEN, 7, archived)
    [request] = manager.requests
    assert (request.method, request.url.path) == ("POST", "/api/games/7/archive")
    assert json.loads(request.content) == {"archived": archived}
    assert request.extensions["timeout"]["read"] == 60  # CONTROL_TIMEOUT: archiving stops the server


async def test_deleting_a_game_is_a_delete_that_is_not_retried(manager: FakeManager):
    manager.add("DELETE", "/api/games/7", (503, {"detail": "busy"}), {"ok": True})
    with pytest.raises(ManagerUnavailable):
        await manager.client.delete_game(BOT_TOKEN, 7)
    [request] = manager.requests
    assert (request.method, request.url.path, request.content) == ("DELETE", "/api/games/7", b"")
    assert request.headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert request.extensions["timeout"]["read"] == 60


@pytest.mark.parametrize("bad", ["7/../../tokens", "x"])
async def test_the_new_calls_take_numbers_in_their_paths_only(manager: FakeManager, bad):
    calls = (
        lambda: manager.client.job(BOT_TOKEN, bad),
        lambda: manager.client.cancel_job(BOT_TOKEN, bad),
        lambda: manager.client.archive_game(BOT_TOKEN, bad, True),
        lambda: manager.client.delete_game(BOT_TOKEN, bad),
    )
    for call in calls:
        with pytest.raises(ValueError):
            await call()
    assert manager.requests == []
