import pytest
from fakes import USER_TOKEN, FakeManager, game_json

from apbot.manager.errors import ManagerUnavailable
from apbot.manager.views import parse_games
from apbot.services.my_games import MAX_CACHED, MY_GAMES_TTL, MyGames, private_games


def games(*raw):
    return parse_games(list(raw))


# --- MyGames -----------------------------------------------------------------------------------------------------------
async def test_the_list_is_kept_for_a_minute_per_person(manager: FakeManager):
    clock = [0.0]
    my_games = MyGames(manager.client, now=lambda: clock[0])
    manager.add("GET", "/api/games", [game_json(1, "Friday Run"), game_json(2, "Secret", visibility="participants")])
    first = await my_games.games(1001, USER_TOKEN)
    assert [g.name for g in first] == ["Friday Run", "Secret"]
    assert await my_games.games(1001, USER_TOKEN) == first
    assert len(manager.requests) == 1
    await my_games.games(2002, USER_TOKEN)  # someone else's list is never shared
    assert len(manager.requests) == 2
    clock[0] += MY_GAMES_TTL
    await my_games.games(1001, USER_TOKEN)
    assert len(manager.requests) == 3


async def test_the_list_is_asked_with_the_given_token_and_quick_passes_through(manager: FakeManager):
    manager.add("GET", "/api/games", [])
    await MyGames(manager.client).games(1001, USER_TOKEN, quick=True)
    [request] = manager.requests
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert request.extensions["timeout"]["read"] == 2


async def test_forget_drops_the_entry(manager: FakeManager):
    my_games = MyGames(manager.client)
    manager.add("GET", "/api/games", [game_json(1)])
    await my_games.games(1001, USER_TOKEN)
    assert my_games.cached(1001) is not None
    my_games.forget(1001)
    assert my_games.cached(1001) is None
    await my_games.games(1001, USER_TOKEN)
    assert len(manager.requests) == 2


def test_cached_is_none_when_nothing_is_kept(manager: FakeManager):
    assert MyGames(manager.client).cached(1001) is None
    MyGames(manager.client).forget(1001)  # forgetting nothing is fine


async def test_a_manager_error_caches_nothing(manager: FakeManager):
    my_games = MyGames(manager.client)
    manager.add("GET", "/api/games", (503, {"detail": "busy"}), [game_json(1)])
    with pytest.raises(ManagerUnavailable):
        await my_games.games(1001, USER_TOKEN, quick=True)
    assert my_games.cached(1001) is None
    assert len(await my_games.games(1001, USER_TOKEN, quick=True)) == 1


async def test_an_answer_that_is_not_a_list_raises_and_caches_nothing(manager: FakeManager):
    my_games = MyGames(manager.client)
    manager.add("GET", "/api/games", {"not": "a list"})
    with pytest.raises(TypeError):
        await my_games.games(1001, USER_TOKEN)
    assert my_games.cached(1001) is None


async def test_at_most_500_people_are_kept(manager: FakeManager):
    my_games = MyGames(manager.client)
    manager.add("GET", "/api/games", [])
    for owner in range(MAX_CACHED + 1):
        await my_games.games(owner, USER_TOKEN)
    assert sum(my_games.cached(owner) is not None for owner in range(MAX_CACHED + 1)) == MAX_CACHED


# --- private_games -----------------------------------------------------------------------------------------------------
def test_private_games_are_the_ones_missing_from_the_public_list_in_their_order():
    public = games(game_json(1), game_json(3))
    mine = games(game_json(5, "Five"), game_json(1), game_json(4, "Four"), game_json(3))
    assert [g.name for g in private_games(mine, public)] == ["Five", "Four"]


def test_nothing_is_private_when_everything_is_public():
    both = games(game_json(1), game_json(2))
    assert private_games(both, both) == []
    assert private_games([], both) == []


def test_visibility_is_not_what_decides():
    # A game the bot has not seen yet counts as private even when the manager says "all" (the safe direction).
    assert [g.id for g in private_games(games(game_json(8, visibility="all")), [])] == [8]
    # ... and a game in the public list is never private, whatever the manager says about it.
    assert private_games(games(game_json(8, visibility="participants")), games(game_json(8))) == []
