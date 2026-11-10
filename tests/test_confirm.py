import asyncio

import discord
from fakes import FakeInteraction

from apbot.ui.views import ConfirmView


async def let_tasks_run() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


def make(author_id=1001, lang="en", **kwargs) -> ConfirmView:
    return ConfirmView(author_id, lang, confirm_label=kwargs.pop("confirm_label", "Release items"), **kwargs)


async def ask(view, author_id=1001):
    interaction = FakeInteraction(user_id=author_id)
    task = asyncio.create_task(view.ask(interaction, discord.Embed(description="Sure?")))
    await let_tasks_run()
    return interaction, task


async def test_the_question_is_private_and_has_a_confirm_and_a_cancel_button():
    view = make()
    interaction, task = await ask(view)
    [message] = interaction.messages
    assert message["ephemeral"] is True and message["view"] is view
    assert [b.label for b in view.children] == ["Release items", "Cancel"]
    assert [b.style for b in view.children] == [discord.ButtonStyle.primary, discord.ButtonStyle.secondary]
    view.stop()
    await task


async def test_a_dangerous_action_gets_a_red_confirm_button():
    assert make(danger=True).children[0].style == discord.ButtonStyle.danger


async def test_buttons_follow_the_language():
    assert [b.label for b in make(lang="de", confirm_label="Weiter").children] == ["Weiter", "Abbrechen"]


async def test_confirming_answers_true_and_leaves_the_message_for_the_command_to_edit():
    view = make()
    interaction, task = await ask(view)
    click = FakeInteraction(user_id=1001)
    await view.children[0].callback(click)
    assert await task is True
    assert click.response.deferred and not click.response.edited
    assert interaction.original_edits == []


async def test_cancelling_answers_false_and_replaces_the_question():
    view = make()
    _, task = await ask(view)
    click = FakeInteraction(user_id=1001)
    await view.children[1].callback(click)
    assert await task is False
    [edit] = click.response.edited
    assert edit["embed"].description == "Cancelled" and edit["view"] is None


async def test_nobody_answering_is_false_and_the_buttons_are_removed():
    view = make()
    interaction, task = await ask(view)
    view.stop()  # what the timeout does
    assert await task is False
    [edit] = interaction.original_edits
    assert edit["view"] is None and "expired" in edit["embed"].description


async def test_only_the_person_who_asked_can_press_the_buttons():
    view = make(author_id=1001)
    assert await view.interaction_check(FakeInteraction(user_id=1001)) is True
    stranger = FakeInteraction(user_id=2002)
    assert await view.interaction_check(stranger) is False
    assert stranger.messages[0]["ephemeral"] is True
    view.stop()


def test_the_timeout_is_a_minute():
    assert asyncio.run(_timeout()) == 60


async def _timeout():
    return make().timeout
