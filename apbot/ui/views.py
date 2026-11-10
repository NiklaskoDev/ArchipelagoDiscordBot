"""Discord components shared by the commands."""

import contextlib

import discord

from apbot.i18n import t
from apbot.ui.embeds import notice_embed
from apbot.ui.respond import reply

CONFIRM_TIMEOUT = 60  # seconds


class OpenInManagerButton(discord.ui.Button):
    """A link button to a page of the manager's web UI."""

    def __init__(self, url: str, lang: str):
        super().__init__(style=discord.ButtonStyle.link, label=t("Open in manager", lang), url=url)


def manager_page_url(site_url: str | None, path: str) -> str | None:
    """The address of a page of the web UI (`path` like "/games/3"), or None when the manager's address is not known
    or is not one Discord accepts for a link button."""
    if not site_url or not site_url.startswith(("http://", "https://")):
        return None
    return f"{site_url.rstrip('/')}/#{path}"


def open_in_manager_view(site_url: str | None, path: str, lang: str) -> discord.ui.View | None:
    url = manager_page_url(site_url, path)
    if url is None:
        return None
    view = discord.ui.View()
    view.add_item(OpenInManagerButton(url, lang))
    return view


class ConfirmView(discord.ui.View):
    """Confirm and Cancel buttons that only the person who asked can press. `ask` shows them and tells the answer."""

    def __init__(self, author_id: int, lang: str, *, confirm_label: str, danger: bool = False):
        super().__init__(timeout=CONFIRM_TIMEOUT)
        self.confirmed: bool | None = None  # None until answered
        self._author_id = author_id
        self._lang = lang
        style = discord.ButtonStyle.danger if danger else discord.ButtonStyle.primary
        confirm = discord.ui.Button(label=confirm_label, style=style)
        confirm.callback = self._on_confirm
        cancel = discord.ui.Button(label=t("Cancel", lang), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(confirm)
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self._author_id:
            return True
        await reply(interaction, embed=notice_embed(t("This question is not for you.", self._lang)), ephemeral=True)
        return False

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        self.confirmed = True
        await interaction.response.defer()  # the command goes on and edits its own message with the result
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.confirmed = False
        await interaction.response.edit_message(embed=notice_embed(t("Cancelled", self._lang)), view=None)
        self.stop()

    async def ask(self, interaction: discord.Interaction, embed: discord.Embed) -> bool:
        """Sends `embed` with the buttons as the answer to `interaction` (which must not have been answered yet) and
        waits. True when confirmed; False when cancelled or when nobody answered within a minute."""
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
        await self.wait()
        if self.confirmed is None:
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    embed=notice_embed(t("This question expired. Run the command again.", self._lang)), view=None
                )
        return bool(self.confirmed)
