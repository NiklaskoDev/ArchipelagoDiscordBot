import asyncio

import pytest
from fakes import USER_TOKEN, FakeInteraction

from apbot.cogs.alerts import AlertsCog
from apbot.cogs.linking import LinkingCog
from apbot.db.live import ALERT_KINDS
from apbot.errors import NotLinkedError
from apbot.ui.views import MultiSelectView


def link(bot, user_id=1001, manager_id=10):
    bot.links.save(user_id, manager_id, "alice", bot.cipher.encrypt(USER_TOKEN), USER_TOKEN[-4:], "en")


def cog(bot) -> AlertsCog:
    return AlertsCog(bot)


async def ask(bot, **kwargs) -> FakeInteraction:
    interaction = FakeInteraction(bot, **kwargs)
    await cog(bot).alerts.callback(cog(bot), interaction)
    return interaction


async def test_someone_who_is_not_linked_is_told_to_link(bot):
    with pytest.raises(NotLinkedError):
        await ask(bot)


async def test_the_list_has_the_six_alerts_all_off_to_begin_with_and_is_private(bot):
    link(bot)
    interaction = await ask(bot)
    [message] = interaction.messages
    assert message["ephemeral"] is True
    view = message["view"]
    assert isinstance(view, MultiSelectView)
    assert [o.value for o in view.select.options] == list(ALERT_KINDS) and len(ALERT_KINDS) == 6
    assert not any(o.default for o in view.select.options)
    assert (view.select.min_values, view.select.max_values) == (0, 6)
    assert [o.label for o in view.select.options][0] == "An item arrives for one of my slots"


async def test_what_is_on_is_ticked(bot):
    link(bot)
    bot.alert_prefs.replace(1001, ["hint_found", "slot_offered"])
    view = (await ask(bot)).messages[0]["view"]
    assert {o.value for o in view.select.options if o.default} == {"hint_found", "slot_offered"}


async def test_the_list_is_in_the_persons_language(bot):
    link(bot)
    view = (await ask(bot, locale="nl")).messages[0]["view"]
    assert view.select.options[4].label == "Mij wordt een slot aangeboden"


async def test_choosing_saves_exactly_what_is_ticked_and_replaces_the_old_choice(bot):
    link(bot)
    bot.alert_prefs.replace(1001, ["hint_found"])
    view = (await ask(bot)).messages[0]["view"]
    view.select._values = ["item_received", "game_online"]
    click = FakeInteraction(bot, user_id=1001)
    await view.select.callback(click)
    assert bot.alert_prefs.enabled(1001) == {"item_received", "game_online"}
    [edit] = click.response.edited
    assert edit["view"] is None
    assert edit["embed"].description == (
        "Saved. You get direct messages when:\nAn item arrives for one of my slots\nOne of my games comes online"
    )


async def test_choosing_nothing_turns_everything_off(bot):
    link(bot)
    bot.alert_prefs.replace(1001, ["hint_found"])
    view = (await ask(bot)).messages[0]["view"]
    view.select._values = []
    click = FakeInteraction(bot, user_id=1001)
    await view.select.callback(click)
    assert bot.alert_prefs.enabled(1001) == frozenset()
    assert click.response.edited[0]["embed"].description == "All alerts are off"


async def test_the_confirmation_is_in_the_persons_language(bot):
    link(bot)
    view = (await ask(bot)).messages[0]["view"]
    view.select._values = ["slot_offered"]
    click = FakeInteraction(bot, user_id=1001, locale="de")
    await view.select.callback(click)
    assert click.response.edited[0]["embed"].description == "Gespeichert. Du bekommst Direktnachrichten, wenn:\nMir wird ein Slot angeboten"


async def test_someone_else_cannot_change_my_alerts(bot):
    link(bot)
    view = (await ask(bot)).messages[0]["view"]
    stranger = FakeInteraction(bot, user_id=2002)
    assert await view.interaction_check(stranger) is False
    assert stranger.messages[0]["ephemeral"] is True
    assert bot.alert_prefs.enabled(1001) == frozenset()


async def test_a_person_who_unlinked_while_the_list_was_open_saves_nothing(bot):
    link(bot)
    view = (await ask(bot)).messages[0]["view"]
    bot.links.delete(1001)
    view.select._values = ["hint_found"]
    with pytest.raises(NotLinkedError):
        await view.select.callback(FakeInteraction(bot, user_id=1001))
    assert bot.db.query_all("SELECT * FROM alert_prefs") == []


async def test_an_error_in_the_list_is_answered_privately_by_the_bots_error_handler(bot):
    link(bot)
    view = (await ask(bot)).messages[0]["view"]
    click = FakeInteraction(bot, user_id=1001)
    await view.on_error(click, NotLinkedError(), view.select)
    assert click.messages[0]["ephemeral"] is True
    assert click.messages[0]["embed"].description == "Link your manager account first with /link"


async def test_mydata_lists_the_alerts_that_are_on(bot):
    link(bot)
    bot.alert_prefs.replace(1001, ["hint_found", "game_online"])
    interaction = FakeInteraction(bot, user_id=1001)
    await LinkingCog(bot).mydata.callback(LinkingCog(bot), interaction)
    fields = {f.name: f.value for f in interaction.messages[0]["embed"].fields}
    assert fields["Alerts"] == "An item I was hinted is found\nOne of my games comes online"


async def test_unlinking_deletes_the_alert_choices(bot):
    link(bot)
    bot.alert_prefs.replace(1001, ["hint_found"])
    interaction = FakeInteraction(bot, user_id=1001)
    task = asyncio.create_task(LinkingCog(bot).unlink.callback(LinkingCog(bot), interaction))
    for _ in range(10):
        await asyncio.sleep(0)
    await interaction.messages[0]["view"].children[0].callback(FakeInteraction(bot, user_id=1001))
    await task
    assert bot.db.query_all("SELECT * FROM alert_prefs") == []
