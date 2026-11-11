"""/games and /game show what is running on the manager (public: the bot's own view, never a user's). A linked person
also gets the games only their own account sees, in replies that only they see.
/wake, /start, /stop, /restart, /game_archive and /game_delete act as the person who asks, with the token they linked."""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.autocomplete import archive_game_choices, delete_game_choices, game_choices_visible
from apbot.errors import NotLinkedError, UserFacingError, user_message
from apbot.i18n import language_of, t
from apbot.manager.client import ManagerClient
from apbot.manager.errors import ManagerError, NotFound
from apbot.manager.views import GameDetail, GameSummary, listed_games, parse_game_detail, parse_game_summary
from apbot.services.my_games import private_games
from apbot.ui.embeds import (
    error_embed,
    game_archived_embed,
    game_detail_embeds,
    game_state_embed,
    games_list_embed,
    notice_embed,
    private_games_embed,
)
from apbot.ui.format import safe_text
from apbot.ui.respond import reply
from apbot.ui.style import NAME_LIMIT, SUCCESS
from apbot.ui.views import ConfirmView, open_in_manager_view

if TYPE_CHECKING:
    from apbot.bot import ApBot

log = logging.getLogger(__name__)

MAX_GAME_ID_DIGITS = 9  # ids come from the autocomplete; this only rejects typed nonsense early
ARCHIVE = "archive"
RESTORE = "restore"
ARCHIVE_ACTIONS = [
    app_commands.Choice(name=locale_str("archive"), value=ARCHIVE),
    app_commands.Choice(name=locale_str("restore"), value=RESTORE),
]


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

    # --- the archive and deleting: the manager decides who may; the bot only asks first -----------------------------
    @app_commands.command(
        name=locale_str("game_archive"), description=locale_str("Move a game to the archive, or take it out again")
    )
    @app_commands.describe(
        action=locale_str("Move the game to the archive or take it out"), game=locale_str("The game")
    )
    @app_commands.rename(action=locale_str("action"), game=locale_str("game"))
    @app_commands.choices(action=ARCHIVE_ACTIONS)
    @app_commands.autocomplete(game=archive_game_choices)
    @app_commands.guild_only()
    async def game_archive(
        self, interaction: discord.Interaction, action: str, game: app_commands.Range[str, 1, 20]
    ) -> None:
        lang = language_of(interaction.locale)
        game_id = parse_game_id(game)
        restoring = action == RESTORE
        self.bot.access.require_role(interaction, "organizer")
        token = self.bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True)
        detail = await self._game_of(interaction, token, game_id)
        if not detail.can_archive:
            raise UserFacingError(
                "Only whoever runs this game, or the player who generated it, can move it to the archive"
            )
        if not restoring and detail.finished_at is not None:
            raise UserFacingError("That game is in the archive already")
        if restoring and detail.finished_at is None:
            raise UserFacingError("That game is not in the archive")
        name = safe_text(detail.name, NAME_LIMIT)
        if restoring:
            question = t("Take **{game}** out of the archive?", lang, game=name)
            view = ConfirmView(interaction.user.id, lang, confirm_label=t("Back to running games", lang))
        else:
            question = t(
                "Move **{game}** to the archive? Its server stops. It is listed as finished if every player reached their goal, otherwise as aborted.",
                lang,
                game=name,
            )
            view = ConfirmView(interaction.user.id, lang, confirm_label=t("Move to archive", lang))
        if not await view.ask(interaction, notice_embed(question)):
            return
        raw = await self._ask(interaction, token, lambda tk: self.bot.manager.archive_game(tk, game_id, not restoring))
        await interaction.edit_original_response(embed=game_archived_embed(parse_game_summary(raw), lang), view=None)
        await self._games_changed(interaction)

    @app_commands.command(name=locale_str("game_delete"), description=locale_str("Delete a game for good"))
    @app_commands.describe(game=locale_str("The game"))
    @app_commands.rename(game=locale_str("game"))
    @app_commands.autocomplete(game=delete_game_choices)
    @app_commands.guild_only()
    async def game_delete(self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20]) -> None:
        lang = language_of(interaction.locale)
        game_id = parse_game_id(game)
        self.bot.access.require_role(interaction, "organizer")
        token = self.bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True)
        detail = await self._game_of(interaction, token, game_id)
        if not detail.can_manage:
            raise UserFacingError("Only the organizer of this game and administrators can delete it")
        name = safe_text(detail.name, NAME_LIMIT)
        question = t(
            "Delete **{game}** for good? Its files, save and history are removed from the manager. This cannot be undone.",
            lang,
            game=name,
        )
        view = ConfirmView(interaction.user.id, lang, confirm_label=t("Delete game", lang), danger=True)
        if not await view.ask(interaction, notice_embed(question)):
            return
        await self._ask(interaction, token, lambda tk: self.bot.manager.delete_game(tk, game_id))
        await interaction.edit_original_response(
            embed=notice_embed(t("**{game}** was deleted", lang, game=name), SUCCESS), view=None
        )
        await self._games_changed(interaction)

    async def _ask[T](self, interaction: discord.Interaction, token: str, call: Callable[[str], Awaitable[T]]) -> T:
        """Asks the manager as the person. A game it does not show them is explained, not shown as "not found"."""
        try:
            return await self.bot.access.call_as(interaction, token, call)
        except NotFound:
            raise UserFacingError("That game was not found. Pick one from the list.") from None

    async def _game_of(self, interaction: discord.Interaction, token: str, game_id: int) -> GameDetail:
        return parse_game_detail(await self._ask(interaction, token, lambda tk: self.bot.manager.game(tk, game_id)))

    async def _games_changed(self, interaction: discord.Interaction) -> None:
        """A game moved or went away: the person's list and the public one are out of date."""
        self.bot.my_games.forget(interaction.user.id)
        await self.bot.cache.run_refresh()
