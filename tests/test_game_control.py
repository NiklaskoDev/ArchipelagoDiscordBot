import pytest
from fakes import BOT_TOKEN, USER_TOKEN, FakeInteraction, game_json

from apbot.cogs.games import GamesCog
from apbot.errors import MissingRoleError, NotLinkedError, UserFacingError
from apbot.manager.errors import Conflict, Forbidden

GUILD = 5000
ORGANIZER_ROLE = 777


def link(bot, user_id=1001):
    bot.links.save(user_id, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def asker(bot, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, guild_id=GUILD, **kwargs)


def run(bot, command: str, interaction, game="7"):
    cog = GamesCog(bot)
    return getattr(cog, command).callback(cog, interaction, game)


# --- the four commands act as the person -------------------------------------------------------------------------------
@pytest.mark.parametrize("command", ["wake", "start", "stop", "restart"])
async def test_the_call_goes_to_the_managers_endpoint_with_the_users_token_not_the_bots(bot, manager, command):
    link(bot)
    manager.add("POST", f"/api/games/7/{command}", game_json(7, "Friday Run", state="running"))
    await run(bot, command, asker(bot))
    [request] = manager.requests
    assert (request.method, request.url.path) == ("POST", f"/api/games/7/{command}")
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert BOT_TOKEN not in str(request.headers)


@pytest.mark.parametrize(("state", "text"), [("running", "Running"), ("stopped", "Stopped"), ("sleeping", "Sleeping")])
async def test_the_reply_is_private_and_names_the_state_after_the_call(bot, manager, state, text):
    link(bot)
    manager.add("POST", "/api/games/7/wake", game_json(7, "Friday Run", state=state))
    interaction = asker(bot)
    await run(bot, "wake", interaction)
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    [message] = interaction.messages
    assert message["ephemeral"] is True
    assert (message["embed"].title, message["embed"].description) == ("Friday Run", text)


async def test_the_reply_is_in_the_users_language(bot, manager):
    link(bot)
    manager.add("POST", "/api/games/7/stop", game_json(7, state="stopped"))
    interaction = asker(bot, locale="de")
    await run(bot, "stop", interaction)
    assert interaction.messages[0]["embed"].description == "Gestoppt"


async def test_the_reply_never_contains_a_password(bot, manager):
    link(bot)
    manager.add("POST", "/api/games/7/start", game_json(7))
    interaction = asker(bot)
    await run(bot, "start", interaction)
    assert "s3cret" not in str(interaction.messages[0]["embed"].to_dict())


# --- the manager says no -----------------------------------------------------------------------------------------------
async def test_a_409_is_shown_privately_with_the_managers_words(bot, manager):
    link(bot)
    manager.add("POST", "/api/games/7/start", (409, {"detail": "The game is already running"}))
    interaction = asker(bot)
    with pytest.raises(Conflict) as error:
        await run(bot, "start", interaction)
    await bot.on_app_command_error(interaction, _wrapped(error.value))
    shown = interaction.followup.sent[-1]
    assert shown["ephemeral"] is True
    assert shown["embed"].description == "The manager answered: The game is already running"


async def test_a_403_from_the_manager_is_shown_as_it_is(bot, manager):
    link(bot)
    manager.add("POST", "/api/games/7/stop", (403, {"detail": "You are not allowed to control this game"}))
    with pytest.raises(Forbidden) as error:
        await run(bot, "stop", asker(bot))
    assert error.value.detail == "You are not allowed to control this game"


async def test_a_game_the_manager_does_not_show_the_user_asks_to_pick_from_the_list(bot, manager):
    link(bot)
    manager.add("POST", "/api/games/7/wake", (404, {"detail": "Game not found"}))
    with pytest.raises(UserFacingError) as error:
        await run(bot, "wake", asker(bot))
    assert error.value.message("en") == "That game was not found. Pick one from the list."


async def test_a_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    manager.add("POST", "/api/games/7/start", (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError) as error:
        await run(bot, "start", asker(bot))
    assert "run /link again" in error.value.message("en")
    assert bot.links.get(1001).status == "invalid"


async def test_a_read_only_token_is_explained(bot, manager):
    link(bot)
    manager.add("POST", "/api/games/7/start", (403, {"detail": "This API token can only read"}))
    with pytest.raises(UserFacingError) as error:
        await run(bot, "start", asker(bot))
    assert "can only read" in error.value.message("en")
    assert bot.links.get(1001).status == "active"


async def test_a_change_that_gets_no_answer_is_not_repeated(bot, manager):
    import httpx

    from apbot.manager.errors import ManagerUnavailable

    link(bot)
    manager.add("POST", "/api/games/7/restart", httpx.ReadTimeout("slow"))
    with pytest.raises(ManagerUnavailable):
        await run(bot, "restart", asker(bot))
    assert len(manager.requests) == 1


# --- not linked, bad input ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("command", ["wake", "start", "stop", "restart"])
async def test_without_a_link_nothing_is_asked_and_nothing_shows_as_thinking(bot, manager, command):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await run(bot, command, interaction)
    assert manager.requests == [] and not interaction.response.deferred


async def test_a_game_id_that_is_not_a_number_never_reaches_the_manager(bot, manager):
    link(bot)
    with pytest.raises(UserFacingError):
        await run(bot, "start", asker(bot), game="abc")
    assert manager.requests == []


# --- the organizer role ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("command", ["start", "stop", "restart"])
async def test_with_an_organizer_role_set_others_are_refused_before_the_manager_is_asked(bot, manager, command):
    link(bot)
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    with pytest.raises(MissingRoleError):
        await run(bot, command, asker(bot, roles=[1]))
    assert manager.requests == []


@pytest.mark.parametrize("command", ["start", "stop", "restart"])
async def test_the_organizer_role_holder_may(bot, manager, command):
    link(bot)
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    manager.add("POST", f"/api/games/7/{command}", game_json(7))
    await run(bot, command, asker(bot, roles=[ORGANIZER_ROLE]))
    assert len(manager.requests) == 1


@pytest.mark.parametrize("command", ["start", "stop", "restart"])
async def test_without_an_organizer_role_set_anyone_linked_may_try_and_the_manager_decides(bot, manager, command):
    link(bot)
    manager.add("POST", f"/api/games/7/{command}", game_json(7))
    await run(bot, command, asker(bot))
    assert len(manager.requests) == 1


async def test_wake_is_not_limited_to_organizers(bot, manager):
    link(bot)
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    manager.add("POST", "/api/games/7/wake", game_json(7))
    await run(bot, "wake", asker(bot, roles=[1]))
    assert len(manager.requests) == 1


async def test_wake_works_in_a_direct_message(bot, manager):
    link(bot)
    manager.add("POST", "/api/games/7/wake", game_json(7))
    interaction = FakeInteraction(bot, user_id=1001, guild_id=None)
    await run(bot, "wake", interaction)
    assert len(interaction.messages) == 1


def _wrapped(error: Exception):
    from types import SimpleNamespace

    from discord import app_commands

    return app_commands.CommandInvokeError(SimpleNamespace(name="start"), error)
