"""Commands that speak to a game's server as one of your slots: /hint, /hint_location, /remaining, /say, /release and
/collect. The manager keeps the player's text client connection; the bot only asks it (see services/text_client.py)."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.autocomplete import is_loading_choice, item_choices, location_choices
from apbot.cogs.common import SlotTarget, SlotText, begin_slot_command, call_manager, slot_options
from apbot.errors import UserFacingError
from apbot.i18n import language_of, t, translatable
from apbot.manager.errors import ManagerUnavailable
from apbot.manager.views import parse_tracker
from apbot.services.text_client import checked_text
from apbot.ui.embeds import notice_embed, server_answer_embed
from apbot.ui.format import safe_text
from apbot.ui.respond import reply
from apbot.ui.style import NAME_LIMIT, SUCCESS, WARNING
from apbot.ui.views import ConfirmView

if TYPE_CHECKING:
    from apbot.bot import ApBot

NAME_OPTION_LENGTH = 200  # an item or location name typed in full; the suggestions are cut at Discord's 100
SAY_LENGTH = 1000  # the manager's limit for one message
RESULT_LIMIT = 500  # of what the server says about a release or collect
SERVER_SILENT_STATUS = 504  # the manager's answer when the game's server did not answer a release or collect

# What the rules say when a release or collect cannot be done now, by action and rule.
_RULE_TEXTS = {
    ("release", "disabled"): translatable("Releasing is turned off in this game."),
    ("release", "done"): translatable("There is nothing left to release."),
    ("release", "automatic"): translatable("Your items are released by themselves when you reach your goal."),
    ("release", "after_goal"): translatable("You can release your items once you have reached your goal."),
    ("collect", "disabled"): translatable("Collecting is turned off in this game."),
    ("collect", "done"): translatable("There is nothing left to collect."),
    ("collect", "automatic"): translatable("Your items are collected by themselves when you reach your goal."),
    ("collect", "after_goal"): translatable("You can collect your items once you have reached your goal."),
}


def _name_text(name: str) -> str:
    """A name the person typed or picked, on one line. The suggestion shown while names load is not a name."""
    if is_loading_choice(name):
        raise UserFacingError("The names are still loading. Try again in a moment.")
    return " ".join(name.split())


def done_embed(result: dict, lang: str) -> discord.Embed:
    """What the server said about a release or collect (`{done, message}`)."""
    done = bool(result.get("done"))
    message = result.get("message")
    text = safe_text(message, RESULT_LIMIT) if isinstance(message, str) else ""
    if not text:
        text = t("Done", lang) if done else t("The server did not do it", lang)
    return notice_embed(text, SUCCESS if done else WARNING)


class TextClientCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    @app_commands.command(name=locale_str("hint"), description=locale_str("Ask the server for a hint about an item"))
    @slot_options
    @app_commands.describe(item=locale_str("The item"))
    @app_commands.rename(item=locale_str("item"))
    @app_commands.autocomplete(item=item_choices)
    async def hint(
        self,
        interaction: discord.Interaction,
        game: app_commands.Range[str, 1, 20],
        item: app_commands.Range[str, 1, NAME_OPTION_LENGTH],
        slot: SlotText | None = None,
    ) -> None:
        await self._ask(interaction, game, slot, f"!hint {_name_text(item)}")

    @app_commands.command(
        name=locale_str("hint_location"), description=locale_str("Ask the server for a hint about a location")
    )
    @slot_options
    @app_commands.describe(location=locale_str("The location"))
    @app_commands.rename(location=locale_str("location"))
    @app_commands.autocomplete(location=location_choices)
    async def hint_location(
        self,
        interaction: discord.Interaction,
        game: app_commands.Range[str, 1, 20],
        location: app_commands.Range[str, 1, NAME_OPTION_LENGTH],
        slot: SlotText | None = None,
    ) -> None:
        await self._ask(interaction, game, slot, f"!hint_location {_name_text(location)}")

    @app_commands.command(
        name=locale_str("remaining"), description=locale_str("Show how many items you still have to find")
    )
    @slot_options
    async def remaining(
        self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20], slot: SlotText | None = None
    ) -> None:
        await self._ask(interaction, game, slot, "!remaining")

    @app_commands.command(name=locale_str("say"), description=locale_str("Say something in the game's chat as your slot"))
    @slot_options
    @app_commands.describe(text=locale_str("What to say"))
    @app_commands.rename(text=locale_str("text"))
    async def say(
        self,
        interaction: discord.Interaction,
        game: app_commands.Range[str, 1, 20],
        text: app_commands.Range[str, 1, SAY_LENGTH],
        slot: SlotText | None = None,
    ) -> None:
        said = checked_text(text)  # a text the server would refuse is answered before anything is asked
        target = await begin_slot_command(self.bot, interaction, game, slot)
        await call_manager(
            self.bot,
            interaction,
            target.token,
            lambda token: self.bot.text_client.say(token, target.game_id, target.slot, said),
        )
        await reply(interaction, embed=notice_embed(t("Sent", language_of(interaction.locale)), SUCCESS), ephemeral=True)

    @app_commands.command(name=locale_str("release"), description=locale_str("Release all remaining items from your world"))
    @slot_options
    async def release(
        self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20], slot: SlotText | None = None
    ) -> None:
        await self._slot_action(interaction, game, slot, "release")

    @app_commands.command(
        name=locale_str("collect"), description=locale_str("Collect the items that other worlds still hold for you")
    )
    @slot_options
    async def collect(
        self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20], slot: SlotText | None = None
    ) -> None:
        await self._slot_action(interaction, game, slot, "collect")

    async def _ask(self, interaction: discord.Interaction, game: str, slot: str | None, text: str) -> None:
        """Says `text` (a server command) as the person's slot and shows what the server answers."""
        lang = language_of(interaction.locale)
        checked = checked_text(text)
        target = await begin_slot_command(self.bot, interaction, game, slot)
        answer = await call_manager(
            self.bot,
            interaction,
            target.token,
            lambda token: self.bot.text_client.run(token, target.game_id, target.slot, checked),
        )
        await reply(interaction, embed=server_answer_embed(answer.lines, answer.hidden, lang), ephemeral=True)

    async def _slot_action(self, interaction: discord.Interaction, game: str, slot: str | None, action: str) -> None:
        """Release or collect: only when the game's rules allow it now, and only after the person confirmed."""
        lang = language_of(interaction.locale)
        target = await begin_slot_command(self.bot, interaction, game, slot)
        raw = await call_manager(
            self.bot,
            interaction,
            target.token,
            lambda token: self.bot.manager.tracker(token, target.game_id, target.slot),
        )
        tracker = parse_tracker(raw)
        rule = tracker.release if action == "release" else tracker.collect
        if rule != "available":
            await reply(interaction, embed=notice_embed(t(_RULE_TEXTS[action, rule], lang), WARNING), ephemeral=True)
            return
        name = safe_text(tracker.name, NAME_LIMIT)
        if action == "release":
            question = t("Release all remaining items from {slot}? This cannot be undone.", lang, slot=name)
            confirm_label = t("Release items", lang)
        else:
            question = t(
                "Collect all items that other worlds still hold for {slot}? This cannot be undone.", lang, slot=name
            )
            confirm_label = t("Collect items", lang)
        view = ConfirmView(interaction.user.id, lang, confirm_label=confirm_label, danger=True)
        if not await view.ask(interaction, notice_embed(question)):
            return
        result = await self._run_action(interaction, target, action)
        await interaction.edit_original_response(embed=done_embed(result, lang), view=None)

    async def _run_action(self, interaction: discord.Interaction, target: SlotTarget, action: str) -> dict:
        try:
            return await call_manager(
                self.bot,
                interaction,
                target.token,
                lambda token: self.bot.manager.slot_action(token, target.game_id, target.slot, action),
            )
        except ManagerUnavailable as exc:
            if exc.status == SERVER_SILENT_STATUS:
                raise UserFacingError(
                    "The game's server did not answer in time. Look at /tracker in a minute to see whether it happened."
                ) from None
            raise
