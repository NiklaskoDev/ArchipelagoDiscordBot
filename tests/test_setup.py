import discord
import httpx
import pytest
from fakes import BOT_TOKEN, SITE_URL, FakeChannel, FakeInteraction, FakeRole

from apbot.cogs.setup import SetupCog, missing_permissions, timezone_choices
from apbot.errors import MissingRoleError, UserFacingError

GUILD = 5000
HEALTH = {"ok": True, "version": "1.3.0", "archipelago": "0.6.8", "ready": True, "archipelago_error": None}
BOT_ACCOUNT = {"user": {"id": 2, "username": "apbot", "role": "user"}, "site_url": SITE_URL}

ALL = discord.Permissions.all()
GAMES_PERMISSIONS = discord.Permissions(
    view_channel=True, send_messages=True, embed_links=True, send_messages_in_threads=True,
    create_public_threads=True, manage_threads=True, read_message_history=True,
)


def admin(bot, locale="en-US", **kwargs) -> FakeInteraction:
    return FakeInteraction(bot, locale=locale, guild_id=GUILD, manage_guild=True, **kwargs)


def cog(bot) -> SetupCog:
    return SetupCog(bot)


def manager_ok(manager, account=BOT_ACCOUNT, health=HEALTH):
    manager.add("GET", "/api/health", health)
    manager.add("GET", "/api/auth/status", account)


# --- who may use it ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("show", ()),
        ("manager", ()),
        ("channel", ("games", FakeChannel(1, ALL))),
        ("role", ("organizer", FakeRole(2))),
        ("role_clear", ("organizer",)),
        ("language", ("de",)),
        ("timezone", ("Europe/Berlin",)),
        ("feed", ("7",)),
        ("events", (True,)),
    ],
)
async def test_every_setup_command_refuses_people_without_manage_server_or_the_admin_role(bot, manager, command, args):
    bot.guild_settings.update(GUILD, admin_role_id=888)
    interaction = FakeInteraction(bot, guild_id=GUILD, roles=[777])
    with pytest.raises(MissingRoleError):
        await getattr(cog(bot), command).callback(cog(bot), interaction, *args)
    assert manager.requests == []
    assert bot.guild_settings.get(GUILD).admin_role_id == 888 and bot.guild_settings.get(GUILD).language == "en"


async def test_the_admin_role_may_configure_without_manage_server(bot):
    bot.guild_settings.update(GUILD, admin_role_id=888)
    interaction = FakeInteraction(bot, guild_id=GUILD, roles=[888])
    await cog(bot).language.callback(cog(bot), interaction, "fr")
    assert bot.guild_settings.get(GUILD).language == "fr"


# --- show and manager --------------------------------------------------------------------------------------------------
async def test_show_replies_privately_with_the_settings_and_the_manager_check(bot, manager):
    manager_ok(manager)
    bot.guild_settings.update(GUILD, language="de", timezone="Europe/Berlin", games_channel_id=111, admin_role_id=888)
    interaction = admin(bot)
    await cog(bot).show.callback(cog(bot), interaction)
    assert interaction.response.defer_kwargs == {"ephemeral": True}
    [message] = interaction.messages
    assert message["ephemeral"] is True
    settings, check = message["embeds"]
    fields = {f.name: f.value for f in settings.fields}
    assert fields["Language"] == "Deutsch" and fields["Time zone"] == "Europe/Berlin"
    assert fields["Games channel"] == "<#111>" and fields["Sign-ups channel"] == "Not set"
    assert fields["Admin role"] == "<@&888>" and fields["Organizer role"] == "Not set"
    assert {f.name: f.value for f in check.fields}["Version"] == "1.3.0"


async def test_show_with_an_unreachable_manager_still_shows_the_settings(bot, manager):
    manager.add("GET", "/api/health", httpx.ConnectError("refused"))
    interaction = admin(bot)
    await cog(bot).show.callback(cog(bot), interaction)
    settings, check = interaction.messages[0]["embeds"]
    assert settings.title == "Server settings"
    assert check.fields[0].value == "No. The manager did not answer."


async def test_manager_check_reports_each_point_and_asks_the_manager_as_the_bot(bot, manager):
    manager_ok(manager)
    interaction = admin(bot)
    await cog(bot).manager.callback(cog(bot), interaction)
    [message] = interaction.messages
    assert message["ephemeral"] is True
    fields = {f.name: f.value for f in message["embed"].fields}
    assert fields == {
        "Reachable": "Yes",
        "Version": "1.3.0",
        "Ready": "Yes",
        "Bot account": "apbot (user)",
        "Web address": SITE_URL,
    }
    assert message["embed"].colour == discord.Colour(0x2E7D32)
    assert manager.requests[-1].headers["Authorization"] == f"Bearer {BOT_TOKEN}"


async def test_manager_check_refreshes_the_known_web_address(bot, manager):
    manager_ok(manager)
    await cog(bot).manager.callback(cog(bot), admin(bot))
    assert bot.cache.site_url == SITE_URL


async def test_manager_check_flags_an_old_version_and_a_privileged_bot_account(bot, manager):
    manager_ok(manager, account={"user": {"id": 2, "username": "root", "role": "admin"}, "site_url": None},
               health={**HEALTH, "version": "1.2.0", "ready": False})
    interaction = admin(bot)
    await cog(bot).manager.callback(cog(bot), interaction)
    embed = interaction.messages[0]["embed"]
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Version"] == "1.2.0. The bot needs 1.3.0 or newer."
    assert fields["Ready"] == "No"
    assert fields["Bot account"].startswith("root (admin)\n") and "plain user" in fields["Bot account"]
    assert fields["Web address"] == "Not set in the manager"
    assert embed.colour == discord.Colour(0xB26A00)


async def test_manager_check_with_a_token_the_manager_refuses_is_an_error(bot, manager):
    manager_ok(manager, account=(401, {"detail": "This API token is not valid (any more)"}))
    bot.cache.set_site_url("https://old.example")
    interaction = admin(bot)
    await cog(bot).manager.callback(cog(bot), interaction)
    embed = interaction.messages[0]["embed"]
    assert {f.name: f.value for f in embed.fields}["Bot account"] == "Not accepted. Check MANAGER_API_TOKEN."
    assert embed.colour == discord.Colour(0xC62828)
    assert bot.cache.site_url == "https://old.example"


async def test_manager_check_that_cannot_reach_the_manager_says_so(bot, manager):
    manager.add("GET", "/api/health", httpx.ConnectError("refused"))
    interaction = admin(bot, locale="de")
    await cog(bot).manager.callback(cog(bot), interaction)
    embed = interaction.messages[0]["embed"]
    assert embed.fields[0].value == "Nein. Der Manager hat nicht geantwortet."
    assert embed.colour == discord.Colour(0xC62828)


# --- channel -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "column", "message"),
    [
        ("games", "games_channel_id", "Games channel set to <#111>"),
        ("signups", "signups_channel_id", "Sign-ups channel set to <#111>"),
        ("overview", "overview_channel_id", "Overview channel set to <#111>"),
    ],
)
async def test_a_channel_the_bot_can_use_is_saved(bot, kind, column, message):
    interaction = admin(bot)
    await cog(bot).channel.callback(cog(bot), interaction, kind, FakeChannel(111, ALL))
    assert getattr(bot.guild_settings.get(GUILD), column) == 111
    [reply] = interaction.messages
    assert reply["ephemeral"] is True and reply["embed"].description == message


async def test_a_channel_where_the_bot_lacks_permissions_is_refused_and_lists_them(bot):
    channel = FakeChannel(111, discord.Permissions(view_channel=True, send_messages=True, embed_links=True))
    with pytest.raises(UserFacingError) as error:
        await cog(bot).channel.callback(cog(bot), admin(bot), "games", channel)
    text = error.value.message("en")
    assert text.startswith("The bot is missing these permissions in <#111>: ")
    for label in ("Send messages in threads", "Create public threads", "Manage threads", "Read message history"):
        assert label in text
    assert "View channel" not in text
    assert bot.guild_settings.get(GUILD).games_channel_id is None


async def test_missing_permissions_are_listed_in_the_users_language(bot):
    channel = FakeChannel(111, discord.Permissions(view_channel=True, send_messages=True, embed_links=True))
    with pytest.raises(UserFacingError) as error:
        await cog(bot).channel.callback(cog(bot), admin(bot, locale="de"), "overview", channel)
    assert "Nachrichtenverlauf anzeigen, Nachrichten verwalten" in error.value.message("de")


def test_each_kind_of_channel_needs_what_the_bot_does_there():
    nothing = discord.Permissions.none()
    assert set(missing_permissions(nothing, "signups")) == {"view_channel", "send_messages", "embed_links"}
    assert set(missing_permissions(nothing, "overview")) == {
        "view_channel", "send_messages", "embed_links", "read_message_history", "manage_messages"
    }
    assert set(missing_permissions(nothing, "games")) == {
        "view_channel", "send_messages", "embed_links", "send_messages_in_threads", "create_public_threads",
        "manage_threads", "read_message_history",
    }
    assert missing_permissions(GAMES_PERMISSIONS, "games") == []
    assert missing_permissions(GAMES_PERMISSIONS, "overview") == ["manage_messages"]


# --- roles -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("kind", "column"), [("organizer", "organizer_role_id"), ("admin", "admin_role_id")])
async def test_a_role_is_saved(bot, kind, column):
    interaction = admin(bot)
    await cog(bot).role.callback(cog(bot), interaction, kind, FakeRole(222))
    assert getattr(bot.guild_settings.get(GUILD), column) == 222
    assert interaction.messages[0]["embed"].description.endswith("role set to <@&222>")


async def test_the_everyone_role_cannot_be_used(bot):
    with pytest.raises(UserFacingError) as error:
        await cog(bot).role.callback(cog(bot), admin(bot), "admin", FakeRole(GUILD, default=True))
    assert error.value.message("en") == "Pick a role other than @everyone"
    assert bot.guild_settings.get(GUILD).admin_role_id is None


async def test_clearing_a_role_removes_only_that_one(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=1, admin_role_id=2)
    interaction = admin(bot)
    await cog(bot).role_clear.callback(cog(bot), interaction, "organizer")
    settings = bot.guild_settings.get(GUILD)
    assert settings.organizer_role_id is None and settings.admin_role_id == 2
    assert interaction.messages[0]["embed"].description == "Organizer role cleared"


async def test_after_clearing_the_organizer_role_everyone_may_start_games_again(bot):
    bot.guild_settings.update(GUILD, organizer_role_id=1)
    with pytest.raises(MissingRoleError):
        bot.access.require_role(FakeInteraction(bot, guild_id=GUILD), "organizer")
    await cog(bot).role_clear.callback(cog(bot), admin(bot), "organizer")
    bot.access.require_role(FakeInteraction(bot, guild_id=GUILD), "organizer")


# --- language and time zone --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("code", ["en", "de", "fr", "nl"])
async def test_the_server_language_is_saved(bot, code):
    await cog(bot).language.callback(cog(bot), admin(bot), code)
    assert bot.guild_settings.get(GUILD).language == code


async def test_the_language_reply_names_the_language_in_its_own_words(bot):
    interaction = admin(bot, locale="fr")
    await cog(bot).language.callback(cog(bot), interaction, "nl")
    assert interaction.messages[0]["embed"].description == "Langue du serveur définie sur Nederlands"


async def test_an_unknown_language_is_refused(bot):
    with pytest.raises(UserFacingError):
        await cog(bot).language.callback(cog(bot), admin(bot), "xx")
    assert bot.guild_settings.get(GUILD).language == "en"


async def test_a_known_time_zone_is_saved(bot):
    interaction = admin(bot)
    await cog(bot).timezone.callback(cog(bot), interaction, "Europe/Berlin")
    assert bot.guild_settings.get(GUILD).timezone == "Europe/Berlin"
    assert interaction.messages[0]["embed"].description == "Time zone set to Europe/Berlin"


@pytest.mark.parametrize("value", ["Mars/Base", "europe/berlin", "../etc/passwd", "UTC; DROP TABLE guild_settings"])
async def test_an_unknown_time_zone_is_refused(bot, value):
    with pytest.raises(UserFacingError) as error:
        await cog(bot).timezone.callback(cog(bot), admin(bot), value)
    assert error.value.message("en") == "Pick a time zone from the list"
    assert bot.guild_settings.get(GUILD).timezone == "UTC"


def test_time_zone_suggestions_filter_by_what_was_typed_and_stop_at_25():
    assert [c.value for c in timezone_choices("berlin")] == ["Europe/Berlin"]
    assert [c.value for c in timezone_choices("new york")] == ["America/New_York"]
    assert len(timezone_choices("")) == 25
    assert timezone_choices("zzzz-nothing") == []
    assert all(c.name == c.value for c in timezone_choices("europe/b"))


async def test_the_time_zone_autocomplete_is_wired_to_the_option(bot):
    choices = await cog(bot).timezone_autocomplete(admin(bot), "berlin")
    assert [c.value for c in choices] == ["Europe/Berlin"]


# --- leaving a server --------------------------------------------------------------------------------------------------
async def test_leaving_a_server_deletes_its_settings_only(bot):
    bot.guild_settings.update(GUILD, language="de", games_channel_id=1)
    bot.guild_settings.update(6000, language="fr")
    await bot.on_guild_remove(discord.Object(GUILD))
    assert bot.guild_settings.get(GUILD).language == "en" and bot.guild_settings.get(GUILD).games_channel_id is None
    assert bot.guild_settings.get(6000).language == "fr"
    assert bot.db.query_all("SELECT * FROM guild_settings WHERE guild_id = ?", (GUILD,)) == []
