import discord
import httpx
import pytest
from fakes import (
    BOT_TOKEN,
    SECRETS,
    SITE_URL,
    USER_TOKEN,
    FakeInteraction,
    by_token,
    game_json,
    json_text,
    signup_json,
)

from apbot.autocomplete import game_choices, game_choices_public, game_choices_visible
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


# --- private games for linked users ------------------------------------------------------------------------------------
PUBLIC = game_json(1, "Friday Run")
PRIVATE = game_json(9, "Hidden Gem", visibility="participants")


def link(bot, user_id=1001):
    bot.links.save(user_id, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def two_views(manager, *, user=(PUBLIC, PRIVATE)):
    """The manager shows the bot's account the public game and the linked person's account that plus a private one."""
    manager.add("GET", "/api/games", by_token({BOT_TOKEN: [PUBLIC], USER_TOKEN: list(user)}))
    manager.add("GET", "/api/auth/status", {"site_url": SITE_URL})


async def run_games(bot, **kwargs) -> FakeInteraction:
    interaction = FakeInteraction(bot, **kwargs)
    await GamesCog(bot).games.callback(GamesCog(bot), interaction)
    return interaction


def sent_text(messages) -> str:
    return json_text([m["embed"] for m in messages])


def users_call_fails(manager, error: Exception):
    def answer(request):
        if request.headers["Authorization"] == f"Bearer {USER_TOKEN}":
            raise error
        return [PUBLIC]

    manager.add("GET", "/api/games", answer)


async def test_games_for_an_unlinked_caller_is_exactly_the_public_list(bot, manager):
    two_views(manager)
    await bot.cache.refresh()
    manager.requests.clear()
    interaction = await run_games(bot)
    [message] = interaction.messages
    assert "ephemeral" not in message and "Hidden Gem" not in sent_text([message])
    assert manager.requests == []


async def test_games_for_a_linked_caller_without_private_games_sends_one_public_message(bot, manager):
    link(bot)
    two_views(manager, user=(PUBLIC,))
    await bot.cache.refresh()
    interaction = await run_games(bot)
    [message] = interaction.messages
    assert "ephemeral" not in message
    assert manager.requests[-1].headers["Authorization"] == f"Bearer {USER_TOKEN}"


async def test_a_private_game_is_only_in_an_ephemeral_followup(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    interaction = await run_games(bot)
    public, private = interaction.messages
    assert "ephemeral" not in public and "Hidden Gem" not in sent_text([public])
    assert private["ephemeral"] is True
    assert private["embed"].title == "Private games you can see"
    assert "Hidden Gem" in private["embed"].description and "Friday Run" not in private["embed"].description
    assert all("Hidden Gem" not in sent_text([m]) for m in interaction.messages if not m.get("ephemeral"))
    assert not interaction.deleted_original


async def test_the_private_part_after_the_first_load_keeps_the_public_reply_public(bot, manager):
    link(bot)
    two_views(manager)  # the bot's cache is not loaded: /games defers publicly and loads it
    interaction = await run_games(bot)
    assert interaction.response.deferred and interaction.response.defer_kwargs == {}
    public, private = interaction.followup.sent
    assert "ephemeral" not in public and "Hidden Gem" not in sent_text([public])
    assert private["ephemeral"] is True and "Hidden Gem" in private["embed"].description


async def test_private_games_follow_the_callers_language(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    interaction = await run_games(bot, locale="de")
    assert interaction.messages[1]["embed"].title == "Private Spiele, die du sehen kannst"


async def test_a_finished_private_game_is_not_listed(bot, manager):
    link(bot)
    two_views(manager, user=(PUBLIC, game_json(9, "Old One", finished_at=5.0, outcome="finished")))
    await bot.cache.refresh()
    assert len((await run_games(bot)).messages) == 1


async def test_a_refused_token_marks_the_link_invalid_and_keeps_the_public_list(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    manager.add("GET", "/api/games", by_token({BOT_TOKEN: [PUBLIC]}))  # the user's token gets a 401
    interaction = await run_games(bot)
    public, problem = interaction.messages
    assert "Friday Run" in public["embed"].description and not interaction.deleted_original
    assert problem["ephemeral"] is True and "run /link again" in problem["embed"].description
    assert bot.links.get(1001).status == "invalid"


async def test_an_unreadable_token_keeps_the_public_list_and_the_link(bot, manager):
    from cryptography.fernet import Fernet

    from apbot.crypto import TokenCipher

    other = TokenCipher([Fernet.generate_key().decode()])
    bot.links.save(1001, 10, "alice", other.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
    two_views(manager)
    await bot.cache.refresh()
    manager.requests.clear()
    interaction = await run_games(bot)
    public, problem = interaction.messages
    assert "Friday Run" in public["embed"].description and not interaction.deleted_original
    assert problem["ephemeral"] is True and "cannot be read" in problem["embed"].description
    assert bot.links.get(1001).status == "active"
    assert manager.requests == []


async def test_an_unreachable_manager_for_the_private_part_keeps_the_public_list(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    users_call_fails(manager, httpx.ReadTimeout("slow"))
    interaction = await run_games(bot)
    public, problem = interaction.messages
    assert "Friday Run" in public["embed"].description and not interaction.deleted_original
    assert problem["ephemeral"] is True
    assert problem["embed"].description == "Your private games could not be loaded right now. Try again later."


async def test_the_could_not_be_loaded_message_follows_the_language(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    users_call_fails(manager, httpx.ConnectError("refused"))
    interaction = await run_games(bot, locale="fr")
    assert interaction.messages[1]["embed"].description.startswith("Tes parties privées")


async def test_the_second_games_call_within_a_minute_does_not_ask_the_manager_again(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    await run_games(bot)
    manager.requests.clear()
    interaction = await run_games(bot)
    assert manager.requests == [] and interaction.messages[1]["embed"].title == "Private games you can see"


# --- /game with a private game -------------------------------------------------------------------------------------------
def game_as(bot, game_id="9", **kwargs):
    interaction = FakeInteraction(bot, **kwargs)
    return interaction, GamesCog(bot).game.callback(GamesCog(bot), interaction, game_id)


async def test_a_public_game_is_shown_publicly_with_the_bot_token_even_to_a_linked_caller(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    manager.add("GET", "/api/games/1", game_json(1, "Friday Run"))
    interaction, call = game_as(bot, "1")
    await call
    [message] = interaction.messages
    assert interaction.response.defer_kwargs == {} and "ephemeral" not in message
    assert manager.requests[-1].headers["Authorization"] == f"Bearer {BOT_TOKEN}"


async def test_a_private_game_is_shown_ephemerally_with_the_callers_token(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    manager.add("GET", "/api/games/9", by_token({USER_TOKEN: game_json(9, "Hidden Gem", visibility="participants")}))
    interaction, call = game_as(bot, "9")
    await call
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    [message] = interaction.messages
    assert message["ephemeral"] is True
    assert message["embeds"][0].title == "Hidden Gem"
    assert message["view"].children[0].url == f"{SITE_URL}/#/games/9"
    assert manager.requests[-1].headers["Authorization"] == f"Bearer {USER_TOKEN}"
    text = json_text(message["embeds"])
    assert all(secret not in text for secret in (SECRETS["password"], SECRETS["server_password"], "webhook-token"))


async def test_a_private_game_for_an_unlinked_caller_is_not_found_via_the_bot_token(bot, manager):
    two_views(manager)
    await bot.cache.refresh()
    manager.add("GET", "/api/games/9", (404, {"detail": "Game not found"}))
    interaction, call = game_as(bot, "9")
    with pytest.raises(UserFacingError) as error:
        await call
    assert error.value.message("en") == "That game was not found. Pick one from the list."
    assert interaction.response.defer_kwargs == {}
    assert manager.requests[-1].headers["Authorization"] == f"Bearer {BOT_TOKEN}"


async def test_a_private_game_the_callers_account_does_not_see_is_not_found(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    manager.add("GET", "/api/games/9", (404, {"detail": "Game not found"}))
    with pytest.raises(UserFacingError) as error:
        await game_as(bot, "9")[1]
    assert error.value.message("en") == "That game was not found. Pick one from the list."


async def test_a_broken_link_falls_back_to_the_bot_token_path(bot, manager):
    link(bot)
    bot.links.mark_invalid(1001)
    two_views(manager)
    await bot.cache.refresh()
    manager.add("GET", "/api/games/9", (404, {"detail": "Game not found"}))
    interaction, call = game_as(bot, "9")
    with pytest.raises(UserFacingError):
        await call
    assert interaction.response.defer_kwargs == {}
    assert manager.requests[-1].headers["Authorization"] == f"Bearer {BOT_TOKEN}"


async def test_a_refused_token_on_a_private_game_marks_the_link_invalid(bot, manager):
    link(bot)
    two_views(manager)
    await bot.cache.refresh()
    manager.add("GET", "/api/games/9", (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError) as error:
        await game_as(bot, "9")[1]
    assert "run /link again" in error.value.message("en")
    assert bot.links.get(1001).status == "invalid"


# --- the commands that take a game suggest private ones ------------------------------------------------------------------
@pytest.mark.parametrize("command", ["game", "wake", "start", "stop", "restart"])
def test_every_command_that_takes_a_game_suggests_private_games_too(bot, command):
    assert getattr(GamesCog(bot), command)._params["game"].autocomplete is game_choices_visible
