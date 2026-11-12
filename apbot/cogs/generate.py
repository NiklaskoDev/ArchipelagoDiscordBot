"""/generate makes a game from the person's saved configs and follows the generation in its reply. It acts as the person
who asks, with the manager token they linked: the manager decides what they may generate and how much."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from apbot.autocomplete import generate_config_choices
from apbot.errors import UserFacingError
from apbot.i18n import language_of, t
from apbot.manager.errors import BadRequest, Conflict, Forbidden, ManagerUnavailable, NotFound
from apbot.manager.views import JOB_FINISHED, JobView, parse_job
from apbot.services.slots import number_or_none
from apbot.ui.embeds import error_embed, notice_embed
from apbot.ui.format import safe_text
from apbot.ui.generate import GenerationView, config_name, job_embed
from apbot.ui.respond import reply
from apbot.ui.style import WARNING
from apbot.ui.views import open_in_manager_view

if TYPE_CHECKING:
    from apbot.bot import ApBot

log = logging.getLogger(__name__)

ID_OPTION_LENGTH = 20  # digits of an id as text; Discord needs a limit on a text option
SEED_LENGTH = 18  # digits; a seed the manager keeps as a 64-bit number
NAME_LENGTH = 120  # the manager's limit for the name of a game
JOB_POLL_INTERVAL = 10  # seconds between looks at the job
JOB_POLLS = 84  # looks: 14 minutes, inside the 15 minutes Discord lets a reply be edited
REFUSAL_LIMIT = 300  # characters of the manager's reason shown when it refuses
REFUSALS = (BadRequest, Conflict, Forbidden, NotFound)  # the manager says no to the request

SPOILER_CHOICES = [
    app_commands.Choice(name=locale_str("none"), value=0),
    app_commands.Choice(name=locale_str("basic"), value=1),
    app_commands.Choice(name=locale_str("playthrough"), value=2),
    app_commands.Choice(name=locale_str("full"), value=3),
]
ConfigText = app_commands.Range[str, 1, ID_OPTION_LENGTH]


def parse_config_ids(options: list[str | None]) -> list[int]:
    """The ids of the configs picked in the `config` options, in order (options left empty are skipped)."""
    ids = []
    for option in options:
        if option is None:
            continue
        config_id = number_or_none(option)
        if config_id is None:
            raise UserFacingError("Pick a config from the list")
        ids.append(config_id)
    if len(set(ids)) != len(ids):
        raise UserFacingError("The same config was picked twice")
    return ids


def parse_seed(text: str | None) -> int | None:
    if text is None:
        return None
    digits = text.strip()
    if not digits.isascii() or not digits.isdecimal():
        raise UserFacingError("The seed must be a whole number")
    return int(digits)


class GenerateCog(commands.Cog):
    def __init__(self, bot: "ApBot", *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.bot = bot
        self._sleep = sleep

    @app_commands.command(name=locale_str("generate"), description=locale_str("Generate a game from your configs"))
    @app_commands.describe(
        config=locale_str("The first config to generate with"),
        config2=locale_str("Another config to generate with"),
        config3=locale_str("Another config to generate with"),
        config4=locale_str("Another config to generate with"),
        config5=locale_str("Another config to generate with"),
        config6=locale_str("Another config to generate with"),
        config7=locale_str("Another config to generate with"),
        config8=locale_str("Another config to generate with"),
        config9=locale_str("Another config to generate with"),
        config10=locale_str("Another config to generate with"),
        name=locale_str("The name of the game"),
        spoiler=locale_str("How much the spoiler log shows"),
        race=locale_str("Generate as a race game"),
        seed=locale_str("A seed number, to get the same game again"),
        start=locale_str("Start the game's server once it is generated"),
    )
    @app_commands.rename(
        config=locale_str("config"),
        config2=locale_str("config2"),
        config3=locale_str("config3"),
        config4=locale_str("config4"),
        config5=locale_str("config5"),
        config6=locale_str("config6"),
        config7=locale_str("config7"),
        config8=locale_str("config8"),
        config9=locale_str("config9"),
        config10=locale_str("config10"),
        name=locale_str("name"),
        spoiler=locale_str("spoiler"),
        race=locale_str("race"),
        seed=locale_str("seed"),
        start=locale_str("start"),
    )
    @app_commands.autocomplete(
        config=generate_config_choices,
        config2=generate_config_choices,
        config3=generate_config_choices,
        config4=generate_config_choices,
        config5=generate_config_choices,
        config6=generate_config_choices,
        config7=generate_config_choices,
        config8=generate_config_choices,
        config9=generate_config_choices,
        config10=generate_config_choices,
    )
    @app_commands.choices(spoiler=SPOILER_CHOICES)
    @app_commands.guild_only()
    async def generate(
        self,
        interaction: discord.Interaction,
        config: ConfigText,
        config2: ConfigText | None = None,
        config3: ConfigText | None = None,
        config4: ConfigText | None = None,
        config5: ConfigText | None = None,
        config6: ConfigText | None = None,
        config7: ConfigText | None = None,
        config8: ConfigText | None = None,
        config9: ConfigText | None = None,
        config10: ConfigText | None = None,
        name: app_commands.Range[str, 1, NAME_LENGTH] | None = None,
        spoiler: int = 1,
        race: bool = False,
        seed: app_commands.Range[str, 1, SEED_LENGTH] | None = None,
        start: bool = True,
    ) -> None:
        lang = language_of(interaction.locale)
        self.bot.access.require_role(interaction, "organizer")
        config_ids = parse_config_ids([config, config2, config3, config4, config5, config6, config7, config8, config9, config10])
        seed_number = parse_seed(seed)
        token = self.bot.access.user_token(interaction)  # not linked: say so before anything shows as "thinking"
        await interaction.response.defer(ephemeral=True)
        try:
            raw = await self.bot.access.call_as(
                interaction,
                token,
                lambda user_token: self.bot.manager.start_job(
                    user_token,
                    config_ids,
                    name=(name or "").strip() or None,
                    spoiler=spoiler,
                    race=race,
                    seed=seed_number,
                    host_after=start,
                ),
            )
        except REFUSALS as exc:
            text = t("The game could not be generated: {detail}", lang, detail=safe_text(exc.detail, REFUSAL_LIMIT))
            await reply(interaction, embed=error_embed(text), ephemeral=True)
            return
        job = parse_job(raw)
        self.bot.my_games.forget(interaction.user.id)
        view = GenerationView(interaction.user.id, lang, job.id)
        link = self.bot.links.get(interaction.user.id)
        me = link.manager_user_id
        await reply(interaction, embed=job_embed(job, lang, me=me), view=view, ephemeral=True)
        await self._tell_friends(job, me, link.manager_username)
        try:
            await self._follow(interaction, token, job, view, lang, me)
        finally:
            view.stop()

    async def _tell_friends(self, job: JobView, me: int, my_name: str) -> None:
        """Tells each friend whose config is in the game (and who is linked and asked to hear it), once, now that the
        manager has queued it, so a generation that fails later has told them too: it is a heads-up, not a promise."""
        configs: dict[int, list[str]] = {}
        for player in job.players:
            if player.user_id not in (None, me):
                configs.setdefault(player.user_id, []).append(config_name(player.filename))
        for friend_id, names in configs.items():
            friend = self.bot.links.by_manager_user(friend_id)
            if friend is not None:
                await self.bot.alerts.friend_game(friend, by=my_name, game=job.name, configs=names)

    async def _follow(
        self, interaction: discord.Interaction, token: str, job: JobView, view: GenerationView, lang: str, me: int
    ) -> None:
        """Looks at the job every few seconds, showing how far it is, until it is over (or the reply can no longer be
        edited)."""
        job_id = job.id
        for _ in range(JOB_POLLS):
            await self._sleep(JOB_POLL_INTERVAL)
            try:
                raw = await self.bot.access.call_as(
                    interaction, token, lambda user_token: self.bot.manager.job(user_token, job_id)
                )
            except ManagerUnavailable:
                continue  # the manager may be busy; the next look will do
            except (NotFound, Forbidden):
                await self._show_lost(interaction, lang)
                return
            job = parse_job(raw)
            if job.status not in JOB_FINISHED:
                if not await self._show_progress(interaction, job, view, lang, me):
                    return
                continue
            await self._show(interaction, embed=job_embed(job, lang, me=me), view=self._game_link(job, lang))
            if job.status == "succeeded":
                self.bot.my_games.forget(interaction.user.id)
                await self.bot.cache.run_refresh()  # the game shows in /games at once instead of within the minute
            return
        text = t("Still generating. Follow it in the manager, or look at /games later.", lang)
        await self._show(
            interaction,
            embed=notice_embed(text, WARNING),
            view=open_in_manager_view(self.bot.cache.site_url, f"/jobs/{job_id}", lang),
        )

    async def _show_progress(
        self, interaction: discord.Interaction, job: JobView, view: GenerationView, lang: str, me: int
    ) -> bool:
        """Edits the reply with how far the job is. False when the reply cannot be edited any more.

        Cancel is handled while this edit is on its way, and Discord may apply the two edits in either order: when it
        was pressed meanwhile, the edit is made again so the greyed-out button is what stays."""
        while True:
            cancelled = view.cancel_requested
            view.cancel.disabled = cancelled
            if not await self._show(interaction, embed=job_embed(job, lang, me=me), view=view):
                return False
            if view.cancel_requested == cancelled:
                return True

    async def _show_lost(self, interaction: discord.Interaction, lang: str) -> None:
        """The manager does not show the job any more (it was removed, or the account lost access to it)."""
        text = t("The manager no longer shows this generation. Look at /games, or open the manager.", lang)
        await self._show(
            interaction,
            embed=notice_embed(text, WARNING),
            view=open_in_manager_view(self.bot.cache.site_url, "/games", lang),
        )

    def _game_link(self, job: JobView, lang: str) -> discord.ui.View | None:
        if job.status != "succeeded" or job.game_id is None:
            return None
        return open_in_manager_view(self.bot.cache.site_url, f"/games/{job.game_id}", lang)

    @staticmethod
    async def _show(interaction: discord.Interaction, **kwargs) -> bool:
        """Edits the reply. False when that is not possible any more (Discord stops accepting edits after 15 minutes, or
        the person deleted the message)."""
        try:
            await interaction.edit_original_response(**kwargs)
        except discord.HTTPException as exc:
            log.debug("The generation reply could not be edited any more: %s", exc)
            return False
        return True
