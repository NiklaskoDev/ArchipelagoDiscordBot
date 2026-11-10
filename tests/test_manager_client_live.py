import httpx
import pytest
from fakes import USER_TOKEN, FakeManager, activity_json, entry

from apbot.manager.client import TRACKER_TIMEOUT
from apbot.manager.errors import Conflict, ManagerUnavailable


async def test_activity_asks_for_the_entries_after_a_number_with_the_given_token(manager: FakeManager):
    manager.add("GET", "/api/games/7/activity", activity_json([entry(5, "goal", player="Alice")]))
    answer = await manager.client.activity(USER_TOKEN, 7, 4)
    request = manager.requests[0]
    assert answer["last"] == 5
    assert request.url.params["after"] == "4"
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert USER_TOKEN not in str(request.url)


async def test_activity_never_asks_for_a_negative_number(manager: FakeManager):
    manager.add("GET", "/api/games/7/activity", activity_json([]))
    await manager.client.activity(USER_TOKEN, 7, -3)
    assert manager.requests[0].url.params["after"] == "0"


async def test_activity_is_a_read_that_is_retried_when_the_manager_is_busy(manager: FakeManager):
    manager.add("GET", "/api/games/7/activity", (503, {"detail": "busy"}), activity_json([]))
    assert (await manager.client.activity(USER_TOKEN, 7, 0))["last"] == 0
    assert len(manager.requests) == 2


async def test_tracker_reads_the_slots_tracker_with_a_longer_timeout(manager: FakeManager):
    manager.add("GET", "/api/games/7/slots/2/tracker", {"hints": []})
    assert await manager.client.tracker(USER_TOKEN, 7, 2) == {"hints": []}
    assert manager.requests[0].extensions["timeout"]["read"] == TRACKER_TIMEOUT.read == 30


async def test_accepting_a_handover_is_one_post_with_the_users_token(manager: FakeManager):
    manager.add("POST", "/api/games/7/slots/2/handover/accept", {"id": 7})
    assert await manager.client.accept_handover(USER_TOKEN, 7, 2) == {"id": 7}
    request = manager.requests[0]
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"


async def test_a_handover_that_got_no_answer_is_not_accepted_twice(manager: FakeManager):
    manager.add("POST", "/api/games/7/slots/2/handover/accept", httpx.ReadTimeout("slow"))
    with pytest.raises(ManagerUnavailable):
        await manager.client.accept_handover(USER_TOKEN, 7, 2)
    assert len(manager.requests) == 1


async def test_declining_a_handover_is_a_delete(manager: FakeManager):
    manager.add("DELETE", "/api/games/7/slots/2/handover", {"declined": True})
    assert await manager.client.cancel_handover(USER_TOKEN, 7, 2) == {"declined": True}
    assert manager.requests[0].method == "DELETE"


async def test_a_conflict_on_accepting_keeps_the_managers_words(manager: FakeManager):
    manager.add("POST", "/api/games/7/slots/2/handover/accept", (409, {"detail": "This slot is no longer being handed over"}))
    with pytest.raises(Conflict) as error:
        await manager.client.accept_handover(USER_TOKEN, 7, 2)
    assert error.value.detail == "This slot is no longer being handed over"
