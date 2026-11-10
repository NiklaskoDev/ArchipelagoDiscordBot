"""Discord components shared by the commands."""

import contextlib
from collections.abc import Awaitable, Callable, Collection, Sequence

import discord

from apbot.i18n import t
from apbot.manager.views import HintView
from apbot.ui.dynamic import hint_status_view
from apbot.ui.embeds import notice_embed
from apbot.ui.format import clip
from apbot.ui.respond import reply
from apbot.ui.style import CHOICE_NAME, SELECT_OPTIONS, SEPARATOR
from apbot.ui.tracker import hint_status_label, hints_for_me, line_for_me

CONFIRM_TIMEOUT = 60  # seconds
SELECT_TIMEOUT = 300
HINT_PICKER_TIMEOUT = 600


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


class AuthorView(discord.ui.View):
    """A view that only the person who asked can use; anyone else is told it is not theirs."""

    def __init__(self, author_id: int, lang: str, *, timeout: float):
        super().__init__(timeout=timeout)
        self._author_id = author_id
        self._lang = lang

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self._author_id:
            return True
        await reply(interaction, embed=notice_embed(t("This question is not for you.", self._lang)), ephemeral=True)
        return False


class ConfirmView(AuthorView):
    """Confirm and Cancel buttons that only the person who asked can press. `ask` shows them and tells the answer."""

    def __init__(self, author_id: int, lang: str, *, confirm_label: str, danger: bool = False):
        super().__init__(author_id, lang, timeout=CONFIRM_TIMEOUT)
        self.confirmed: bool | None = None  # None until answered
        style = discord.ButtonStyle.danger if danger else discord.ButtonStyle.primary
        confirm = discord.ui.Button(label=confirm_label, style=style)
        confirm.callback = self._on_confirm
        cancel = discord.ui.Button(label=t("Cancel", lang), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(confirm)
        self.add_item(cancel)

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        self.confirmed = True
        await interaction.response.defer()  # the command goes on and edits its own message with the result
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.confirmed = False
        await interaction.response.edit_message(embed=notice_embed(t("Cancelled", self._lang)), view=None)
        self.stop()

    async def ask(self, interaction: discord.Interaction, embed: discord.Embed) -> bool:
        """Sends `embed` with the buttons as the answer to `interaction` (or as the follow-up to it when it was deferred
        while the manager was asked) and waits. True when confirmed; False when cancelled or when nobody answered
        within a minute."""
        await reply(interaction, embed=embed, view=self, ephemeral=True)
        await self.wait()
        if self.confirmed is None:
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    embed=notice_embed(t("This question expired. Run the command again.", self._lang)), view=None
                )
        return bool(self.confirmed)


class MultiSelectView(AuthorView):
    """A drop-down in which the person ticks any number of options (none included). Choosing saves at once: `save`
    gets the interaction and the chosen values and returns the embed that replaces the message."""

    def __init__(
        self,
        author_id: int,
        lang: str,
        options: Sequence[tuple[str, str]],
        selected: Collection[str],
        save: Callable[[discord.Interaction, list[str]], Awaitable[discord.Embed]],
        *,
        placeholder: str,
    ):
        super().__init__(author_id, lang, timeout=SELECT_TIMEOUT)
        self._save = save
        self.select = discord.ui.Select(
            placeholder=placeholder,
            min_values=0,
            max_values=len(options),
            options=[discord.SelectOption(label=label, value=value, default=value in selected) for value, label in options],
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await interaction.client.on_app_command_error(interaction, error)  # one place decides what the person is told

    async def _on_select(self, interaction: discord.Interaction) -> None:
        embed = await self._save(interaction, list(self.select.values))
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


class HintPickerView(AuthorView):
    """A drop-down of the hints for items the person waits for. Choosing one replaces the message with buttons that mark
    exactly that hint (they carry its numbers, so they work after a restart too)."""

    def __init__(self, author_id: int, lang: str, game_id: int, slot: int, hints: Sequence[HintView]):
        super().__init__(author_id, lang, timeout=HINT_PICKER_TIMEOUT)
        self._game_id, self._slot = game_id, slot
        self._hints = {self._key(hint): hint for hint in hints}
        self.select = discord.ui.Select(
            placeholder=t("Choose a hint to mark", lang),
            options=[
                discord.SelectOption(
                    label=clip(f"{hint.item}{SEPARATOR}{hint.location}", CHOICE_NAME),
                    value=self._key(hint),
                    description=clip(hint_status_label(hint.status, lang), CHOICE_NAME),
                )
                for hint in hints
            ],
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    @staticmethod
    def _key(hint: HintView) -> str:
        return f"{hint.finder_slot}:{hint.location_id}"

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await interaction.client.on_app_command_error(interaction, error)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        hint = self._hints[self.select.values[0]]
        question = t("How important is this item to you?", self._lang)
        embed = notice_embed(f"{line_for_me(hint, self._lang)}\n{question}")
        view = hint_status_view(self._game_id, self._slot, hint.finder_slot, hint.location_id, self._lang)
        await interaction.response.edit_message(embed=embed, view=view)
        self.stop()


def hint_picker(author_id: int, lang: str, game_id: int, slot: int, hints: Sequence[HintView]) -> HintPickerView | None:
    """The picker for the hints that can be marked (the manager sent their numbers, they are for the person and not
    found yet); None when there are none. Discord shows 25 options at most."""
    markable = [hint for hint in hints_for_me(hints) if hint.finder_slot is not None and hint.location_id is not None]
    return HintPickerView(author_id, lang, game_id, slot, markable[:SELECT_OPTIONS]) if markable else None
