"""Commands about your own slots: /status says how you are doing, /claim takes a free slot, /handover offers a slot to
someone else or takes the offer back. Everything is done as the linked person; the manager decides what is allowed."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.autocomplete import claimable_slot_choices, game_choices_public
from apbot.cogs.common import SlotText, begin_slot_command, call_manager, slot_label, slot_options
from apbot.errors import UserFacingError
from apbot.i18n import language_of, t
from apbot.manager.views import parse_game_detail
from apbot.ui.embeds import PLAYER_STATUS_LABELS, notice_embed
from apbot.ui.format import safe_text
from apbot.ui.respond import reply
from apbot.ui.style import NAME_LIMIT, SUCCESS

if TYPE_CHECKING:
    from apbot.bot import ApBot

NOTE_LENGTH = 140  # the manager's limit for a status note and a handover note
USERNAME_LENGTH = 100  # the manager's limit for a username
CLEAR_STATUS = "clear"

STATUS_CHOICES = [
    app_commands.Choice(name=locale_str("playing"), value="playing"),
    app_commands.Choice(name=locale_str("stuck"), value="stuck"),
    app_commands.Choice(name=locale_str("away"), value="away"),
    app_commands.Choice(name=locale_str("clear"), value=CLEAR_STATUS),
]


class SlotsCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    handover = app_commands.Group(
        name=locale_str("handover"), description=locale_str("Offer one of your slots to someone else")
    )

    @app_commands.command(
        name=locale_str("status"), description=locale_str("Tell the others how you are doing in a game")
    )
    @slot_options
    @app_commands.describe(status=locale_str("How you are doing"), note=locale_str("A short note for the others"))
    @app_commands.rename(status=locale_str("status"), note=locale_str("note"))
    @app_commands.choices(status=STATUS_CHOICES)
    async def status(
        self,
        interaction: discord.Interaction,
        game: app_commands.Range[str, 1, 20],
        status: str,
        slot: SlotText | None = None,
        note: app_commands.Range[str, 1, NOTE_LENGTH] | None = None,
    ) -> None:
        lang = language_of(interaction.locale)
        target = await begin_slot_command(self.bot, interaction, game, slot)
        new_status = None if status == CLEAR_STATUS else status
        raw = await call_manager(
            self.bot,
            interaction,
            target.token,
            lambda token: self.bot.manager.set_slot_status(
                token, target.game_id, target.slot, new_status, note or ""
            ),
        )
        name = slot_label(raw, target.slot)
        if new_status is None:
            message = t("The status of **{slot}** is cleared", lang, slot=name)
        else:
            message = t("The status of **{slot}** is now: {status}", lang, slot=name, status=t(PLAYER_STATUS_LABELS[new_status], lang))
        await reply(interaction, embed=notice_embed(message, SUCCESS), ephemeral=True)

    @app_commands.command(name=locale_str("claim"), description=locale_str("Claim a free slot in a game"))
    @app_commands.describe(game=locale_str("The game"), slot=locale_str("The slot to claim"))
    @app_commands.rename(game=locale_str("game"), slot=locale_str("slot"))
    @app_commands.autocomplete(game=game_choices_public, slot=claimable_slot_choices)
    async def claim(
        self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20], slot: SlotText
    ) -> None:
        lang = language_of(interaction.locale)
        target = await begin_slot_command(self.bot, interaction, game, slot)
        raw = await call_manager(
            self.bot,
            interaction,
            target.token,
            lambda token: self.bot.manager.claim(token, target.game_id, target.slot),
        )
        self.bot.slots.forget(interaction.user.id, target.game_id)  # what may be claimed changed
        detail = parse_game_detail(raw)
        message = t(
            "You claimed **{slot}** in **{game}**",
            lang,
            slot=slot_label(raw, target.slot),
            game=safe_text(detail.name, NAME_LIMIT),
        )
        await reply(interaction, embed=notice_embed(message, SUCCESS), ephemeral=True)

    @handover.command(name=locale_str("offer"), description=locale_str("Offer one of your slots to someone"))
    @slot_options
    @app_commands.describe(
        member=locale_str("A Discord member who linked their manager account"),
        username=locale_str("A manager username; leave both empty to offer it to anyone"),
        note=locale_str("A short note for whoever takes it"),
    )
    @app_commands.rename(member=locale_str("member"), username=locale_str("username"), note=locale_str("note"))
    async def offer(
        self,
        interaction: discord.Interaction,
        game: app_commands.Range[str, 1, 20],
        slot: SlotText | None = None,
        member: discord.User | discord.Member | None = None,
        username: app_commands.Range[str, 1, USERNAME_LENGTH] | None = None,
        note: app_commands.Range[str, 1, NOTE_LENGTH] | None = None,
    ) -> None:
        lang = language_of(interaction.locale)
        recipient = self._recipient(member, username)
        target = await begin_slot_command(self.bot, interaction, game, slot)
        raw = await call_manager(
            self.bot,
            interaction,
            target.token,
            lambda token: self.bot.manager.offer_handover(token, target.game_id, target.slot, recipient, note or ""),
        )
        name = slot_label(raw, target.slot)
        if recipient is None:
            message = t("You offered **{slot}** to anyone who takes it", lang, slot=name)
        else:
            message = t("You offered **{slot}** to **{user}**", lang, slot=name, user=safe_text(recipient, NAME_LIMIT))
        await reply(interaction, embed=notice_embed(message, SUCCESS), ephemeral=True)

    @handover.command(name=locale_str("cancel"), description=locale_str("Take back the offer of one of your slots"))
    @slot_options
    async def cancel(
        self, interaction: discord.Interaction, game: app_commands.Range[str, 1, 20], slot: SlotText | None = None
    ) -> None:
        lang = language_of(interaction.locale)
        target = await begin_slot_command(self.bot, interaction, game, slot)
        raw = await call_manager(
            self.bot,
            interaction,
            target.token,
            lambda token: self.bot.manager.cancel_handover(token, target.game_id, target.slot),
        )
        message = t("The offer of **{slot}** is taken back", lang, slot=slot_label(raw, target.slot))
        await reply(interaction, embed=notice_embed(message, SUCCESS), ephemeral=True)

    def _recipient(self, member: discord.User | discord.Member | None, username: str | None) -> str | None:
        """The manager username to offer the slot to: that of the member's linked account, the one typed, or None for
        an offer to whoever takes it."""
        if member is not None and username is not None:
            raise UserFacingError("Choose either a member or a username, not both")
        if member is not None:
            link = self.bot.links.get(member.id)
            if link is None:
                raise UserFacingError("{member} has not linked a manager account", member=f"<@{member.id}>")
            return link.manager_username
        return username.strip() if username is not None else None
