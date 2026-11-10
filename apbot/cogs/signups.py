"""/signups lists the open sign-up rounds (public: the bot's own view). /signup works with one round as the person who
asks, with the manager token they linked: show it, join with a config file or a saved config, withdraw an entry, and for
organizers open, close and test a round. The manager decides what each person may do."""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.autocomplete import (
    config_choices,
    my_entry_choices,
    signup_choices_managed,
    signup_choices_open,
    signup_choices_visible,
)
from apbot.errors import DETAIL_LIMIT, UserFacingError
from apbot.i18n import language_of, t
from apbot.manager.errors import BadRequest, Conflict, Forbidden, ManagerUnavailable, NotFound
from apbot.manager.views import (
    EntryView,
    SignupDetail,
    open_signups,
    parse_added_warnings,
    parse_check,
    parse_config,
    parse_signup_detail,
    parse_signups,
)
from apbot.services.slots import number_or_none
from apbot.services.timeparse import parse_local_datetime
from apbot.ui.embeds import notice_embed, signups_embed
from apbot.ui.format import safe_text
from apbot.ui.respond import reply
from apbot.ui.signups import TEST_FINISHED, check_result_embed, entry_added_embed, signup_detail_embed
from apbot.ui.style import NAME_LIMIT, SUCCESS, WARNING
from apbot.ui.views import ConfirmView, open_in_manager_view

if TYPE_CHECKING:
    from apbot.bot import ApBot

ID_OPTION_LENGTH = 20  # digits of an id as text; Discord needs a limit on a text option
MAX_FILE_BYTES = 1024 * 1024  # the manager's limit for a config
CONFIG_EXTENSIONS = (".yaml", ".yml")
CONFIG_NAME_LENGTH = 120  # the manager's limit for the name of a config
ROUND_NAME_LENGTH = 120
DESCRIPTION_LENGTH = 2000
DEADLINE_LENGTH = 32
MAX_SLOTS_PER_USER = 20  # the manager's limit
TEST_POLL_INTERVAL = 10  # seconds between looks at a running test
TEST_POLLS = 60  # looks, so ten minutes at most
REFUSALS = (Conflict, BadRequest, Forbidden)  # the manager says no to something the person asked


def parse_round_id(value: str) -> int:
    number = number_or_none(value)
    if number is None:
        raise UserFacingError("Pick a round from the list")
    return number


def config_name(filename: str) -> str:
    """The name to save an uploaded config under: the file's name, cut to the manager's limit with its extension kept."""
    if len(filename) <= CONFIG_NAME_LENGTH:
        return filename
    stem, extension = os.path.splitext(filename)
    return stem[: CONFIG_NAME_LENGTH - len(extension)] + extension


def check_attachment(file: discord.Attachment) -> None:
    """Refuses a file that cannot be a config before anything is downloaded or asked."""
    if not file.filename.lower().endswith(CONFIG_EXTENSIONS):
        raise UserFacingError("Only .yaml and .yml files can be entered")
    if file.size > MAX_FILE_BYTES:
        raise UserFacingError("The file is larger than 1 MiB")


async def read_attachment(file: discord.Attachment) -> str:
    """The text of a config file, without a byte order mark."""
    try:
        data = await file.read()
    except discord.HTTPException:
        raise UserFacingError("The file could not be downloaded from Discord. Try again") from None
    if len(data) > MAX_FILE_BYTES:
        raise UserFacingError("The file is larger than 1 MiB")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UserFacingError("The file is not UTF-8 text") from None


class SignupsCog(commands.Cog):
    def __init__(self, bot: "ApBot", *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.bot = bot
        self._sleep = sleep

    signup = app_commands.Group(
        name=locale_str("signup"), description=locale_str("Look at a sign-up round, join it, or run it")
    )

    @app_commands.command(name=locale_str("signups"), description=locale_str("Show the open sign-up rounds"))
    async def signups(self, interaction: discord.Interaction) -> None:
        lang = language_of(interaction.locale)
        await interaction.response.defer()
        raw = await self.bot.manager.list_signups(self.bot.config.manager_api_token)
        await reply(interaction, embed=signups_embed(open_signups(parse_signups(raw)), lang))

    # --- everyone who linked their account ------------------------------------------------------------------------
    @signup.command(name=locale_str("show"), description=locale_str("Show a sign-up round and your entries"))
    @app_commands.describe(round=locale_str("The sign-up round"))
    @app_commands.rename(round=locale_str("round"))
    @app_commands.autocomplete(round=signup_choices_visible)
    async def show(self, interaction: discord.Interaction, round: app_commands.Range[str, 1, ID_OPTION_LENGTH]) -> None:
        lang = language_of(interaction.locale)
        signup_id = parse_round_id(round)
        token = self.bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True)
        detail = await self._round(interaction, token, signup_id)
        await reply(
            interaction,
            embed=signup_detail_embed(detail, lang),
            view=open_in_manager_view(self.bot.cache.site_url, f"/signups/{detail.id}", lang),
            ephemeral=True,
        )

    @signup.command(name=locale_str("join"), description=locale_str("Enter a config file or one of your saved configs"))
    @app_commands.describe(
        round=locale_str("The sign-up round"),
        file=locale_str("A YAML file; it is checked, saved in your manager library and entered"),
        config=locale_str("One of the configs you saved in the manager"),
    )
    @app_commands.rename(round=locale_str("round"), file=locale_str("file"), config=locale_str("config"))
    @app_commands.autocomplete(round=signup_choices_open, config=config_choices)
    async def join(
        self,
        interaction: discord.Interaction,
        round: app_commands.Range[str, 1, ID_OPTION_LENGTH],
        file: discord.Attachment | None = None,
        config: app_commands.Range[str, 1, ID_OPTION_LENGTH] | None = None,
    ) -> None:
        lang = language_of(interaction.locale)
        signup_id = parse_round_id(round)
        if file is not None and config is not None:
            raise UserFacingError("Attach a file or pick a saved config, not both")
        if file is None and config is None:
            raise UserFacingError("Attach a YAML file or pick one of your saved configs")
        config_id = None
        if config is not None:
            config_id = number_or_none(config)
            if config_id is None:
                raise UserFacingError("Pick a config from the list")
        if file is not None:
            check_attachment(file)
        token = self.bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True)
        saved_as = None
        if file is not None:
            saved = await self._save_checked(interaction, token, file)
            if saved is None:
                return
            config_id, saved_as = saved.id, saved.name
        raw = await self._add_entry(interaction, token, signup_id, config_id, saved_as)
        detail = parse_signup_detail(raw)
        entry = next((e for e in detail.entries if e.mine and e.config_id == config_id), None)
        self.bot.signup_directory.forget_entries(interaction.user.id, signup_id)
        await reply(interaction, embed=entry_added_embed(entry, detail, parse_added_warnings(raw), lang), ephemeral=True)

    @signup.command(name=locale_str("withdraw"), description=locale_str("Take one of your entries out of a round"))
    @app_commands.describe(round=locale_str("The sign-up round"), entry=locale_str("The entry to withdraw"))
    @app_commands.rename(round=locale_str("round"), entry=locale_str("entry"))
    @app_commands.autocomplete(round=signup_choices_open, entry=my_entry_choices)
    async def withdraw(
        self,
        interaction: discord.Interaction,
        round: app_commands.Range[str, 1, ID_OPTION_LENGTH],
        entry: app_commands.Range[str, 1, ID_OPTION_LENGTH],
    ) -> None:
        lang = language_of(interaction.locale)
        signup_id = parse_round_id(round)
        entry_id = number_or_none(entry)
        if entry_id is None:
            raise UserFacingError("Pick an entry from the list")
        token = self.bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True)
        detail = await self._round(interaction, token, signup_id)
        mine = next((e for e in detail.entries if e.mine and e.id == entry_id), None)
        if mine is None:
            raise UserFacingError("You have no such entry in this round. Pick one from the list.")
        names = {"entry": _entry_name(mine), "round": safe_text(detail.name, NAME_LIMIT)}
        view = ConfirmView(interaction.user.id, lang, confirm_label=t("Withdraw entry", lang), danger=True)
        if not await view.ask(interaction, notice_embed(t("Withdraw **{entry}** from **{round}**?", lang, **names))):
            return
        raw = await self._ask(interaction, token, lambda tk: self.bot.manager.remove_entry(tk, signup_id, entry_id))
        after = parse_signup_detail(raw)
        self.bot.signup_directory.forget_entries(interaction.user.id, signup_id)
        message = t(
            "Withdrew **{entry}** from **{round}**. You use {used} of {limit} slots.",
            lang,
            used=after.my_slot_count,
            limit=after.slots_per_user,
            **names,
        )
        await interaction.edit_original_response(embed=notice_embed(message, SUCCESS), view=None)

    # --- organizers -----------------------------------------------------------------------------------------------
    @signup.command(name=locale_str("open"), description=locale_str("Open a new sign-up round"))
    @app_commands.describe(
        name=locale_str("The name of the round"),
        deadline=locale_str("When it closes, as YYYY-MM-DD HH:MM in the server's time zone; empty for no deadline"),
        slots_per_user=locale_str("How many slots each player may enter"),
        description=locale_str("What the round is about"),
    )
    @app_commands.rename(
        name=locale_str("name"),
        deadline=locale_str("deadline"),
        slots_per_user=locale_str("slots_per_user"),
        description=locale_str("description"),
    )
    async def open_round(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, ROUND_NAME_LENGTH],
        deadline: app_commands.Range[str, 1, DEADLINE_LENGTH] | None = None,
        slots_per_user: app_commands.Range[int, 1, MAX_SLOTS_PER_USER] = 1,
        description: app_commands.Range[str, 1, DESCRIPTION_LENGTH] | None = None,
    ) -> None:
        lang = language_of(interaction.locale)
        self.bot.access.require_role(interaction, "organizer")
        closes_at = self._deadline(interaction, deadline)
        token = self.bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True)
        raw = await self._ask(
            interaction,
            token,
            lambda tk: self.bot.manager.create_signup(
                tk, name.strip(), (description or "").strip() or None, closes_at, slots_per_user
            ),
        )
        detail = parse_signup_detail(raw)
        self.bot.signup_directory.forget_rounds(interaction.user.id)
        await reply(
            interaction,
            embed=signup_detail_embed(detail, lang),
            view=open_in_manager_view(self.bot.cache.site_url, f"/signups/{detail.id}", lang),
            ephemeral=True,
        )

    @signup.command(name=locale_str("close"), description=locale_str("Close a sign-up round"))
    @app_commands.describe(round=locale_str("The sign-up round"))
    @app_commands.rename(round=locale_str("round"))
    @app_commands.autocomplete(round=signup_choices_managed)
    async def close_round(
        self, interaction: discord.Interaction, round: app_commands.Range[str, 1, ID_OPTION_LENGTH]
    ) -> None:
        lang = language_of(interaction.locale)
        self.bot.access.require_role(interaction, "organizer")
        signup_id = parse_round_id(round)
        token = self.bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True)
        detail = await self._round(interaction, token, signup_id)
        counts = {"entries": detail.entry_count, "players": detail.user_count}
        question = t(
            "Close the sign-ups of **{round}**? Players can no longer enter or withdraw. It has {entries} entries from {players} players.",
            lang,
            round=safe_text(detail.name, NAME_LIMIT),
            **counts,
        )
        view = ConfirmView(interaction.user.id, lang, confirm_label=t("Close sign-ups", lang))
        if not await view.ask(interaction, notice_embed(question)):
            return
        raw = await self._ask(interaction, token, lambda tk: self.bot.manager.close_signup(tk, signup_id))
        closed = parse_signup_detail(raw)
        self.bot.signup_directory.forget_rounds(interaction.user.id)
        message = t(
            "Closed the sign-ups of **{round}**. It has {entries} entries from {players} players.",
            lang,
            round=safe_text(closed.name, NAME_LIMIT),
            entries=closed.entry_count,
            players=closed.user_count,
        )
        await interaction.edit_original_response(embed=notice_embed(message, SUCCESS), view=None)

    @signup.command(name=locale_str("test"), description=locale_str("Test-generate the entries of a round"))
    @app_commands.describe(round=locale_str("The sign-up round"))
    @app_commands.rename(round=locale_str("round"))
    @app_commands.autocomplete(round=signup_choices_managed)
    async def test_round(
        self, interaction: discord.Interaction, round: app_commands.Range[str, 1, ID_OPTION_LENGTH]
    ) -> None:
        lang = language_of(interaction.locale)
        self.bot.access.require_role(interaction, "organizer")
        signup_id = parse_round_id(round)
        token = self.bot.access.user_token(interaction)
        await interaction.response.defer(ephemeral=True)
        started = parse_signup_detail(
            await self._ask(interaction, token, lambda tk: self.bot.manager.test_signup(tk, signup_id))
        )
        message = t(
            "Testing **{round}**: the manager generates the current entries without keeping the result. The result appears here.",
            lang,
            round=safe_text(started.name, NAME_LIMIT),
        )
        await reply(interaction, embed=notice_embed(message), ephemeral=True)
        await interaction.edit_original_response(embed=await self._test_result(interaction, token, signup_id, lang))

    # --- the parts ------------------------------------------------------------------------------------------------
    async def _ask[T](self, interaction: discord.Interaction, token: str, call: Callable[[str], Awaitable[T]]) -> T:
        """Asks the manager as the person. A round it does not show them is explained, not shown as "not found"."""
        try:
            return await self.bot.access.call_as(interaction, token, call)
        except NotFound:
            raise UserFacingError("That round or config was not found. Pick them from the list.") from None

    async def _round(self, interaction: discord.Interaction, token: str, signup_id: int) -> SignupDetail:
        return parse_signup_detail(await self._ask(interaction, token, lambda tk: self.bot.manager.signup(tk, signup_id)))

    def _deadline(self, interaction: discord.Interaction, text: str | None) -> float | None:
        """The deadline typed on the clock of the server, as unix time; None when none was typed."""
        if text is None:
            return None
        moment = parse_local_datetime(text, self.bot.guild_settings.get(interaction.guild.id).timezone)
        if moment <= time.time():
            raise UserFacingError("The deadline is in the past")
        return moment

    async def _save_checked(self, interaction: discord.Interaction, token: str, file: discord.Attachment):
        """Checks the file with the manager, shows the result and, when the manager takes it, saves it in the person's
        library. Returns the saved config, or None when the check found problems (then nothing was saved)."""
        lang = language_of(interaction.locale)
        content = await read_attachment(file)
        raw = await self._ask(interaction, token, lambda tk: self.bot.manager.check_config(tk, content))
        result = parse_check(raw)
        await reply(interaction, embed=check_result_embed(result, file.filename, lang), ephemeral=True)
        if not result.ok:
            return None
        saved = parse_config(
            await self._ask(
                interaction, token, lambda tk: self.bot.manager.create_config(tk, config_name(file.filename), content)
            )
        )
        self.bot.signup_directory.forget_configs(interaction.user.id)
        return saved

    async def _add_entry(
        self, interaction: discord.Interaction, token: str, signup_id: int, config_id: int, saved_as: str | None
    ):
        """Enters a saved config. When it was just saved from a file, a refusal says so, so the person knows the file
        is in their library now."""
        try:
            return await self._ask(interaction, token, lambda tk: self.bot.manager.add_entry(tk, signup_id, config_id))
        except REFUSALS as exc:
            if saved_as is None:
                raise
            raise UserFacingError(
                "Saved as **{name}** in your manager library, but it could not be entered: {detail}",
                name=safe_text(saved_as, NAME_LIMIT),
                detail=safe_text(exc.detail, DETAIL_LIMIT),
            ) from None

    async def _test_result(
        self, interaction: discord.Interaction, token: str, signup_id: int, lang: str
    ) -> discord.Embed:
        """Looks at the round every few seconds until its test is over (or ten minutes passed)."""
        for _ in range(TEST_POLLS):
            await self._sleep(TEST_POLL_INTERVAL)
            try:
                detail = await self._round(interaction, token, signup_id)
            except ManagerUnavailable:
                continue  # the manager may be busy; the next look will do
            if detail.test is not None and detail.test.status in TEST_FINISHED:
                return signup_detail_embed(detail, lang)
        text = t("The test is still running. Look again later with /signup show.", lang)
        return notice_embed(text, WARNING)


def _entry_name(entry: EntryView) -> str:
    return safe_text(entry.name or "?", NAME_LIMIT)
