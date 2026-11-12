"""/config share: lets a linked person share one of their saved configs with every player, all their friends or chosen
friends, or stop sharing it. It acts as the person, with the manager token they linked; the manager decides who may
see a shared config. Sharing by friend is the manager's friends feature: on a manager that does not have it the person
is told so, and nothing is sent."""

from typing import TYPE_CHECKING, Final

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.autocomplete import config_choices
from apbot.errors import UserFacingError
from apbot.i18n import language_of, t
from apbot.manager.views import ConfigView, FriendView, parse_configs
from apbot.services.slots import number_or_none
from apbot.ui.embeds import notice_embed
from apbot.ui.format import clip, safe_text
from apbot.ui.respond import reply
from apbot.ui.style import CHOICE_NAME, NAME_LIMIT, SELECT_OPTIONS, SUCCESS
from apbot.ui.views import ConfirmView, MultiSelectView

if TYPE_CHECKING:
    from apbot.bot import ApBot

CONFIG_OPTION_LENGTH = 20  # digits of an id as text; Discord needs a limit on a text option
EVERYONE: Final = "everyone"
FRIENDS: Final = "friends"
CHOSEN: Final = "chosen"
STOP: Final = "stop"
AUDIENCES = [
    app_commands.Choice(name=locale_str("everyone"), value=EVERYONE),
    app_commands.Choice(name=locale_str("friends"), value=FRIENDS),
    app_commands.Choice(name=locale_str("chosen"), value=CHOSEN),
    app_commands.Choice(name=locale_str("stop"), value=STOP),
]


class ConfigsCog(commands.Cog):
    def __init__(self, bot: "ApBot"):
        self.bot = bot

    config = app_commands.Group(name=locale_str("config"), description=locale_str("Your saved configs on the manager"))

    @config.command(name=locale_str("share"), description=locale_str("Share one of your configs, or stop sharing it"))
    @app_commands.describe(
        config=locale_str("The config to share"), audience=locale_str("Who may use it, or stop to keep it to yourself")
    )
    @app_commands.rename(config=locale_str("config"), audience=locale_str("audience"))
    @app_commands.choices(audience=AUDIENCES)
    @app_commands.autocomplete(config=config_choices)
    async def share(
        self, interaction: discord.Interaction, config: app_commands.Range[str, 1, CONFIG_OPTION_LENGTH], audience: str
    ) -> None:
        lang = language_of(interaction.locale)
        config_id = number_or_none(config)
        if config_id is None:
            raise UserFacingError("Pick one of your configs from the list")
        token = self.bot.access.user_token(interaction)  # not linked: say so before anything shows as "thinking"
        await interaction.response.defer(ephemeral=True)
        user = interaction.user.id
        # Asking for the friends first stops a manager without friends before anything is sent: it would answer with 405
        # or ignore `share_with`. The list may be a minute old; the manager refuses a friend who is none any more.
        overview = await self.bot.access.call_as(
            interaction, token, lambda user_token: self.bot.friend_directory.overview(user, user_token)
        )
        own = parse_configs(await self.bot.access.call_as(interaction, token, self.bot.manager.list_configs))
        chosen_config = next((c for c in own if c.id == config_id), None)
        if chosen_config is None:
            raise UserFacingError("Pick one of your configs from the list")
        name = safe_text(chosen_config.name, NAME_LIMIT)
        if audience == CHOSEN:
            await self._choose_friends(interaction, lang, chosen_config, name, overview.friends)
            return
        if audience == EVERYONE:
            question = t(
                "Share **{config}** with every player? Everyone on the manager can then read it, player names included, and copy it.",
                lang,
                config=name,
            )
            view = ConfirmView(user, lang, confirm_label=t("Share with everyone", lang))
            if not await view.ask(interaction, notice_embed(question)):
                return
        await self.bot.access.call_as(
            interaction, token, lambda user_token: self._write(user_token, config_id, audience)
        )
        self._forget(user)
        text = {
            EVERYONE: t("**{config}** is shared with every player", lang, config=name),
            FRIENDS: t("**{config}** is shared with all your friends", lang, config=name),
            STOP: t("**{config}** is no longer shared", lang, config=name),
        }[audience]
        embed = notice_embed(text, SUCCESS)
        if audience == EVERYONE:
            await interaction.edit_original_response(embed=embed, view=None)
        else:
            await reply(interaction, embed=embed, ephemeral=True)

    async def _write(self, token: str, config_id: int, audience: str) -> dict:
        if audience == STOP:
            return await self.bot.manager.share_config(token, config_id, shared=False)
        return await self.bot.manager.share_config(token, config_id, shared=True, share_with=audience)

    async def _choose_friends(
        self, interaction: discord.Interaction, lang: str, config: ConfigView, name: str, friends: tuple[FriendView, ...]
    ) -> None:
        if not friends:
            raise UserFacingError("You have no friends to share it with yet. Add some with /friend add")
        shown = friends[:SELECT_OPTIONS]
        names = {friend.id: friend.username for friend in friends}
        # The menu shows 25 at most. Friends it cannot show who already see the config keep seeing it.
        unseen = [friend_id for friend_id in config.shared_with if friend_id not in {f.id for f in shown}]

        async def save(select_interaction: discord.Interaction, values: list[str]) -> discord.Embed:
            if not values:
                raise UserFacingError("Choose at least one friend, or pick stop to stop sharing")
            ids = [int(value) for value in values] + unseen
            await self.bot.access.run(
                select_interaction,
                lambda token: self.bot.manager.share_config(token, config.id, shared=True, share_with=CHOSEN, friends=ids),
            )
            self._forget(select_interaction.user.id)
            people = ", ".join(safe_text(names.get(friend_id, "?"), NAME_LIMIT) for friend_id in ids if friend_id in names)
            return notice_embed(t("**{config}** is shared with: {names}", lang, config=name, names=people), SUCCESS)

        view = MultiSelectView(
            interaction.user.id,
            lang,
            [(str(friend.id), clip(friend.username, CHOICE_NAME)) for friend in shown],
            {str(friend_id) for friend_id in config.shared_with},
            save,
            placeholder=t("Choose friends", lang),
        )
        text = t("Choose the friends who may see **{config}**.", lang, config=name)
        if len(friends) > len(shown):
            text += "\n" + t("The menu shows the first {count} friends. Use the manager for the others.", lang, count=len(shown))
        await reply(interaction, embed=notice_embed(text), view=view, ephemeral=True)

    def _forget(self, user: int) -> None:
        """What a person shares changed, so the suggestions of configs must be asked again."""
        self.bot.signup_directory.forget_configs(user)
        self.bot.friend_directory.forget_library(user)
