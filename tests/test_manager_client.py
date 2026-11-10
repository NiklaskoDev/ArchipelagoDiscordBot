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
