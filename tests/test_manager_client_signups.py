import json

import pytest
from fakes import USER_TOKEN, FakeManager

from apbot.manager.errors import Conflict, ManagerUnavailable, TooManyRequests


async def sent(manager: FakeManager, call, method, path, answer=None):
    manager.add(method, path, answer or {"ok": True})
    await call
    [request] = manager.requests
    assert (request.method, request.url.path) == (method, path)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}" and USER_TOKEN not in str(request.url)
    return request, json.loads(request.content) if request.content else None


async def test_a_round_is_read_with_its_number(manager: FakeManager):
    await sent(manager, manager.client.signup(USER_TOKEN, 3), "GET", "/api/signups/3")


async def test_creating_a_round_posts_its_name_description_deadline_and_slots(manager: FakeManager):
    call = manager.client.create_signup(USER_TOKEN, "Spring", "About it", 1_900_000_000.5, 3)
    _, body = await sent(manager, call, "POST", "/api/signups")
    assert body == {"name": "Spring", "description": "About it", "deadline": 1_900_000_000.5, "slots_per_user": 3}


async def test_a_round_without_deadline_or_description_sends_nulls(manager: FakeManager):
    _, body = await sent(manager, manager.client.create_signup(USER_TOKEN, "Spring", None, None, 1), "POST", "/api/signups")
    assert body == {"name": "Spring", "description": None, "deadline": None, "slots_per_user": 1}


@pytest.mark.parametrize(("method", "action"), [("close_signup", "close"), ("test_signup", "test")])
async def test_closing_and_testing_a_round_are_posts_without_a_body(manager: FakeManager, method, action):
    call = getattr(manager.client, method)(USER_TOKEN, 3)
    _, body = await sent(manager, call, "POST", f"/api/signups/3/{action}")
    assert body is None


async def test_entering_a_config_posts_its_id(manager: FakeManager):
    _, body = await sent(manager, manager.client.add_entry(USER_TOKEN, 3, 41), "POST", "/api/signups/3/entries")
    assert body == {"config_id": 41}


async def test_withdrawing_an_entry_is_a_delete(manager: FakeManager):
    _, body = await sent(manager, manager.client.remove_entry(USER_TOKEN, 3, 9), "DELETE", "/api/signups/3/entries/9")
    assert body is None


async def test_configs_are_listed_and_created_by_text(manager: FakeManager):
    await sent(manager, manager.client.list_configs(USER_TOKEN), "GET", "/api/configs", [])
    manager.requests.clear()
    call = manager.client.create_config(USER_TOKEN, "alice.yaml", "name: Alice")
    _, body = await sent(manager, call, "POST", "/api/configs/text")
    assert body == {"name": "alice.yaml", "content": "name: Alice"}


async def test_a_check_posts_the_text_and_waits_up_to_200_seconds(manager: FakeManager):
    call = manager.client.check_config(USER_TOKEN, "name: Alice")
    request, body = await sent(manager, call, "POST", "/api/configs/check")
    assert body == {"content": "name: Alice"} and request.extensions["timeout"]["read"] == 200


async def test_a_check_that_the_manager_refuses_because_one_runs_already_is_a_429(manager: FakeManager):
    manager.add("POST", "/api/configs/check", (429, {"detail": "Your last check is not done yet."}))
    with pytest.raises(TooManyRequests):
        await manager.client.check_config(USER_TOKEN, "x")
    assert len(manager.requests) == 1  # never repeated


CHANGES = {
    "create": (lambda c: c.create_signup(USER_TOKEN, "n", None, None, 1), "POST", "/api/signups"),
    "add": (lambda c: c.add_entry(USER_TOKEN, 3, 4), "POST", "/api/signups/3/entries"),
    "remove": (lambda c: c.remove_entry(USER_TOKEN, 3, 4), "DELETE", "/api/signups/3/entries/4"),
    "close": (lambda c: c.close_signup(USER_TOKEN, 3), "POST", "/api/signups/3/close"),
    "test": (lambda c: c.test_signup(USER_TOKEN, 3), "POST", "/api/signups/3/test"),
    "create_config": (lambda c: c.create_config(USER_TOKEN, "a.yaml", "x"), "POST", "/api/configs/text"),
    "check": (lambda c: c.check_config(USER_TOKEN, "x"), "POST", "/api/configs/check"),
}


@pytest.mark.parametrize("name", CHANGES)
async def test_changes_are_never_sent_twice(manager: FakeManager, name):
    make, method, path = CHANGES[name]
    manager.add(method, path, (503, {"detail": "busy"}), {"ok": True})
    with pytest.raises(ManagerUnavailable):
        await make(manager.client)
    assert len(manager.requests) == 1


async def test_a_conflict_keeps_the_managers_words(manager: FakeManager):
    manager.add("POST", "/api/signups/3/entries", (409, {"detail": "Each player can enter 1 slot in this round."}))
    with pytest.raises(Conflict) as error:
        await manager.client.add_entry(USER_TOKEN, 3, 4)
    assert error.value.detail == "Each player can enter 1 slot in this round."


READS = {
    "rounds": (lambda c, quick: c.list_signups(USER_TOKEN, quick=quick), "/api/signups"),
    "configs": (lambda c, quick: c.list_configs(USER_TOKEN, quick=quick), "/api/configs"),
    "round": (lambda c, quick: c.signup(USER_TOKEN, 3, quick=quick), "/api/signups/3"),
}


@pytest.mark.parametrize("name", READS)
async def test_reads_for_suggestions_give_up_after_two_seconds_and_are_not_retried(manager: FakeManager, name):
    read, path = READS[name]
    manager.add("GET", path, (503, {"detail": "busy"}), {})
    with pytest.raises(ManagerUnavailable):
        await read(manager.client, True)
    assert len(manager.requests) == 1 and manager.requests[0].extensions["timeout"]["read"] == 2


@pytest.mark.parametrize("name", READS)
async def test_a_plain_read_is_retried_when_the_manager_is_busy(manager: FakeManager, name):
    read, path = READS[name]
    manager.add("GET", path, (503, {"detail": "busy"}), {})
    await read(manager.client, False)
    assert len(manager.requests) == 2
