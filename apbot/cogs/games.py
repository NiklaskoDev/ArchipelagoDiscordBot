"""/games and /game show what is running on the manager (public: the bot's own view, never a user's). A linked person
also gets the games only their own account sees, in replies that only they see.
/wake, /start, /stop and /restart act as the person who asks, with the token they linked."""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.autocomplete import game_choices_visible
from apbot.errors import NotLinkedError, UserFacingError, user_message
from apbot.i18n import language_of, t
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError, NotFound
from apbot.manager.views import GameSummary, listed_games, parse_game_detail, parse_game_summary
from apbot.services.my_games import private_games
from apbot.ui.embeds import error_embed, game_detail_embeds, game_state_embed, games_list_embed, private_games_embed
from apbot.ui.respond import reply
from apbot.ui.views import open_in_manager_view

if TYPE_CHECKING:
    from apbot.bot import ApBot

log = logging.getLogger(__name__)

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
        token, problem = self._personal_token(interaction)
        if not self.bot.cache.loaded:
            await interaction.response.defer()  # the first list has to come from the manager
        games = await self.bot.cache.get()
        await reply(interaction, embed=games_list_embed(listed_games(games), lang))
        # From here on nothing may be raised: the error handler would delete the public list that is already sent.
        if problem is not None:
            await interaction.followup.send(embed=error_embed(user_message(problem, lang)), ephemeral=True)
        elif token is not None:
            await self._send_private_games(interaction, token, games, lang)

    def _personal_token(self, interaction: discord.Interaction) -> tuple[str | None, UserFacingError | None]:
        """The caller's manager token, or why there is none. Not linked is no problem: they just have no private games."""
        try:
            return self.bot.access.user_token(interaction), None
        except NotLinkedError:
            return None, None
        except UserFacingError as exc:
            return None, exc

    async def _send_private_games(
        self, interaction: discord.Interaction, token: str, public: list[GameSummary], lang: str
    ) -> None:
        """The games only the caller's account sees, in a message only the caller sees."""
        try:
            mine = await self.bot.access.call_as(
                interaction, token, lambda user_token: self.bot.my_games.games(interaction.user.id, user_token)
            )
        except UserFacingError as exc:
            await interaction.followup.send(embed=error_embed(user_message(exc, lang)), ephemeral=True)
            return
        except (ManagerError, TypeError) as exc:
            log.debug("The private games could not be loaded: %s", exc)
            text = t("Your private games could not be loaded right now. Try again later.", lang)
            await interaction.followup.send(embed=error_embed(text), ephemeral=True)
            return
        extra = listed_games(private_games(mine, public))
        if extra:
            await interaction.followup.send(embed=private_games_embed(extra, lang), ephemeral=True)

    @app_commands.command(name=locale_str("game"), description=locale_str("Show one game in detail"))
    @app_commands.describe(game=locale_str("The game to show"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_visible)
    async def game(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        lang = language_of(interaction.locale)
        game_id = parse_game_id(game)
        token = None if self._is_public(game_id) else self._personal_token(interaction)[0]
        # Public or private has to be settled before deferring: a deferred public reply cannot become ephemeral.
        visibility = {} if token is None else {"ephemeral": True}
        await interaction.response.defer(**visibility)
        try:
            if token is None:
                raw = await self.bot.manager.game(self.bot.config.manager_api_token, game_id)
            else:
                raw = await self.bot.access.call_as(
                    interaction, token, lambda user_token: self.bot.manager.game(user_token, game_id)
                )
        except NotFound:
            raise UserFacingError("That game was not found. Pick one from the list.") from None
        detail = parse_game_detail(raw)
        await reply(
            interaction,
            embeds=game_detail_embeds(detail, lang),
            view=open_in_manager_view(self.bot.cache.site_url, f"/games/{detail.id}", lang),
            **visibility,
        )

    def _is_public(self, game_id: int) -> bool:
        return any(game.id == game_id for game in self.bot.cache.games)

    @app_commands.command(name=locale_str("wake"), description=locale_str("Wake up a sleeping game"))
    @app_commands.describe(game=locale_str("The game"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_visible)
    async def wake(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        await self._control(interaction, game, ManagerClient.wake)

    @app_commands.command(name=locale_str("start"), description=locale_str("Start a game"))
    @app_commands.describe(game=locale_str("The game"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_visible)
    @app_commands.guild_only()
    async def start(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        await self._control(interaction, game, ManagerClient.start, organizers_only=True)

    @app_commands.command(name=locale_str("stop"), description=locale_str("Stop a game"))
    @app_commands.describe(game=locale_str("The game"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_visible)
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        await self._control(interaction, game, ManagerClient.stop, organizers_only=True)

    @app_commands.command(name=locale_str("restart"), description=locale_str("Restart a game"))
    @app_commands.describe(game=locale_str("The game"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=game_choices_visible)
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
        token = self.bot.access.user_token(interaction)  # not linked: say so before anything is shown as "thinking"
        await interaction.response.defer(ephemeral=True)
        try:
            raw = await self.bot.access.call_as(
                interaction, token, lambda user_token: action(self.bot.manager, user_token, game_id)
            )
        except NotFound:
            raise UserFacingError("That game was not found. Pick one from the list.") from None
        await reply(interaction, embed=game_state_embed(parse_game_summary(raw), lang), ephemeral=True)
