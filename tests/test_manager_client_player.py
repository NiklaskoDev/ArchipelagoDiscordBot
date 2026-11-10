import json

import httpx
import pytest
from fakes import USER_TOKEN, FakeManager

from apbot.manager.errors import ManagerUnavailable


async def sent(manager: FakeManager, call, method, path, answer=None):
    manager.add(method, path, answer or {"ok": True})
    await call
    [request] = manager.requests
    assert (request.method, request.url.path) == (method, path)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}" and USER_TOKEN not in str(request.url)
    return request, json.loads(request.content) if request.content else None


async def test_polling_the_text_client_posts_the_number_to_start_after(manager: FakeManager):
    _, body = await sent(manager, manager.client.client_poll(USER_TOKEN, 7, 2, 5), "POST", "/api/games/7/slots/2/client")
    assert body == {"after": 5}


async def test_polling_never_asks_for_a_negative_number(manager: FakeManager):
    _, body = await sent(manager, manager.client.client_poll(USER_TOKEN, 7, 2, -4), "POST", "/api/games/7/slots/2/client")
    assert body == {"after": 0}


async def test_polling_is_not_repeated_when_the_manager_is_busy(manager: FakeManager):
    manager.add("POST", "/api/games/7/slots/2/client", (503, {"detail": "busy"}))
    with pytest.raises(ManagerUnavailable):
        await manager.client.client_poll(USER_TOKEN, 7, 2, 0)
    assert len(manager.requests) == 1


async def test_saying_posts_the_text(manager: FakeManager):
    call = manager.client.client_say(USER_TOKEN, 7, 2, "!hint Sword")
    _, body = await sent(manager, call, "POST", "/api/games/7/slots/2/client/say")
    assert body == {"text": "!hint Sword"}


async def test_the_names_are_a_read_that_is_retried(manager: FakeManager):
    manager.add("GET", "/api/games/7/slots/2/client/names", (503, {"detail": "busy"}), {"items": ["a"], "locations": []})
    assert (await manager.client.client_names(USER_TOKEN, 7, 2))["items"] == ["a"]
    assert len(manager.requests) == 2


@pytest.mark.parametrize("action", ["release", "collect"])
async def test_a_slot_action_posts_the_action_with_a_longer_timeout(manager: FakeManager, action):
    call = manager.client.slot_action(USER_TOKEN, 7, 2, action)
    request, body = await sent(manager, call, "POST", "/api/games/7/slots/2/action", {"done": True, "message": ""})
    assert body == {"action": action} and request.extensions["timeout"]["read"] == 30


async def test_a_slot_action_that_got_no_answer_is_not_sent_twice(manager: FakeManager):
    manager.add("POST", "/api/games/7/slots/2/action", httpx.ReadTimeout("slow"))
    with pytest.raises(ManagerUnavailable):
        await manager.client.slot_action(USER_TOKEN, 7, 2, "release")
    assert len(manager.requests) == 1


async def test_a_hint_is_marked_with_a_put_to_its_numbers(manager: FakeManager):
    call = manager.client.set_hint_status(USER_TOKEN, 7, 2, 5, 77, 30)
    request, body = await sent(manager, call, "PUT", "/api/games/7/slots/2/hints/5/77", {"status": 30})
    assert body == {"status": 30} and request.extensions["timeout"]["read"] == 30


@pytest.mark.parametrize("status", ["stuck", None])
async def test_the_slot_status_is_a_put_with_a_note(manager: FakeManager, status):
    call = manager.client.set_slot_status(USER_TOKEN, 7, 2, status, "note")
    _, body = await sent(manager, call, "PUT", "/api/games/7/slots/2/status")
    assert body == {"status": status, "note": "note"}


async def test_claiming_is_a_post_without_a_body(manager: FakeManager):
    _, body = await sent(manager, manager.client.claim(USER_TOKEN, 7, 2), "POST", "/api/games/7/slots/2/claim")
    assert body is None


@pytest.mark.parametrize("to", ["bob", None])
async def test_offering_a_handover_is_a_put_to_a_username_or_to_anyone(manager: FakeManager, to):
    call = manager.client.offer_handover(USER_TOKEN, 7, 2, to, "note")
    _, body = await sent(manager, call, "PUT", "/api/games/7/slots/2/handover")
    assert body == {"to": to, "note": "note"}


async def test_ids_in_paths_are_numbers_only(manager: FakeManager):
    manager.add("POST", "/api/games/7/slots/2/claim", {})
    await manager.client.claim(USER_TOKEN, "7", 2)
    assert manager.requests[0].url.path == "/api/games/7/slots/2/claim"
    with pytest.raises(ValueError):
        await manager.client.claim(USER_TOKEN, "7/../x", 2)
