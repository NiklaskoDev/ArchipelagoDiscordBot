"""The embed and the Cancel button that follow a generation in the reply to /generate."""

import contextlib

import discord

from apbot.i18n import t
from apbot.manager.errors import Conflict
from apbot.manager.views import JobView
from apbot.ui.embeds import TITLE_LIMIT, field_lines
from apbot.ui.format import safe_text
from apbot.ui.style import ACCENT, ERROR, NAME_LIMIT, SEPARATOR, SUCCESS, WARNING
from apbot.ui.views import AuthorView

ERROR_LIMIT = 900  # characters of the generator's complaint
CONFIG_EXTENSIONS = (".yaml", ".yml")
GENERATION_VIEW_TIMEOUT = 900  # seconds; the reply cannot be edited for longer than Discord's 15 minutes anyway


def config_name(filename: str) -> str:
    """A player file's name without its extension."""
    for extension in CONFIG_EXTENSIONS:
        if filename.lower().endswith(extension):
            return filename[: -len(extension)] or filename
    return filename


def _status_line(job: JobView, lang: str) -> tuple[str, discord.Colour]:
    if job.status == "queued":
        if job.queue_position is not None:
            return t("Waiting to be generated (place {n} in the queue)", lang, n=job.queue_position), ACCENT
        return t("Waiting to be generated", lang), ACCENT
    if job.status == "running":
        return t("Generating…", lang), ACCENT
    if job.status == "succeeded":
        text = t("Generated. The game is ready.", lang)
        if job.host_after:
            text += " " + t("Its server starts now.", lang)
        return text, SUCCESS
    if job.status == "failed":
        text = t("Generation failed", lang)
        if job.error:
            text += "\n" + safe_text(job.error, ERROR_LIMIT)
        return text, ERROR
    if job.status == "cancelled":
        return t("Cancelled", lang), WARNING
    return safe_text(job.status, NAME_LIMIT), ACCENT


def job_embed(job: JobView, lang: str, *, me: int | None = None) -> discord.Embed:
    """A generation job: its state, and whose configs go into it. A config that belongs to another person (`me` is the
    asker's manager user id) names its owner."""
    description, colour = _status_line(job, lang)
    embed = discord.Embed(
        title=safe_text(job.name or t("New game", lang), TITLE_LIMIT), description=description, colour=colour
    )
    lines = []
    for player in job.players:
        line = safe_text(config_name(player.filename), NAME_LIMIT)
        if player.user_id not in (None, me) and player.owner:
            line += SEPARATOR + safe_text(player.owner, NAME_LIMIT)
        lines.append(line)
    if lines:
        embed.add_field(name=t("Players", lang), value=field_lines(lines, "", lang), inline=False)
    return embed


class GenerationView(AuthorView):
    """The Cancel button under a running generation. Only the person who started it can press it."""

    def __init__(self, author_id: int, lang: str, job_id: int):
        super().__init__(author_id, lang, timeout=GENERATION_VIEW_TIMEOUT)
        self._job_id = job_id
        self.cancel_requested = False
        self.cancel = discord.ui.Button(label=t("Cancel generation", lang), style=discord.ButtonStyle.danger)
        self.cancel.callback = self._on_cancel
        self.add_item(self.cancel)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await interaction.client.on_app_command_error(interaction, error)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        await interaction.response.defer()
        with contextlib.suppress(Conflict):  # it is over already; the next look at the job says how it ended
            await bot.access.run(interaction, lambda token: bot.manager.cancel_job(token, self._job_id))
        self.cancel_requested = True
        self.cancel.disabled = True
        await interaction.edit_original_response(view=self)
