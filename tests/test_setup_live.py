import discord
import pytest
from fakes import FakeInteraction, game_json

from apbot.cogs.setup import SetupCog
from apbot.db.live import DEFAULT_FEED_KINDS
from apbot.errors import MissingRoleError, UserFacingError
from apbot.manager.views import FEED_KINDS
from apbot.ui.views import MultiSelectView

GUILD = 5000


def admin(bot, locale="en-US", **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, locale=locale, guild_id=GUILD, manage_guild=True, **kwargs)


def cog(bot) -> SetupCog:
    return SetupCog(bot)


def with_thread(bot, game_id=7):
    bot.spaces.create(GUILD, game_id, 100, 200, DEFAULT_FEED_KINDS)


# --- /setup feed -------------------------------------------------------------------------------------------------------
async def test_feed_offers_every_kind_with_the_current_ones_ticked(bot):
    with_thread(bot)
    interaction = admin(bot)
    await cog(bot).feed.callback(cog(bot), interaction, "7")
    [message] = interaction.messages
    assert message["ephemeral"] is True
    view = message["view"]
    assert isinstance(view, MultiSelectView)
    select = view.select
    assert (select.min_values, select.max_values) == (0, 12)
    assert [o.value for o in select.options] == list(FEED_KINDS)
    assert {o.value for o in select.options if o.default} == set(DEFAULT_FEED_KINDS)
    assert [o.label for o in select.options][:3] == ["Goals reached", "Releases", "Collects"]


async def test_feed_is_in_the_admins_language(bot):
    with_thread(bot)
    interaction = admin(bot, locale="de")
    await cog(bot).feed.callback(cog(bot), interaction, "7")
    assert interaction.messages[0]["view"].select.options[0].label == "Erreichte Ziele"


async def test_choosing_saves_exactly_what_is_ticked(bot):
    with_thread(bot)
    interaction = admin(bot)
    await cog(bot).feed.callback(cog(bot), interaction, "7")
    view = interaction.messages[0]["view"]
    view.select._values = ["item", "chat"]
    click = admin(bot)
    await view.select.callback(click)
    assert bot.spaces.get(GUILD, 7).kinds == ("item", "chat")
    [edit] = click.response.edited
    assert edit["view"] is None and edit["embed"].description == "Saved. The thread shows what you ticked."


async def test_choosing_nothing_shows_nothing(bot):
    with_thread(bot)
    interaction = admin(bot)
    await cog(bot).feed.callback(cog(bot), interaction, "7")
    view = interaction.messages[0]["view"]
    view.select._values = []
    await view.select.callback(admin(bot))
    assert bot.spaces.get(GUILD, 7).kinds == ()


async def test_only_the_admin_who_asked_can_use_the_list(bot):
    with_thread(bot)
    interaction = admin(bot, user_id=1001)
    await cog(bot).feed.callback(cog(bot), interaction, "7")
    view = interaction.messages[0]["view"]
    stranger = admin(bot, user_id=2002)
    assert await view.interaction_check(stranger) is False
    assert stranger.messages[0]["ephemeral"] is True
    assert bot.spaces.get(GUILD, 7).kinds == DEFAULT_FEED_KINDS


async def test_an_admin_who_lost_the_right_in_the_meantime_cannot_save(bot):
    with_thread(bot)
    interaction = admin(bot)
    await cog(bot).feed.callback(cog(bot), interaction, "7")
    view = interaction.messages[0]["view"]
    view.select._values = ["chat"]
    with pytest.raises(MissingRoleError):
        await view.select.callback(FakeInteraction(bot, guild_id=GUILD, manage_guild=False))
    assert bot.spaces.get(GUILD, 7).kinds == DEFAULT_FEED_KINDS


async def test_a_game_without_a_thread_in_this_server_cannot_be_set_up(bot):
    bot.spaces.create(6000, 7, 100, 200, DEFAULT_FEED_KINDS)  # another server's thread
    with pytest.raises(UserFacingError) as error:
        await cog(bot).feed.callback(cog(bot), admin(bot), "7")
    assert error.value.message("en") == "This game has no open thread in this server"


async def test_a_game_whose_thread_is_closed_cannot_be_set_up(bot):
    with_thread(bot)
    bot.spaces.close(GUILD, 7)
    with pytest.raises(UserFacingError):
        await cog(bot).feed.callback(cog(bot), admin(bot), "7")


async def test_a_game_that_is_not_a_number_is_refused(bot):
    with pytest.raises(UserFacingError) as error:
        await cog(bot).feed.callback(cog(bot), admin(bot), "abc")
    assert error.value.message("en") == "Pick a game from the list"


async def test_the_game_option_suggests_the_bots_games(bot, manager):
    manager.add("GET", "/api/games", [game_json(7, "Friday Run")])
    manager.add("GET", "/api/auth/status", {"user": {"id": 2}, "site_url": None})
    await bot.cache.refresh()
    interaction = admin(bot)
    command = cog(bot).feed
    autocomplete = command._params["game"].autocomplete
    choices = await autocomplete(interaction, "friday")
    assert [(c.name, c.value) for c in choices] == [("Friday Run", "7")]


# --- /setup events -----------------------------------------------------------------------------------------------------
def events_interaction(bot, *, manage_events: bool) -> FakeInteraction:
    interaction = admin(bot)
    interaction.guild.me.guild_permissions = discord.Permissions(manage_events=manage_events)
    return interaction


async def test_events_are_turned_on_when_the_bot_may_manage_them(bot):
    interaction = events_interaction(bot, manage_events=True)
    await cog(bot).events.callback(cog(bot), interaction, True)
    assert bot.guild_settings.get(GUILD).scheduled_events is True
    [message] = interaction.messages
    assert message["ephemeral"] is True and message["embed"].description == "Scheduled events turned on"


async def test_events_cannot_be_turned_on_without_the_permission(bot):
    with pytest.raises(UserFacingError) as error:
        await cog(bot).events.callback(cog(bot), events_interaction(bot, manage_events=False), True)
    assert error.value.message("en") == "The bot is missing the Manage Events permission"
    assert bot.guild_settings.get(GUILD).scheduled_events is False


async def test_events_can_always_be_turned_off(bot):
    bot.guild_settings.update(GUILD, scheduled_events=True)
    interaction = events_interaction(bot, manage_events=False)
    await cog(bot).events.callback(cog(bot), interaction, False)
    assert bot.guild_settings.get(GUILD).scheduled_events is False
    assert interaction.messages[0]["embed"].description == "Scheduled events turned off"


async def test_events_answer_in_the_admins_language(bot):
    interaction = events_interaction(bot, manage_events=True)
    interaction.locale = "fr"
    await cog(bot).events.callback(cog(bot), interaction, True)
    assert interaction.messages[0]["embed"].description == "Événements planifiés activés"
