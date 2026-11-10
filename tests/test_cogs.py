import discord
import httpx
import pytest
from fakes import BOT_TOKEN, SECRETS, SITE_URL, FakeInteraction, game_json, json_text, signup_json

from apbot.autocomplete import game_choices, game_choices_public
from apbot.cogs.games import GamesCog, parse_game_id
from apbot.cogs.signups import SignupsCog
from apbot.errors import UserFacingError
from apbot.manager.errors import ManagerUnavailable
from apbot.manager.views import parse_games


def loaded_cache(bot, manager, *games):
    manager.add("GET", "/api/games", list(games))
    manager.add("GET", "/api/auth/status", {"site_url": SITE_URL})
    return bot.cache.refresh()


# --- /games ------------------------------------------------------------------------------------------------------------
async def test_games_replies_publicly_from_the_cache_without_asking_the_manager(bot, manager):
    await loaded_cache(bot, manager, game_json(1, "Friday Run"), game_json(2, "Done", finished_at=5.0, outcome="finished"))
    manager.requests.clear()
    interaction = FakeInteraction(bot)
    await GamesCog(bot).games.callback(GamesCog(bot), interaction)
    [message] = interaction.messages
    assert "ephemeral" not in message
    assert not interaction.response.deferred
    assert "Friday Run" in message["embed"].description and "Done" not in message["embed"].description
    assert manager.requests == []


async def test_games_asks_the_manager_once_when_nothing_is_loaded_yet(bot, manager):
    manager.add("GET", "/api/games", [game_json(1, "Friday Run")])
    manager.add("GET", "/api/auth/status", {"site_url": None})
    interaction = FakeInteraction(bot)
    await GamesCog(bot).games.callback(GamesCog(bot), interaction)
    assert interaction.response.deferred
    assert "Friday Run" in interaction.followup.sent[0]["embed"].description


async def test_games_reply_follows_the_users_language(bot, manager):
    await loaded_cache(bot, manager, game_json(1))
    interaction = FakeInteraction(bot, locale="de")
    await GamesCog(bot).games.callback(GamesCog(bot), interaction)
    assert interaction.messages[0]["embed"].title == "Spiele"


async def test_games_with_an_unreachable_manager_raises_for_the_error_handler(bot, manager):
    manager.add("GET", "/api/games", httpx.ConnectError("refused"))
    with pytest.raises(ManagerUnavailable):
        await GamesCog(bot).games.callback(GamesCog(bot), FakeInteraction(bot))


# --- /game -------------------------------------------------------------------------------------------------------------
async def test_game_shows_detail_with_the_bot_token_and_a_manager_button(bot, manager):
    manager.add("GET", "/api/games/7", game_json(7, "Friday Run"))
    bot.cache.set_site_url(SITE_URL)
    interaction = FakeInteraction(bot)
    await GamesCog(bot).game.callback(GamesCog(bot), interaction, "7")
    [message] = interaction.messages
    assert interaction.response.deferred and "ephemeral" not in message
    assert message["embeds"][0].title == "Friday Run"
    assert message["view"].children[0].url == f"{SITE_URL}/#/games/7"
    assert manager.requests[0].headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    text = json_text(message["embeds"])
    assert all(secret not in text for secret in (SECRETS["password"], SECRETS["server_password"], "webhook-token"))


async def test_game_without_a_known_site_url_has_no_button(bot, manager):
    manager.add("GET", "/api/games/7", game_json(7))
    interaction = FakeInteraction(bot)
    await GamesCog(bot).game.callback(GamesCog(bot), interaction, "7")
    assert "view" not in interaction.messages[0]


async def test_game_that_does_not_exist_asks_to_pick_from_the_list(bot, manager):
    manager.add("GET", "/api/games/9", (404, {"detail": "Game not found"}))
    with pytest.raises(UserFacingError) as error:
        await GamesCog(bot).game.callback(GamesCog(bot), FakeInteraction(bot), "9")
    assert error.value.message("en") == "That game was not found. Pick one from the list."
    assert error.value.message("de").startswith("Dieses Spiel wurde nicht gefunden")


@pytest.mark.parametrize("value", ["abc", "", "1 2", "-3", "12.5", "1234567890", "١٢"])
def test_game_id_must_be_a_plain_number(value):
    with pytest.raises(UserFacingError) as error:
        parse_game_id(value)
    assert error.value.message("en") == "Pick a game from the list"


async def test_invalid_game_id_never_reaches_the_manager(bot, manager):
    with pytest.raises(UserFacingError):
        await GamesCog(bot).game.callback(GamesCog(bot), FakeInteraction(bot), "not-a-number")
    assert manager.requests == []


def test_parse_game_id_accepts_a_number_with_spaces_around():
    assert parse_game_id(" 42 ") == 42


# --- autocomplete ------------------------------------------------------------------------------------------------------
def test_autocomplete_filters_by_name_and_returns_the_id():
    games = parse_games([game_json(1, "Friday Run"), game_json(2, "Sunday Async"), game_json(3, "Other")])
    assert [(c.name, c.value) for c in game_choices(games, "RUN")] == [("Friday Run", "1")]
    assert [c.value for c in game_choices(games, "")] == ["3", "2", "1"]  # same age: the higher id is newer


def test_autocomplete_lists_running_games_first_then_finished_ones_last():
    games = parse_games(
        [
            game_json(1, "done", finished_at=9.0, outcome="finished"),
            game_json(2, "stopped", state="stopped"),
            game_json(3, "running", state="running"),
        ]
    )
    assert [c.name for c in game_choices(games, "")] == ["running", "stopped", "done"]


def test_autocomplete_returns_at_most_25_choices_with_names_up_to_100_characters():
    games = parse_games([game_json(i, "n" * 150) for i in range(1, 60)])
    choices = game_choices(games, "")
    assert len(choices) == 25
    assert all(len(c.name) <= 100 for c in choices)


async def test_public_autocomplete_reads_the_cache(bot, manager):
    await loaded_cache(bot, manager, game_json(5, "Friday Run"))
    choices = await game_choices_public(FakeInteraction(bot), "fri")
    assert [(c.name, c.value) for c in choices] == [("Friday Run", "5")]
    assert isinstance(choices[0], discord.app_commands.Choice)


async def test_autocomplete_before_the_first_load_is_empty(bot):
    assert await game_choices_public(FakeInteraction(bot), "") == []


# --- /signups ----------------------------------------------------------------------------------------------------------
async def test_signups_lists_open_rounds_publicly_with_the_bot_token(bot, manager):
    manager.add("GET", "/api/signups", [signup_json(1, "Later", deadline=2000.0), signup_json(2, "Closed", open=False),
                                        signup_json(3, "Soon", deadline=1000.0)])
    interaction = FakeInteraction(bot)
    await SignupsCog(bot).signups.callback(SignupsCog(bot), interaction)
    [message] = interaction.messages
    assert "ephemeral" not in message
    lines = message["embed"].description.split("\n")
    assert [line.split(" — ")[0] for line in lines] == ["**Soon**", "**Later**"]
    assert manager.requests[0].headers["Authorization"] == f"Bearer {BOT_TOKEN}"


async def test_signups_without_open_rounds_says_so(bot, manager):
    manager.add("GET", "/api/signups", [signup_json(1, open=False)])
    interaction = FakeInteraction(bot)
    await SignupsCog(bot).signups.callback(SignupsCog(bot), interaction)
    assert "No sign-up rounds are open" in interaction.messages[0]["embed"].description

