"""`python -m apbot`: reads the settings, then runs the bot until it is stopped. Exit code 2: the manager refuses the
bot's API token."""

import logging
import os
import sqlite3
import sys

import discord

from apbot import __version__
from apbot.bot import ApBot
from apbot.config import ConfigError, load_config
from apbot.logging_setup import setup_logging
from apbot.services.manager_watch import StartupFailure

EXIT_BAD_SETTINGS = 1
log = logging.getLogger("apbot")


def main() -> int:
    try:
        config = load_config(os.environ)
    except ConfigError as exc:
        print(f"Invalid settings: {exc}", file=sys.stderr)
        return EXIT_BAD_SETTINGS
    setup_logging(config.log_level)
    log.info("Archipelago Discord bot %s starting", __version__)

    # The bot has no voice features; do not warn about the libraries only voice needs.
    discord.VoiceClient.warn_nacl = False
    discord.VoiceClient.warn_dave = False

    try:
        bot = ApBot(config)
    except (OSError, sqlite3.Error) as exc:
        log.error("Cannot use the data folder %s for the database: %s", config.data_dir, exc)
        return EXIT_BAD_SETTINGS
    try:
        bot.run(config.discord_token, log_handler=None)  # run() also ends the bot on SIGINT and SIGTERM
    except StartupFailure as exc:
        log.error("%s", exc)
        return exc.exit_code
    except discord.LoginFailure:
        log.error("DISCORD_TOKEN is not accepted by Discord")
        return EXIT_BAD_SETTINGS
    return bot.exit_code


if __name__ == "__main__":
    sys.exit(main())
