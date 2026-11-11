import asyncio
import logging
import sqlite3
from types import SimpleNamespace

import discord
import httpx
import pytest
from discord import app_commands
from fakes import ENCRYPTION_KEY, FakeInteraction, game_json, make_config

import apbot.__main__ as entry
from apbot.bot import ApBot
from apbot.errors import UserFacingError, user_message
from apbot.manager.errors import (
    Conflict,
    ManagerError,
    ManagerUnavailable,
    TokenInvalid,
    TooManyRequests,
)
from apbot.services.manager_watch import StartupFailure


COMMAND_NAMES = {
    "games", "game", "signups", "signup", "wake", "start", "stop", "restart", "link", "unlink", "mydata", "setup", "alerts",
    "tracker", "hints", "hint", "hint_location", "remaining", "say", "release", "collect", "status", "claim", "handover",
    "generate", "game_archive", "game_delete", "friends", "friend",
}


def invoke_error(original: Exception) -> app_commands.CommandInvokeError:
    return app_commands.CommandInvokeError(SimpleNamespace(name="games"), original)


def message_of(interaction: FakeInteraction) -> dict:
    [message] = interaction.messages
    return message


# --- how the bot is set up ---------------------------------------------------------------------------------------------
def test_bot_asks_for_no_privileged_intents_and_pings_nobody(bot):
    assert bot.intents.value == discord.Intents(guilds=True).value
    assert not bot.intents.message_content and not bot.intents.members and not bot.intents.presences
    mentions = bot.allowed_mentions
    assert mentions.everyone is False and mentions.users is False and mentions.roles is False


# --- errors are answered privately, in the user's language -------------------------------------------------------------
async def test_unreachable_manager_gets_an_ephemeral_localized_reply(bot):
    interaction = FakeInteraction(bot, locale="de")
    error = invoke_error(ManagerUnavailable(None, "no route"))
    await bot.on_app_command_error(interaction, error)
    message = message_of(interaction)
    assert message["ephemeral"] is True
    assert message["embed"].description == "Der Manager ist gerade nicht erreichbar. Versuche es gleich noch einmal."


async def test_error_after_a_public_defer_replaces_the_thinking_message_and_stays_private(bot):
    interaction = FakeInteraction(bot)
    await interaction.response.defer()
    await bot.on_app_command_error(interaction, invoke_error(UserFacingError("Pick a game from the list")))
    assert interaction.deleted_original
    [message] = interaction.followup.sent
    assert message["ephemeral"] is True
    assert message["embed"].description == "Pick a game from the list"


async def test_unexpected_error_is_logged_and_the_user_gets_a_plain_message(bot, caplog):
    interaction = FakeInteraction(bot)
    with caplog.at_level(logging.ERROR):
        await bot.on_app_command_error(interaction, invoke_error(KeyError("secret detail")))
    assert message_of(interaction)["embed"].description == "Something went wrong. Try again later."
    assert "secret detail" not in message_of(interaction)["embed"].description
    assert any(record.exc_info for record in caplog.records)


def test_user_messages_for_manager_errors():
    assert "cannot be reached" in user_message(ManagerUnavailable(None, "x"), "en")
    assert "does not accept the bot's access" in user_message(TokenInvalid(401, "x"), "en")
    assert "busy" in user_message(TooManyRequests(429, "x"), "en")
    assert user_message(Conflict(409, "The game is already running"), "en") == (
        "The manager answered: The game is already running"
    )
    assert user_message(ManagerError(500, "boom *x*"), "fr") == r"Le manager a répondu : boom \*x\*"


def test_a_bug_has_no_user_message():
    assert user_message(KeyError("x"), "en") is None


def test_user_facing_error_fills_its_placeholders():
    assert UserFacingError("Goals {done}/{total}", done=1, total=2).message("de") == "Ziele 1/2"


# --- the process -------------------------------------------------------------------------------------------------------
def test_main_with_a_missing_setting_names_it_and_exits_with_1(monkeypatch, capsys):
    for name in ("DISCORD_TOKEN", "MANAGER_URL", "MANAGER_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MANAGER_URL", "http://manager:8080")
    monkeypatch.setenv("MANAGER_API_TOKEN", "apm_valueNeverPrinted")
    assert entry.main() == 1
    err = capsys.readouterr().err
    assert "DISCORD_TOKEN" in err and "apm_valueNeverPrinted" not in err


class FakeBot:
    exit_code = 0
    raises: BaseException | None = None

    def __init__(self, config):
        self.config = config

    def run(self, token, **kwargs):
        assert kwargs == {"log_handler": None}
        if self.raises:
            raise self.raises


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_TOKEN", "discord-token-value")
    monkeypatch.setenv("MANAGER_URL", "http://manager:8080")
    monkeypatch.setenv("MANAGER_API_TOKEN", "apm_valueForTests")
    monkeypatch.setenv("BOT_ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(entry, "setup_logging", lambda level: None)


def test_main_exits_with_2_when_the_manager_refuses_the_bot_token(monkeypatch, env):
    class Refused(FakeBot):
        raises = StartupFailure("MANAGER_API_TOKEN is not accepted by the manager", 2)

    monkeypatch.setattr(entry, "ApBot", Refused)
    assert entry.main() == 2


def test_main_exits_with_1_when_discord_refuses_the_token(monkeypatch, env):
    class Refused(FakeBot):
        raises = discord.LoginFailure("Improper token has been passed.")

    monkeypatch.setattr(entry, "ApBot", Refused)
    assert entry.main() == 1


def test_main_returns_the_exit_code_the_bot_ended_with(monkeypatch, env):
    class Stopped(FakeBot):
        exit_code = 2

    monkeypatch.setattr(entry, "ApBot", Stopped)
    assert entry.main() == 2
    monkeypatch.setattr(entry, "ApBot", FakeBot)
    assert entry.main() == 0



# --- startup -----------------------------------------------------------------------------------------------------------
HEALTH = {"ok": True, "version": "1.3.0", "archipelago": "0.6.8", "ready": True, "archipelago_error": None}


@pytest.fixture
def no_discord(bot):
    """The bot without Discord: syncing commands is the one call of setup_hook that needs the network."""
    async def sync(*args, **kwargs):
        return []

    bot.tree.sync = sync
    return bot


async def let_tasks_run() -> None:
    for _ in range(30):
        await asyncio.sleep(0)


async def test_bot_starts_without_a_reachable_manager(no_discord, manager):
    manager.add("GET", "/api/health", httpx.ConnectError("refused"))
    await no_discord.setup_hook()
    try:
        assert no_discord.exit_code == 0
        assert {command.name for command in no_discord.tree.get_commands()} == COMMAND_NAMES
    finally:
        await no_discord.close()


async def test_manager_token_that_is_refused_at_startup_stops_the_bot_with_exit_code_2(no_discord, manager):
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", (401, {"detail": "This API token is not valid (any more)"}))
    with pytest.raises(StartupFailure) as failure:
        await no_discord.setup_hook()
    assert failure.value.exit_code == 2
    await no_discord.close()


async def test_manager_token_that_is_refused_after_a_retry_also_ends_with_exit_code_2(no_discord, manager, caplog):
    """The manager is down at startup, comes back, and then does not know the token."""
    health_calls = []

    def health(request):
        health_calls.append(1)
        if len(health_calls) <= 4:  # the first check and its three retries
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=HEALTH)

    manager.add("GET", "/api/health", health)
    manager.add("GET", "/api/auth/status", {"user": None, "site_url": None})
    with caplog.at_level(logging.ERROR):
        await no_discord.setup_hook()
        await let_tasks_run()
    assert no_discord.exit_code == 2
    assert "MANAGER_API_TOKEN is not accepted by the manager" in caplog.text
    await no_discord.close()


# --- command sync and background tasks -----------------------------------------------------------------------------------
def stub_sync(bot) -> list[dict]:
    calls: list[dict] = []

    async def sync(**kwargs):
        calls.append(kwargs)
        return []

    bot.tree.sync = sync
    return calls


async def test_commands_are_synced_globally_by_default(no_discord, manager):
    calls = stub_sync(no_discord)
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", {"user": {"id": 2, "username": "bot", "role": "user"}, "site_url": None})
    await no_discord.setup_hook()
    await no_discord.close()
    assert calls == [{}]


async def test_commands_go_to_the_development_server_only_when_one_is_set(tmp_path, manager):
    bot = ApBot(make_config(tmp_path, dev_guild_id=424242), manager=manager.client)
    calls = stub_sync(bot)
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", {"user": {"id": 2, "username": "bot", "role": "user"}, "site_url": None})
    await bot.setup_hook()
    await bot.close()
    assert [call["guild"].id for call in calls] == [424242]
    assert {c.name for c in bot.tree.get_commands(guild=discord.Object(424242))} == COMMAND_NAMES


async def test_background_tasks_refresh_the_games_and_write_the_heartbeat_once_connected(tmp_path, manager, monkeypatch):
    monkeypatch.setattr(ApBot, "is_ready", lambda self: True)
    bot = ApBot(make_config(tmp_path), manager=manager.client)
    stub_sync(bot)
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", {"user": {"id": 2, "username": "bot", "role": "user"}, "site_url": None})
    manager.add("GET", "/api/games", [game_json(1, "Friday Run")])
    await bot.setup_hook()
    await let_tasks_run()
    await bot.close()
    assert [g.name for g in bot.cache.games] == ["Friday Run"]
    assert (tmp_path / "heartbeat").exists()


async def test_no_heartbeat_while_the_gateway_is_not_connected(no_discord, manager, tmp_path):
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", {"user": {"id": 2, "username": "bot", "role": "user"}, "site_url": None})
    await no_discord.setup_hook()
    await let_tasks_run()
    await no_discord.close()
    assert not (tmp_path / "heartbeat").exists()


# --- the database and the keys -----------------------------------------------------------------------------------------
def test_the_bot_opens_and_migrates_its_database_in_the_data_folder(bot, tmp_path):
    assert (tmp_path / "bot.sqlite3").exists()
    assert [r["version"] for r in bot.db.query_all("SELECT version FROM schema_migrations")] == [1, 2, 3, 4]


async def test_closing_the_bot_closes_the_database(no_discord, manager):
    manager.add("GET", "/api/health", HEALTH)
    manager.add("GET", "/api/auth/status", {"user": {"id": 2, "username": "bot", "role": "user"}, "site_url": None})
    await no_discord.setup_hook()
    await no_discord.close()
    with pytest.raises(sqlite3.ProgrammingError):
        no_discord.db.query_all("SELECT 1")


def test_main_with_a_missing_encryption_key_names_it_and_exits_with_1(monkeypatch, env, capsys):
    monkeypatch.delenv("BOT_ENCRYPTION_KEY")
    assert entry.main() == 1
    assert "BOT_ENCRYPTION_KEY" in capsys.readouterr().err


def test_main_with_an_unusable_data_folder_exits_with_1_and_names_it(monkeypatch, env, tmp_path, caplog):
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(blocker / "data"))
    with caplog.at_level(logging.ERROR, logger="apbot"):
        assert entry.main() == 1
    assert "Cannot use the data folder" in caplog.text


def test_main_with_a_database_that_is_not_one_exits_with_1(monkeypatch, env, tmp_path):
    (tmp_path / "bot.sqlite3").write_bytes(b"this is not a database file" * 100)
    assert entry.main() == 1
