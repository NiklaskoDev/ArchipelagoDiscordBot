import asyncio
import json

import httpx
import pytest
from fakes import (
    BOT_TOKEN,
    SITE_URL,
    USER_TOKEN,
    FakeDirectory,
    FakeInteraction,
    FakeMessageable,
    forbidden,
    game_json,
    job_json,
    not_found,
)

from apbot.cogs.generate import JOB_POLL_INTERVAL, JOB_POLLS, GenerateCog
from apbot.errors import MissingRoleError, NotLinkedError, UserFacingError
from apbot.ui.generate import GenerationView
from apbot.ui.style import ACCENT, ERROR, SUCCESS, WARNING

GUILD = 5000
ORGANIZER_ROLE = 777
JOBS = "/api/jobs"
JOB = "/api/jobs/9"


def link(bot, user_id=1001):
    bot.links.save(user_id, 10, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def asker(bot, **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, user_id=1001, guild_id=GUILD, **kwargs)


def make_cog(bot, sleeps=None) -> GenerateCog:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return GenerateCog(bot, sleep=sleep)


def generate(bot, interaction, config="40", *, sleeps=None, **kwargs):
    cog = make_cog(bot, sleeps)
    return cog.generate.callback(cog, interaction, config, **kwargs)


def posted(manager) -> dict:
    [request] = [r for r in manager.requests if (r.method, r.url.path) == ("POST", JOBS)]
    return json.loads(request.content)


def requests_to(manager, method, path) -> list[httpx.Request]:
    return [r for r in manager.requests if (r.method, r.url.path) == (method, path)]


@pytest.fixture
def sleeps() -> list[float]:
    return []


# --- who may, and what is checked before the manager is asked ---------------------------------------------------------------
async def test_without_a_link_nothing_is_asked_and_nothing_shows_as_thinking(bot, manager):
    interaction = asker(bot)
    with pytest.raises(NotLinkedError):
        await generate(bot, interaction)
    assert manager.requests == [] and not interaction.response.deferred


async def test_with_an_organizer_role_set_others_are_refused_before_anything_else(bot, manager):
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    with pytest.raises(MissingRoleError):  # not even linked: the role is checked first
        await generate(bot, asker(bot, roles=[1]))
    link(bot)
    with pytest.raises(MissingRoleError):
        await generate(bot, asker(bot, roles=[1]), config="abc")
    assert manager.requests == []


async def test_the_organizer_role_holder_may_and_without_a_role_set_anyone_linked_may_try(bot, manager):
    link(bot)
    manager.add("POST", JOBS, (409, {"detail": "no"}))
    await generate(bot, asker(bot))
    bot.guild_settings.update(GUILD, organizer_role_id=ORGANIZER_ROLE)
    await generate(bot, asker(bot, roles=[ORGANIZER_ROLE]))
    assert len(requests_to(manager, "POST", JOBS)) == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"config": "abc"}, "Pick a config from the list"),
        ({"config": "40", "config2": "4x0"}, "Pick a config from the list"),
        ({"config": "40", "config2": "41", "config7": "40"}, "The same config was picked twice"),
        ({"config": "40", "config2": " 40 "}, "The same config was picked twice"),
        ({"config": "40", "seed": "12ab"}, "The seed must be a whole number"),
        ({"config": "40", "seed": "-5"}, "The seed must be a whole number"),
        ({"config": "40", "seed": "１２"}, "The seed must be a whole number"),
    ],
)
async def test_bad_options_are_refused_before_any_manager_call(bot, manager, kwargs, message):
    link(bot)
    interaction = asker(bot)
    with pytest.raises(UserFacingError) as error:
        await generate(bot, interaction, **kwargs)
    assert error.value.message("en") == message
    assert manager.requests == [] and not interaction.response.deferred


# --- what is sent ---------------------------------------------------------------------------------------------------------------
async def test_the_job_is_started_as_the_person_with_the_configs_in_the_order_given(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "succeeded", game_id=5))
    interaction = asker(bot)
    await generate(
        bot, interaction, "40", sleeps=sleeps, config3="42", config2="41", name=" Friday Run ", spoiler=2, race=True,
        seed="123456789012345678", start=False,
    )
    [request] = requests_to(manager, "POST", JOBS)
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert posted(manager) == {
        "config_ids": [40, 41, 42], "name": "Friday Run", "spoiler": 2, "race": True,
        "seed": 123456789012345678, "host_after": False,
    }


async def test_the_defaults_are_basic_spoiler_no_race_no_seed_no_name_and_a_started_server(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "failed", error="x"))
    await generate(bot, asker(bot), sleeps=sleeps)
    assert posted(manager) == {
        "config_ids": [40], "name": None, "spoiler": 1, "race": False, "seed": None, "host_after": True,
    }


async def test_a_blank_name_is_no_name(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "failed"))
    manager.add("GET", JOB, job_json(9, "failed"))
    await generate(bot, asker(bot), sleeps=sleeps, name="   ")
    assert posted(manager)["name"] is None


async def test_the_reply_is_private_and_the_bots_token_is_never_used(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "failed"))
    interaction = asker(bot)
    await generate(bot, interaction, sleeps=sleeps)
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    assert interaction.messages[0]["ephemeral"] is True
    assert all(r.headers["Authorization"] == f"Bearer {USER_TOKEN}" for r in manager.requests if r.url.path != "/api/games")
    assert BOT_TOKEN not in "".join(str(r.headers) for r in requests_to(manager, "POST", JOBS))


# --- the manager says no ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (409, "These games are not installed: Pokemon Red and Blue"),
        (409, "You can have 3 unfinished games at a time"),
        (403, "Only administrators can generate games on this server"),
        (400, "You may only use configs that are shared with you"),
        (404, "No such config"),
    ],
)
async def test_a_refusal_is_shown_privately_with_the_managers_words_and_nothing_is_raised(bot, manager, status, detail):
    link(bot)
    manager.add("POST", JOBS, (status, {"detail": detail}))
    interaction = asker(bot)
    await generate(bot, interaction)
    [message] = interaction.messages
    assert message["ephemeral"] is True and "view" not in message
    assert message["embed"].description == f"The game could not be generated: {detail}"
    assert message["embed"].colour == ERROR
    assert len(manager.requests) == 1


async def test_the_managers_reason_is_escaped_and_cut(bot, manager):
    link(bot)
    manager.add("POST", JOBS, (409, {"detail": "@everyone <@999> **x**"}))
    interaction = asker(bot)
    await generate(bot, interaction)
    text = interaction.messages[0]["embed"].description
    assert "@everyone" not in text and "<@999>" not in text


async def test_a_refusal_is_in_the_users_language(bot, manager):
    link(bot)
    manager.add("POST", JOBS, (409, {"detail": "nope"}))
    interaction = asker(bot, locale="de")
    await generate(bot, interaction)
    assert interaction.messages[0]["embed"].description == "Das Spiel konnte nicht generiert werden: nope"


async def test_a_token_the_manager_refuses_marks_the_link_invalid(bot, manager):
    link(bot)
    manager.add("POST", JOBS, (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError) as error:
        await generate(bot, asker(bot))
    assert "run /link again" in error.value.message("en")
    assert bot.links.get(1001).status == "invalid"


async def test_a_read_only_token_is_explained(bot, manager):
    link(bot)
    manager.add("POST", JOBS, (403, {"detail": "This API token can only read"}))
    with pytest.raises(UserFacingError) as error:
        await generate(bot, asker(bot))
    assert "can only read" in error.value.message("en")
    assert bot.links.get(1001).status == "active"


async def test_a_manager_that_cannot_be_reached_is_not_asked_twice(bot, manager):
    from apbot.manager.errors import ManagerUnavailable

    link(bot)
    manager.add("POST", JOBS, httpx.ReadTimeout("slow"))
    with pytest.raises(ManagerUnavailable):
        await generate(bot, asker(bot))
    assert len(manager.requests) == 1


# --- following the generation ----------------------------------------------------------------------------------------------------
async def test_a_generation_is_followed_from_the_queue_to_a_ready_game(bot, manager, sleeps):
    link(bot)
    bot.cache.set_site_url(SITE_URL)
    manager.add("POST", JOBS, job_json(9, "queued", queue_position=2, name="Friday"))
    manager.add(
        "GET", JOB, job_json(9, "running", name="Friday"), job_json(9, "succeeded", name="Friday", game_id=5)
    )
    manager.add("GET", "/api/games", [game_json(5, "Friday")])
    forgotten = []
    bot.my_games.forget = forgotten.append
    interaction = asker(bot)
    await generate(bot, interaction, sleeps=sleeps)

    [first] = interaction.messages
    assert first["embed"].title == "Friday"
    assert first["embed"].description == "Waiting to be generated (place 2 in the queue)"
    assert isinstance(first["view"], GenerationView) and first["embed"].colour == ACCENT
    running, done = interaction.original_edits
    assert running["embed"].description == "Generating…" and running["view"] is first["view"]
    assert done["embed"].description == "Generated. The game is ready. Its server starts now."
    assert done["embed"].colour == SUCCESS
    [open_button] = done["view"].children
    assert open_button.url == f"{SITE_URL}/#/games/5"
    assert sleeps == [JOB_POLL_INTERVAL, JOB_POLL_INTERVAL] and JOB_POLL_INTERVAL == 10
    assert forgotten == [1001, 1001]  # when queued and when ready
    [refresh] = requests_to(manager, "GET", "/api/games")
    assert refresh.headers["Authorization"] == f"Bearer {BOT_TOKEN}"  # the public list is refreshed
    assert first["view"].is_finished()


async def test_each_look_at_the_job_is_made_as_the_person(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "running"), job_json(9, "cancelled"))
    await generate(bot, asker(bot), sleeps=sleeps)
    looks = requests_to(manager, "GET", JOB)
    assert len(looks) == 2
    assert all(r.headers["Authorization"] == f"Bearer {USER_TOKEN}" for r in looks)


async def test_a_failed_generation_shows_what_the_generator_said_and_removes_the_button(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "failed", error="Timespinner: no such option 'foo'"))
    interaction = asker(bot)
    await generate(bot, interaction, sleeps=sleeps)
    [failed] = interaction.original_edits
    assert failed["embed"].description == "Generation failed\nTimespinner: no such option 'foo'"
    assert failed["embed"].colour == ERROR and failed["view"] is None
    assert requests_to(manager, "GET", "/api/games") == []  # no new game: nothing to refresh


async def test_a_cancelled_generation_says_cancelled(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "cancelled"))
    interaction = asker(bot)
    await generate(bot, interaction, sleeps=sleeps)
    [edit] = interaction.original_edits
    assert (edit["embed"].description, edit["embed"].colour, edit["view"]) == ("Cancelled", WARNING, None)


async def test_a_friends_config_in_the_list_of_players_names_its_owner(bot, manager, sleeps):
    link(bot)
    players = [
        {"filename": "alice.yaml", "user_id": 10, "owner": "alice", "config_id": 40},
        {"filename": "bob.yaml", "user_id": 11, "owner": "bob", "config_id": 77},
    ]
    manager.add("POST", JOBS, job_json(9, "queued", players=players))
    manager.add("GET", JOB, job_json(9, "failed"))
    interaction = asker(bot)
    await generate(bot, interaction, sleeps=sleeps)
    [field] = interaction.messages[0]["embed"].fields
    assert field.value == "alice\nbob — bob"  # the caller (manager user 10) is not named next to their own config


async def test_the_reply_follows_the_users_language(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "running"), job_json(9, "cancelled"))
    interaction = asker(bot, locale="de")
    await generate(bot, interaction, sleeps=sleeps)
    assert interaction.messages[0]["embed"].description == "Wartet auf die Generierung"
    assert interaction.original_edits[0]["embed"].description == "Wird generiert…"
    assert interaction.messages[0]["view"].cancel.label == "Generierung abbrechen"


async def test_a_busy_manager_between_two_looks_does_not_end_the_follow_up(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    # one look fails for good (a read is tried four times), the next one finds the game ready
    manager.add("GET", JOB, *[(503, {"detail": "busy"})] * 4, job_json(9, "succeeded", game_id=5))
    interaction = asker(bot)
    await generate(bot, interaction, sleeps=sleeps)
    assert len(sleeps) == 2
    [done] = interaction.original_edits
    assert done["embed"].description.startswith("Generated.")


async def test_a_generation_that_never_ends_is_given_up_after_fourteen_minutes_with_a_link_to_the_job(bot, manager, sleeps):
    link(bot)
    bot.cache.set_site_url(SITE_URL)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "running"))
    interaction = asker(bot)
    await generate(bot, interaction, sleeps=sleeps)
    assert JOB_POLLS == 84 and JOB_POLLS * JOB_POLL_INTERVAL == 840
    assert sleeps == [JOB_POLL_INTERVAL] * JOB_POLLS
    *progress, last = interaction.original_edits
    assert len(progress) == JOB_POLLS
    assert last["embed"].description == "Still generating. Follow it in the manager, or look at /games later."
    assert last["embed"].colour == WARNING
    [open_button] = last["view"].children
    assert open_button.url == f"{SITE_URL}/#/jobs/9"
    assert requests_to(manager, "GET", "/api/games") == []


async def test_without_a_web_address_the_last_message_has_no_link(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "running"))
    interaction = asker(bot)
    await generate(bot, interaction, sleeps=sleeps)
    assert interaction.original_edits[-1]["view"] is None


async def test_when_the_reply_can_no_longer_be_edited_the_follow_up_ends_quietly(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "running"))
    interaction = asker(bot)

    async def refuse(**kwargs):
        raise not_found()

    interaction.edit_original_response = refuse
    await generate(bot, interaction, sleeps=sleeps)
    assert len(sleeps) == 1 and len(requests_to(manager, "GET", JOB)) == 1


async def test_a_token_that_stops_working_during_the_follow_up_is_reported(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(UserFacingError):
        await generate(bot, asker(bot), sleeps=sleeps)
    assert bot.links.get(1001).status == "invalid"


@pytest.mark.parametrize("status", [403, 404])
async def test_a_job_the_manager_no_longer_shows_ends_the_follow_up_with_a_notice_and_a_link(bot, manager, sleeps, status):
    link(bot)
    bot.cache.set_site_url(SITE_URL)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, (status, {"detail": "That job does not exist"}))
    interaction = asker(bot)
    await generate(bot, interaction, sleeps=sleeps)  # nothing is raised: it would be answered with the generic error
    [lost] = interaction.original_edits
    assert lost["embed"].description == "The manager no longer shows this generation. Look at /games, or open the manager."
    assert lost["embed"].colour == WARNING
    [open_button] = lost["view"].children
    assert open_button.url == f"{SITE_URL}/#/games"
    assert len(sleeps) == 1 and len(interaction.messages) == 1  # no error message on top
    assert bot.links.get(1001).status == "active"


async def test_a_lost_job_notice_follows_the_users_language_and_needs_no_web_address(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, (404, {"detail": "gone"}))
    interaction = asker(bot, locale="de")
    await generate(bot, interaction, sleeps=sleeps)
    [lost] = interaction.original_edits
    assert "Generierung" in lost["embed"].description and lost["view"] is None


async def test_a_cancel_pressed_while_a_progress_edit_is_on_its_way_is_not_undone_by_it(bot, manager, sleeps):
    link(bot)
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "running"), job_json(9, "running"), job_json(9, "cancelled"))
    interaction = asker(bot)
    shown = []  # whether the Cancel button was greyed out in what each edit carried

    async def edit(**kwargs):
        view = kwargs["view"]
        if view is not None:
            shown.append(view.cancel.disabled)
        if len(shown) == 1:  # the person presses Cancel while this edit is still on its way to Discord
            view.cancel_requested = True
            view.cancel.disabled = True
        interaction.original_edits.append(kwargs)

    interaction.edit_original_response = edit
    await generate(bot, interaction, sleeps=sleeps)
    assert shown[0] is False and shown[1:] == [True, True]  # shown again at once, not only with the next look


# --- the Cancel button -----------------------------------------------------------------------------------------------------------
async def start_and_wait(bot, manager, interaction):
    """Starts /generate with a job that stays running; returns its task once the reply is out."""
    manager.add("POST", JOBS, job_json(9, "queued"))
    manager.add("GET", JOB, job_json(9, "running"))
    gate = asyncio.Event()

    async def sleep(seconds: float) -> None:
        await gate.wait()

    cog = GenerateCog(bot, sleep=sleep)
    task = asyncio.create_task(cog.generate.callback(cog, interaction, "40"))
    for _ in range(100):
        if interaction.messages:
            break
        await asyncio.sleep(0)
    return task, interaction.messages[0]["view"], gate


async def test_the_cancel_button_asks_the_manager_as_the_person_and_greys_itself_out(bot, manager):
    link(bot)
    interaction = asker(bot)
    task, view, gate = await start_and_wait(bot, manager, interaction)
    assert [b.label for b in view.children] == ["Cancel generation"] and view.children[0].style.name == "danger"
    manager.add("POST", "/api/jobs/9/cancel", job_json(9, "cancelled"))
    click = FakeInteraction(bot, user_id=1001)
    assert await view.interaction_check(click) is True
    await view.children[0].callback(click)
    [request] = requests_to(manager, "POST", "/api/jobs/9/cancel")
    assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert click.response.deferred and view.cancel_requested is True and view.children[0].disabled is True
    assert click.original_edits == [{"view": view}]
    task.cancel()
    gate.set()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_job_that_is_over_already_is_no_error_for_the_cancel_button(bot, manager):
    link(bot)
    interaction = asker(bot)
    task, view, gate = await start_and_wait(bot, manager, interaction)
    manager.add("POST", "/api/jobs/9/cancel", (409, {"detail": "That job is not running or queued"}))
    click = FakeInteraction(bot, user_id=1001)
    await view.children[0].callback(click)
    assert view.cancel_requested is True and click.response.deferred
    task.cancel()
    gate.set()
    await asyncio.gather(task, return_exceptions=True)


async def test_only_the_person_who_started_the_generation_can_cancel_it(bot, manager):
    link(bot)
    interaction = asker(bot)
    task, view, gate = await start_and_wait(bot, manager, interaction)
    stranger = FakeInteraction(bot, user_id=2002)
    assert await view.interaction_check(stranger) is False
    assert stranger.messages[0]["embed"].description == "This question is not for you."
    assert requests_to(manager, "POST", "/api/jobs/9/cancel") == []
    task.cancel()
    gate.set()
    await asyncio.gather(task, return_exceptions=True)


async def test_the_cancel_button_of_someone_whose_token_stopped_working_gets_the_link_again_message(bot, manager):
    link(bot)
    interaction = asker(bot)
    task, view, gate = await start_and_wait(bot, manager, interaction)
    manager.add("POST", "/api/jobs/9/cancel", (401, {"detail": "This API token is not valid (any more)"}))
    click = FakeInteraction(bot, user_id=1001)
    with pytest.raises(UserFacingError) as error:
        await view.children[0].callback(click)
    await view.on_error(click, error.value, view.children[0])  # what discord.py does with the exception
    shown = click.followup.sent[-1]
    assert shown["ephemeral"] is True and "run /link again" in shown["embed"].description
    assert bot.links.get(1001).status == "invalid"
    task.cancel()
    gate.set()
    await asyncio.gather(task, return_exceptions=True)



# --- friends in the game: they are told when the generation is queued --------------------------------------------------------
OWN = {"filename": "alice.yaml", "user_id": 10, "owner": "alice", "config_id": 40}
BOBS = {"filename": "bob-hk.yaml", "user_id": 11, "owner": "bob", "config_id": 77}
BOBS_SECOND = {"filename": "bob-tunic.yml", "user_id": 11, "owner": "bob", "config_id": 78}
CYS = {"filename": "cy.yaml", "user_id": 12, "owner": "cy", "config_id": 90}
FRIEND_DISCORD = {11: 2002, 12: 2003}


class Friends:
    """Friends who are linked to the bot (manager user 11 is Discord 2002, 12 is 2003) and the direct messages they get."""

    def __init__(self, bot, *, alert_on=(2002, 2003), linked=(2002, 2003)):
        self.directory = FakeDirectory()
        bot.alerts._fetch_user = self.directory
        for discord_id in linked:
            manager_id = {2002: 11, 2003: 12}[discord_id]
            bot.links.save(discord_id, manager_id, f"friend{manager_id}", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")
        for discord_id in set(alert_on) & set(linked):
            bot.alert_prefs.replace(discord_id, ["friend_game"])

    def sent(self, discord_id: int) -> list:
        user = self.directory.users.get(discord_id)
        return user.sent if user else []

    def texts(self, discord_id: int) -> list[str]:
        return [message.kwargs["embed"].description for message in self.sent(discord_id)]


def queue(manager, players, name=None, final="failed"):
    manager.add("POST", JOBS, job_json(9, "queued", name=name, players=players))
    manager.add("GET", JOB, job_json(9, final, name=name, players=players))


async def test_a_friend_whose_config_is_in_the_game_gets_one_message_with_the_callers_manager_name_and_the_job_name(bot, manager, sleeps):
    link(bot)
    friends = Friends(bot)
    queue(manager, [OWN, BOBS], name="Friday Run")
    await generate(bot, asker(bot), "40", config2="77", sleeps=sleeps)
    assert friends.texts(2002) == [
        "**alice** is generating a game with your config bob-hk: **Friday Run**. Once it is ready it is among your games."
    ]
    assert friends.sent(2003) == []  # not in the game
    assert friends.sent(2002)[0].kwargs["embed"].title == "A friend puts you in a game"


async def test_a_game_without_a_name_is_called_a_new_game_and_two_configs_of_one_friend_are_one_message(bot, manager, sleeps):
    link(bot)
    friends = Friends(bot)
    queue(manager, [OWN, BOBS, BOBS_SECOND])
    await generate(bot, asker(bot), "40", config2="77", config3="78", sleeps=sleeps)
    assert friends.texts(2002) == [
        "**alice** is generating a game with your config bob-hk, bob-tunic: **a new game**. Once it is ready it is among your games."
    ]


async def test_each_friend_gets_their_own_message_with_only_their_configs(bot, manager, sleeps):
    link(bot)
    friends = Friends(bot)
    queue(manager, [BOBS, CYS], name="Run")
    await generate(bot, asker(bot), "77", config2="90", sleeps=sleeps)
    assert [len(friends.sent(2002)), len(friends.sent(2003))] == [1, 1]
    assert "your config bob-hk:" in friends.texts(2002)[0] and "cy" not in friends.texts(2002)[0]
    assert "your config cy:" in friends.texts(2003)[0] and "bob" not in friends.texts(2003)[0]


async def test_the_message_follows_the_friends_language_not_the_callers(bot, manager, sleeps):
    link(bot)
    friends = Friends(bot)
    bot.links.set_locale(2002, "de")
    queue(manager, [BOBS], name="Run")
    await generate(bot, asker(bot, locale="fr"), "77", sleeps=sleeps)
    assert friends.texts(2002) == [
        "**alice** generiert ein Spiel mit deiner Config bob-hk: **Run**. Sobald es fertig ist, gehört es zu deinen Spielen."
    ]


async def test_the_friend_is_told_when_the_job_is_queued_before_it_is_followed(bot, manager):
    link(bot)
    friends = Friends(bot)
    queue(manager, [BOBS], name="Run")
    told_at_first_look: list[int] = []

    async def sleep(seconds: float) -> None:
        told_at_first_look.append(len(friends.sent(2002)))

    cog = GenerateCog(bot, sleep=sleep)
    await cog.generate.callback(cog, asker(bot), "77")
    assert told_at_first_look[0] == 1


async def test_a_game_made_of_the_callers_own_configs_tells_nobody(bot, manager, sleeps):
    link(bot)
    friends = Friends(bot)
    bot.alert_prefs.replace(1001, ["friend_game"])
    queue(manager, [OWN, {**OWN, "filename": "second.yaml", "config_id": 41}])
    interaction = asker(bot)
    await generate(bot, interaction, "40", config2="41", sleeps=sleeps)
    assert friends.sent(1001) == [] and friends.sent(2002) == [] and friends.sent(2003) == []
    assert len(friends.directory.users) == 0


async def test_a_friend_who_is_not_linked_to_the_bot_is_not_told_and_nothing_fails(bot, manager, sleeps):
    link(bot)
    friends = Friends(bot, linked=())
    queue(manager, [OWN, BOBS], name="Run")
    interaction = asker(bot)
    await generate(bot, interaction, "40", config2="77", sleeps=sleeps)
    assert friends.sent(2002) == [] and interaction.messages  # the caller still sees the generation


async def test_a_friend_who_did_not_turn_the_alert_on_is_not_told(bot, manager, sleeps):
    link(bot)
    friends = Friends(bot, alert_on=())
    bot.alert_prefs.replace(2002, ["game_online"])
    queue(manager, [BOBS], name="Run")
    await generate(bot, asker(bot), "77", sleeps=sleeps)
    assert friends.sent(2002) == []


async def test_a_player_without_an_account_any_more_tells_no_one(bot, manager, sleeps):
    link(bot)
    friends = Friends(bot)
    queue(manager, [OWN, {"filename": "gone.yaml", "user_id": None, "owner": None, "config_id": 5}])
    await generate(bot, asker(bot), "40", config2="5", sleeps=sleeps)
    assert friends.sent(2002) == [] and friends.sent(2003) == []


@pytest.mark.parametrize("status", [409, 403, 400, 404])
async def test_a_refused_generation_tells_no_friend(bot, manager, sleeps, status):
    link(bot)
    friends = Friends(bot)
    manager.add("POST", JOBS, (status, {"detail": "No"}))
    await generate(bot, asker(bot), "77", sleeps=sleeps)
    assert friends.sent(2002) == [] and friends.sent(2003) == []


async def test_a_friend_who_closed_their_dms_does_not_disturb_the_generation(bot, manager, sleeps):
    link(bot)
    friends = Friends(bot)
    friends.directory.users[2002] = FakeMessageable(2002)
    friends.directory.users[2002].fail_with = forbidden()
    manager.add("POST", JOBS, job_json(9, "queued", players=[BOBS]))
    manager.add("GET", JOB, job_json(9, "succeeded", game_id=5, players=[BOBS]))
    manager.add("GET", "/api/games", [game_json(5, "Friday")])
    interaction = asker(bot)
    await generate(bot, interaction, "77", sleeps=sleeps)
    assert interaction.original_edits[-1]["embed"].colour == SUCCESS
