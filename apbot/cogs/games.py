"""/games and /game show what is running on the manager (public: the bot's own view, never a user's).
/wake, /start, /stop and /restart act as the person who asks, with the token they linked."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.autocomplete import game_choices_public
from apbot.errors import UserFacingError
from apbot.i18n import language_of
from apbot.manager.client import ManagerClient
from apbot.manager.errors import NotFound
from apbot.manager.views import listed_games, parse_game_detail, parse_game_summary
from apbot.ui.embeds import game_detail_embeds, game_state_embed, games_list_embed
from apbot.ui.respond import reply
from apbot.ui.views import open_in_manager_view

if TYPE_CHECKING:
    from apbot.bot import ApBot

MAX_GAME_ID_DIGITS = 9  # ids come from the autocomplete; this only rejects typed nonsense early


def parse_game_id(value: str) -> int:
    text = value.strip()
    if not text.isascii() or not text.isdecimal() or len(text) > MAX_GAME_ID_DIGITS:
        raise UserFacingError("Pick a game from the list")
    return int(text)


class GamesCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    @app_commands.command(
        name=locale_str("games"), description=locale_str("Show the games on the Archipelago manager")
    )
    async def games(self, interaction: discord.Interaction) -> None:
        lang = language_of(interaction.locale)
        if not self.bot.cache.loaded:
            await interaction.response.defer()  # the first list has to come from the manager
        games = await self.bot.cache.get()
        await reply(interaction, embed=games_list_embed(listed_games(games), lang))

    @app_commands.command(name=locale_str("game"), description=locale_str("Show one game in detail"))
    @app_commands.describe(game=locale_str("The game to show"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_public)
    async def game(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        lang = language_of(interaction.locale)
        game_id = parse_game_id(game)
        await interaction.response.defer()
        try:
            raw = await self.bot.manager.game(self.bot.config.manager_api_token, game_id)
        except NotFound:
            raise UserFacingError("That game was not found. Pick one from the list.") from None
        detail = parse_game_detail(raw)
        await reply(
            interaction,
            embeds=game_detail_embeds(detail, lang),
            view=open_in_manager_view(self.bot.cache.site_url, f"/games/{detail.id}", lang),
        )

    @app_commands.command(name=locale_str("wake"), description=locale_str("Wake up a sleeping game"))
    @app_commands.describe(game=locale_str("The game"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_public)
    async def wake(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        await self._control(interaction, game, ManagerClient.wake)

    @app_commands.command(name=locale_str("start"), description=locale_str("Start a game"))
    @app_commands.describe(game=locale_str("The game"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_public)
    @app_commands.guild_only()
    async def start(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        await self._control(interaction, game, ManagerClient.start, organizers_only=True)

    @app_commands.command(name=locale_str("stop"), description=locale_str("Stop a game"))
    @app_commands.describe(game=locale_str("The game"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_public)
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        await self._control(interaction, game, ManagerClient.stop, organizers_only=True)

    @app_commands.command(name=locale_str("restart"), description=locale_str("Restart a game"))
    @app_commands.describe(game=locale_str("The game"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_public)
    @app_commands.guild_only()
    async def restart(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        await self._control(interaction, game, ManagerClient.restart, organizers_only=True)

    async def _control(
        self,
        interaction: discord.Interaction,
        game: str,
        action: Callable[[ManagerClient, str, int], Awaitable[Any]],
        *,
        organizers_only: bool = False,
    ) -> None:
        """Runs a wake, start, stop or restart as the person who asked and tells them what state the game is in.
        The manager decides whether they may (its refusal is shown as it is); the server's organizer role is checked
        on top for start, stop and restart."""
        lang = language_of(interaction.locale)
        game_id = parse_game_id(game)
        if organizers_only:
            self.bot.access.require_role(interaction, "organizer")
        self.bot.access.user_token(interaction)  # not linked: say so before anything is shown as "thinking"
        await interaction.response.defer(ephemeral=True)
        try:
            raw = await self.bot.access.run(interaction, lambda token: action(self.bot.manager, token, game_id))
        except NotFound:
            raise UserFacingError("That game was not found. Pick one from the list.") from None
        await reply(interaction, embed=game_state_embed(parse_game_summary(raw), lang), ephemeral=True)
