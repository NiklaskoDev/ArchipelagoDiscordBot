import pytest
from fakes import USER_TOKEN, config_json, friend_json, friends_json

from apbot.errors import UserFacingError
from apbot.manager.errors import ManagerUnavailable
from apbot.services.friends import FRIENDS_TTL, FriendDirectory, FriendsUnsupported


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def directory(manager, clock) -> FriendDirectory:
    return FriendDirectory(manager.client, now=clock)


async def test_the_overview_is_asked_with_the_persons_token_and_kept_for_a_minute(manager, directory, clock):
    manager.add("GET", "/api/friends", friends_json(friends=[friend_json(20, "bob")]))
    first = await directory.overview(1001, USER_TOKEN)
    assert [f.username for f in first.friends] == ["bob"]
    clock.now += FRIENDS_TTL - 1
    assert await directory.overview(1001, USER_TOKEN) is first
    [request] = manager.requests
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    clock.now += 2
    await directory.overview(1001, USER_TOKEN)
    assert len(manager.requests) == 2 and FRIENDS_TTL == 60.0


async def test_each_person_has_their_own_overview(manager, directory):
    manager.add("GET", "/api/friends", friends_json(friends=[friend_json(20, "bob")]))
    await directory.overview(1001, USER_TOKEN)
    await directory.overview(2002, "apm_tokenOfSomeoneElse")
    assert [r.headers["Authorization"] for r in manager.requests] == [f"Bearer {USER_TOKEN}", "Bearer apm_tokenOfSomeoneElse"]


async def test_forgetting_makes_the_next_look_ask_again(manager, directory):
    manager.add("GET", "/api/friends", friends_json())
    await directory.overview(1001, USER_TOKEN)
    directory.forget(1001)
    directory.forget(9999)  # someone never seen is no error
    await directory.overview(1001, USER_TOKEN)
    assert len(manager.requests) == 2


async def test_an_older_manager_without_friends_gives_the_clear_message_and_nothing_is_kept(manager, directory):
    with pytest.raises(FriendsUnsupported) as error:  # the fake answers an unknown route with 404
        await directory.overview(1001, USER_TOKEN)
    assert isinstance(error.value, UserFacingError)
    assert error.value.message("en") == "This manager has no friends feature yet. Ask an admin to update the Archipelago Manager."
    assert "Archipelago Manager" in error.value.message("de")
    manager.add("GET", "/api/friends", friends_json())
    await directory.overview(1001, USER_TOKEN)  # the failure was not cached: once the manager is updated it works
    assert len(manager.requests) == 2


async def test_a_manager_that_does_not_answer_raises_and_caches_nothing(manager, directory):
    manager.add("GET", "/api/friends", (503, {"detail": "busy"}))
    with pytest.raises(ManagerUnavailable):
        await directory.overview(1001, USER_TOKEN, quick=True)
    manager.add("GET", "/api/friends", friends_json())
    await directory.overview(1001, USER_TOKEN)
    assert len(manager.requests) == 2


async def test_an_answer_the_bot_does_not_know_is_a_manager_problem_not_a_crash(manager, directory):
    manager.add("GET", "/api/friends", {"friends": "bob"})
    with pytest.raises(ManagerUnavailable):
        await directory.overview(1001, USER_TOKEN)
    manager.add("GET", "/api/friends", friends_json())
    assert (await directory.overview(1001, USER_TOKEN)).is_empty


# --- the library: own configs and the ones friends share -----------------------------------------------------------------
LIBRARY = [
    config_json(40, "alice.yaml", user_id=10, owner="alice"),
    config_json(77, "bob.yaml", user_id=20, owner="bob", shared=True, share_with="friends"),
]


async def test_the_library_is_asked_with_friends_one_by_the_persons_token_and_kept_for_a_minute(manager, directory, clock):
    manager.add("GET", "/api/configs", LIBRARY)
    first = await directory.library(1001, USER_TOKEN)
    assert [(c.id, c.owner) for c in first] == [(40, "alice"), (77, "bob")]
    clock.now += FRIENDS_TTL - 1
    assert await directory.library(1001, USER_TOKEN) is first
    [request] = manager.requests
    assert (request.url.path, request.url.query) == ("/api/configs", b"friends=1")
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}" and request.extensions["timeout"]["read"] == 2
    clock.now += 2
    await directory.library(1001, USER_TOKEN)
    assert len(manager.requests) == 2


async def test_a_library_that_could_not_be_read_is_not_kept(manager, directory):
    manager.add("GET", "/api/configs", (503, {"detail": "busy"}))
    with pytest.raises(ManagerUnavailable):
        await directory.library(1001, USER_TOKEN)
    assert directory.cached_library(1001) is None
    manager.add("GET", "/api/configs", LIBRARY)
    await directory.library(1001, USER_TOKEN)
    assert len(manager.requests) == 2


async def test_the_last_library_can_be_looked_at_without_asking_and_forgetting_it_asks_again(manager, directory, clock):
    assert directory.cached_library(1001) is None
    manager.add("GET", "/api/configs", LIBRARY)
    kept = await directory.library(1001, USER_TOKEN)
    assert directory.cached_library(1001) is kept
    directory.forget_library(1001)
    directory.forget_library(9999)  # someone never seen is no error
    assert directory.cached_library(1001) is None
    await directory.library(1001, USER_TOKEN)
    assert len(manager.requests) == 2
    clock.now += FRIENDS_TTL + 1
    assert directory.cached_library(1001) is None


async def test_a_change_of_friends_also_drops_the_library_because_it_holds_their_configs(manager, directory):
    manager.add("GET", "/api/configs", LIBRARY)
    await directory.library(1001, USER_TOKEN)
    directory.forget(1001)
    assert directory.cached_library(1001) is None
    await directory.library(1001, USER_TOKEN)
    assert len(manager.requests) == 2


async def test_forgetting_the_library_keeps_the_friend_overview(manager, directory):
    manager.add("GET", "/api/friends", friends_json())
    await directory.overview(1001, USER_TOKEN)
    directory.forget_library(1001)
    await directory.overview(1001, USER_TOKEN)
    assert len(manager.requests) == 1
