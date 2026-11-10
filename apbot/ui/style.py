"""The look of the bot's embeds: one accent colour, state colours only where they carry meaning, no emoji.

Embeds have no colour by default; informational ones use ACCENT. Discord's limits are listed here so every builder
stays inside them.
"""

import discord

ACCENT = discord.Colour(0x2F6F9F)  # game, sign-up and overview embeds
SUCCESS = discord.Colour(0x2E7D32)  # running, done
WARNING = discord.Colour(0xB26A00)  # stuck, sleeping, a deadline less than a day away
ERROR = discord.Colour(0xC62828)  # errors, crashed

EMBED_TOTAL = 6000  # characters over all embeds of one message
FIELD_VALUE = 1024
FIELDS = 25
DESCRIPTION = 4096
MESSAGE = 2000
SELECT_OPTIONS = 25
AUTOCOMPLETE = 25
CHOICE_NAME = 100

LIST_LINES = 25  # games or rounds in one list before "…and N more"
DISCORD_NAME_LIMIT = 100  # a thread, role or scheduled event name, and an event location
DISCORD_NAME_LIMIT = 100  # a thread, role or scheduled event name, and an event location
NAME_LIMIT = 60  # a game, world or player name in a line, before it is cut
SEPARATOR = " — "
ELLIPSIS = "…"

STATE_COLOURS = {"running": SUCCESS, "starting": SUCCESS, "sleeping": WARNING, "crashed": ERROR}
