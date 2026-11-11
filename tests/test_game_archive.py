import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from discord import app_commands
from fakes import BOT_TOKEN, USER_TOKEN, FakeInteraction, game_json

from apbot.cogs.games import GamesCog
from apbot.errors import MissingRoleError, NotLinkedError, UserFacingError
from apbot.manager.errors import Conflict, Forbidden, ManagerUnavailable
from apbot.ui.style import SUCCESS

GUILD = 5000
ORGANIZER_ROLE = 777
GAME = "/api/games/7"
ARCHIVE = "/api/games/7/archive"


def link(bot, user_id=1001):
    bot.links.save(user_id, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def asker(bot, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, guild_id=GUILD, **kwargs)


def archive(bot, interaction, action="archive", game="7"):
    cog = GamesCog(bot)
    return cog.game_archive.callback(cog, interaction, action, game)


def delete(bot, interaction, game="7"):
    cog = GamesCog(bot)
    return cog.game_delete.callback(cog, interaction, game)


def requests_to(manager, method, path) -> list[httpx.Request]:
    return [r for r in manager.requests if (r.method, r.url.path) == (method, path)]


def mine(**overrides) -> dict:
    """Game 7 as its organizer sees it."""
    defaults = {"name": "Friday Run", "state": "running", "can_manage": True, "can_archive": True}
    return game_json(7, **{**defaults, **overrides})


def in_archive(**overrides) -> dict:
    defaults = {"state": "stopped", "finished_at": 1_700_000_500.0, "outcome": "aborted"}
    return mine(**{**defaults, **overrides})


async def ask(command, bot, interaction, *args):
    """Starts a command that asks for confirmation; returns its task and the question once it is shown."""
    task = asyncio.create_task(command(bot, interaction, *args))
    for _ in range(100):
        if interaction.followup.sent or task.done():
            break
        await asyncio.sleep(0)
    return task


async def answer(task, interaction, button: int):
    click = FakeInteraction(user_id=1001)
    await interaction.followup.sent[-1]["view"].children[button].callback(click)
    await task
    return click


# --- /game_archive: moving a game into the archive ------------------------------------------------------------------------------
async def test_archiving_asks_first_then_moves_the_game_as_the_person(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    manager.add("POST", ARCHIVE, in_archive())
    interaction = asker(bot)
    task = await ask(archive, bot, interaction)
    [question] = interaction.followup.sent
    assert question["ephemeral"] is True
    assert question["embed"].description == (
        "Move **Friday Run** to the archive? Its server stops. It is listed as finished if every player reached "
        "their goal, otherwise as aborted."
    )
    assert [b.label for b in question["view"].children] == ["Move to archive", "Cancel"]
    assert question["view"].children[0].style.name == "primary"
    assert requests_to(manager, "POST", ARCHIVE) == []  # nothing before the person confirms
    await answer(task, interaction, 0)
    [request] = requests_to(manager, "POST", ARCHIVE)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert json.loads(request.content) == {"archived": True}
    assert request.extensions["timeout"]["read"] == 60
    [edit] = interaction.original_edits
    assert edit["embed"].title == "Friday Run"
    assert edit["embed"].description == "Stopped\n**Friday Run** is in the archive (aborted)" and edit["view"] is None


async def test_a_finished_game_is_reported_as_finished(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    manager.add("POST", ARCHIVE, in_archive(outcome="finished"))
    interaction = asker(bot)
    await answer(await ask(archive, bot, interaction), interaction, 0)
    assert interaction.original_edits[0]["embed"].description.endswith("is in the archive (finished)")


async def test_the_defer_and_the_question_are_private(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    interaction = asker(bot)
    task = await ask(archive, bot, interaction)
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    interaction.followup.sent[-1]["view"].stop()
    await task


async def test_cancelling_moves_nothing(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    interaction = asker(bot)
    click = await answer(await ask(archive, bot, interaction), interaction, 1)
    assert click.response.edited[0]["embed"].description == "Cancelled"
    assert requests_to(manager, "POST", ARCHIVE) == [] and interaction.original_edits == []


async def test_someone_else_cannot_confirm(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    interaction = asker(bot)
    task = await ask(archive, bot, interaction)
    view = interaction.followup.sent[-1]["view"]
    assert await view.interaction_check(FakeInteraction(user_id=2002)) is False
    view.stop()
    await task
    assert requests_to(manager, "POST", ARCHIVE) == []


async def test_after_archiving_the_lists_are_refreshed(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    manager.add("POST", ARCHIVE, in_archive())
    manager.add("GET", "/api/games", [])
    forgotten = []
    bot.my_games.forget = forgotten.append
    interaction = asker(bot)
    await answer(await ask(archive, bot, interaction), interaction, 0)
    assert forgotten == [1001]
    [refresh] = requests_to(manager, "GET", "/api/games")
    assert refresh.headers["Authorization"] == f"Bearer {BOT_TOKEN}"


async def test_a_cancelled_question_leaves_the_lists_alone(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    forgotten = []
    bot.my_games.forget = forgotten.append
    interaction = asker(bot)
    await answer(await ask(archive, bot, interaction), interaction, 1)
    assert forgotten == [] and requests_to(manager, "GET", "/api/games") == []


async def test_the_reply_follows_the_users_language(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    manager.add("POST", ARCHIVE, in_archive())
    interaction = asker(bot, locale="de")
    task = await ask(archive, bot, interaction)
    assert [b.label for b in interaction.followup.sent[-1]["view"].children] == ["Ins Archiv verschieben", "Abbrechen"]
    await answer(task, interaction, 0)
    assert "ist im Archiv (abgebrochen)" in interaction.original_edits[0]["embed"].description


# --- /game_archive: taking a game out again ---------------------------------------------------------------------------------------
async def test_restoring_asks_first_then_takes_the_game_out_of_the_archive(bot, manager):
    link(bot)
    manager.add("GET", GAME, in_archive())
    manager.add("POST", ARCHIVE, mine(state="stopped"))
    interaction = asker(bot)
    task = await ask(archive, bot, interaction, "restore")
    [question] = interaction.followup.sent
    assert question["embed"].description == "Take **Friday Run** out of the archive?"
    assert [b.label for b in question["view"].children] == ["Back to running games", "Cancel"]
    assert requests_to(manager, "POST", ARCHIVE) == []
    await answer(task, interaction, 0)
    [request] = requests_to(manager, "POST", ARCHIVE)
    assert json.loads(request.content) == {"archived": False}
    assert interaction.original_edits[0]["embed"].description == "Stopped\n**Friday Run** is back among the running games"


async def test_the_manager_may_still_refuse_to_take_a_game_out(bot, manager):
    link(bot)
    manager.add("GET", GAME, in_archive(return_blocked="finished"))  # left to the manager, which says why
    manager.add("POST", ARCHIVE, (409, {"detail": "Every player reached their goal in this game, so it stays in the archive"}))
    interaction = asker(bot)
    task = await ask(archive, bot, interaction, "restore")
    click = FakeInteraction(user_id=1001)
    await interaction.followup.sent[-1]["view"].children[0].callback(click)
    with pytest.raises(Conflict) as error:
        await task
    await bot.on_app_command_error(interaction, app_commands.CommandInvokeError(SimpleNamespace(name="x"), error.value))
    shown = interaction.followup.sent[-1]
    assert shown["ephemeral"] is True
    assert shown["embed"].description == (
        "The manager answered: Every player reached their goal in this game, so it stays in the archive"
    )


# --- /game_archive: checked before the question is shown ----------------------------------------------------------------------------
async def test_without_the_right_to_archive_there_is_no_question(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine(can_archive=False))
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await archive(bot, interaction)
    assert error.value.message("en") == (
        "Only whoever runs this game, or the player who generated it, can move it to the archive"
    )
    assert interaction.followup.sent == [] and len(manager.requests) == 1


async def test_a_game_in_the_archive_cannot_be_archived_again(bot, manager):
    link(bot)
    manager.add("GET", GAME, in_archive())
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await archive(bot, interaction, "archive")
    assert error.value.message("en") == "That game is in the archive already"
    assert interaction.followup.sent == [] and requests_to(manager, "POST", ARCHIVE) == []


async def test_a_game_that_is_not_in_the_archive_cannot_be_restored(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await archive(bot, interaction, "restore")
    assert error.value.message("en") == "That game is not in the archive"
    assert interaction.followup.sent == [] and requests_to(manager, "POST", ARCHIVE) == []


async def test_a_game_the_manager_does_not_show_is_asked_for_again_from_the_list(bot, manager):
    link(bot)
    manager.add("GET", GAME, (404, {"detail": "Game not found"}))
    with pytest.raises(UserFacingError) as error:
        await archive(bot, asker(bot))
    assert error.value.message("en") == "That game was not found. Pick one from the list."


async def test_without_a_link_nothing_is_asked_and_nothing_shows_as_thinking(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await archive(bot, interaction)
    with pytest.raises(NotLinkedError):
        await delete(bot, interaction)
    assert manager.requests == [] and not interaction.response.deferred


@pytest.mark.parametrize("game", ["abc", "7/../x", "", "12345678901"])
async def test_a_game_that_is_not_a_number_never_reaches_the_manager(bot, manager, game):
    link(bot)
    with pytest.raises(UserFacingError):
        await archive(bot, asker(bot), game=game)
    with pytest.raises(UserFacingError):
        await delete(bot, asker(bot), game=game)
    assert manager.requests == []


async def test_the_organizer_role_is_needed_when_one_is_set(bot, manager):
    link(bot)
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    with pytest.raises(MissingRoleError):
        await archive(bot, asker(bot, roles=[1]))
    with pytest.raises(MissingRoleError):
        await delete(bot, asker(bot, roles=[1]))
    assert manager.requests == []


async def test_the_organizer_role_holder_gets_the_question(bot, manager):
    link(bot)
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    manager.add("GET", GAME, mine())
    interaction = asker(bot, roles=[ORGANIZER_ROLE])
    task = await ask(archive, bot, interaction)
    assert interaction.followup.sent
    interaction.followup.sent[-1]["view"].stop()
    await task


# --- /game_delete -------------------------------------------------------------------------------------------------------------------
async def test_deleting_asks_with_a_red_button_then_deletes_as_the_person(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    manager.add("DELETE", GAME, {"ok": True})
    interaction = asker(bot)
    task = await ask(delete, bot, interaction)
    [question] = interaction.followup.sent
    assert question["embed"].description == (
        "Delete **Friday Run** for good? Its files, save and history are removed from the manager. "
        "This cannot be undone."
    )
    assert [b.label for b in question["view"].children] == ["Delete game", "Cancel"]
    assert question["view"].children[0].style.name == "danger"
    assert requests_to(manager, "DELETE", GAME) == []
    await answer(task, interaction, 0)
    [request] = requests_to(manager, "DELETE", GAME)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert request.extensions["timeout"]["read"] == 60
    [edit] = interaction.original_edits
    assert edit["embed"].description == "**Friday Run** was deleted" and edit["embed"].colour == SUCCESS
    assert edit["view"] is None


async def test_cancelling_deletes_nothing(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    interaction = asker(bot)
    await answer(await ask(delete, bot, interaction), interaction, 1)
    assert requests_to(manager, "DELETE", GAME) == [] and interaction.original_edits == []


async def test_without_the_right_to_delete_there_is_no_question(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine(can_manage=False))
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await delete(bot, interaction)
    assert error.value.message("en") == "Only the organizer of this game and administrators can delete it"
    assert interaction.followup.sent == [] and requests_to(manager, "DELETE", GAME) == []


async def test_a_refusal_by_the_manager_is_shown_with_its_words(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    manager.add("DELETE", GAME, (403, {"detail": "You may not manage this game"}))
    interaction = asker(bot)
    task = await ask(delete, bot, interaction)
    await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
    with pytest.raises(Forbidden) as error:
        await task
    await bot.on_app_command_error(interaction, app_commands.CommandInvokeError(SimpleNamespace(name="x"), error.value))
    assert interaction.followup.sent[-1]["embed"].description == "The manager answered: You may not manage this game"


async def test_a_delete_that_gets_no_answer_is_not_repeated(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    manager.add("DELETE", GAME, httpx.ReadTimeout("slow"))
    interaction = asker(bot)
    task = await ask(delete, bot, interaction)
    await interaction.followup.sent[-1]["view"].children[0].callback(FakeInteraction(user_id=1001))
    with pytest.raises(ManagerUnavailable):
        await task
    assert len(requests_to(manager, "DELETE", GAME)) == 1


async def test_after_deleting_the_lists_are_refreshed(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine())
    manager.add("DELETE", GAME, {"ok": True})
    manager.add("GET", "/api/games", [])
    forgotten = []
    bot.my_games.forget = forgotten.append
    interaction = asker(bot)
    await answer(await ask(delete, bot, interaction), interaction, 0)
    assert forgotten == [1001] and len(requests_to(manager, "GET", "/api/games")) == 1


async def test_the_game_name_in_the_question_is_escaped(bot, manager):
    link(bot)
    manager.add("GET", GAME, mine(name="*x* @everyone <@999>"))
    interaction = asker(bot)
    task = await ask(delete, bot, interaction)
    text = interaction.followup.sent[-1]["embed"].description
    assert "@everyone" not in text and "<@999>" not in text
    interaction.followup.sent[-1]["view"].stop()
    await task


async def test_a_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    manager.add("GET", GAME, (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError) as error:
        await delete(bot, asker(bot))
    assert "run /link again" in error.value.message("en")
    assert bot.links.get(1001).status == "invalid"
