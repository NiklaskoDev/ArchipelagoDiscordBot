"""Suggestions for command options, answered from memory so they stay inside Discord's 3 seconds."""

import discord
from discord import app_commands

from apbot.manager.views import GameSummary, listed_games
from apbot.ui.format import clip
from apbot.ui.style import AUTOCOMPLETE, CHOICE_NAME


def game_choices(games: list[GameSummary], current: str) -> list[app_commands.Choice[str]]:
    """Games whose name contains what was typed: the listed ones first, finished ones after. The value is the id."""
    listed = listed_games(games)
    finished = sorted((g for g in games if g.finished_at is not None), key=lambda g: -g.created_at)
    needle = current.strip().casefold()
    return [
        app_commands.Choice(name=clip(game.name, CHOICE_NAME), value=str(game.id))
        for game in [*listed, *finished]
        if needle in game.name.casefold()
    ][:AUTOCOMPLETE]


async def game_choices_public(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for a `game` option that works with the bot's own view of the games."""
    return game_choices(interaction.client.cache.games, current)
