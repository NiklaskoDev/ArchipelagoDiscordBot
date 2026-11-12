"""The friend list (/friends): the embed, the drop-down to pick a person and the buttons that answer a request, take it
back or end a friendship. Every change is made as the person who pressed, with their own token."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

import discord

from apbot.i18n import t, translatable
from apbot.manager.client import ManagerClient
from apbot.manager.views import FriendsOverview, FriendView
from apbot.ui.embeds import field_lines, notice_embed
from apbot.ui.format import clip, safe_text, ts
from apbot.ui.style import ACCENT, CHOICE_NAME, NAME_LIMIT, SELECT_OPTIONS, SEPARATOR, SUCCESS, WARNING
from apbot.ui.views import SELECT_TIMEOUT, AuthorView

INCOMING: Final = "incoming"
OUTGOING: Final = "outgoing"
FRIEND: Final = "friend"
KIND_LABELS = {
    INCOMING: translatable("Asks to be your friend"),
    OUTGOING: translatable("Your request"),
    FRIEND: translatable("Friend"),
}


@dataclass(frozen=True)
class FriendAction:
    """One button under a chosen person: what it says, which manager call it makes and what it tells afterwards."""

    label: str
    style: discord.ButtonStyle
    call: Callable[[ManagerClient, str, int], Awaitable[dict]]
    result: str  # English text with {user}, shown in place of the buttons


ACTIONS: dict[str, tuple[FriendAction, ...]] = {
    INCOMING: (
        FriendAction(
            translatable("Accept"), discord.ButtonStyle.primary, ManagerClient.accept_friend,
            translatable("You and **{user}** are friends now"),
        ),
        FriendAction(
            translatable("Decline"), discord.ButtonStyle.secondary, ManagerClient.remove_friend,
            translatable("You declined **{user}**'s request"),
        ),
    ),
    OUTGOING: (
        FriendAction(
            translatable("Take back request"), discord.ButtonStyle.secondary, ManagerClient.remove_friend,
            translatable("You took back your request to **{user}**"),
        ),
    ),
    FRIEND: (
        FriendAction(
            translatable("Remove friend"), discord.ButtonStyle.danger, ManagerClient.remove_friend,
            translatable(
                "**{user}** is no longer your friend. Configs you shared only with each other by name are no longer shared."
            ),
        ),
    ),
}


# which list of the overview a chosen person has to be in for the buttons to still apply
STILL_THERE = {INCOMING: "incoming", OUTGOING: "outgoing", FRIEND: "friends"}
GONE_TEXTS = {
    INCOMING: translatable("This friend request is no longer open"),
    OUTGOING: translatable("This friend request is no longer open"),
    FRIEND: translatable("**{user}** is not your friend any more"),
}


def _configs_text(count: int, lang: str) -> str:
    key = translatable("shares {n} config with you") if count == 1 else translatable("shares {n} configs with you")
    return t(key, lang, n=count)


def _line(friend: FriendView, lang: str, *, friends: bool = False) -> str:
    parts = [f"**{safe_text(friend.username, NAME_LIMIT)}**"]
    if friend.at is not None:
        parts.append(ts(friend.at, "R"))
    if friends and friend.shared_configs > 0:
        parts.append(_configs_text(friend.shared_configs, lang))
    return SEPARATOR.join(parts)


def friends_embed(overview: FriendsOverview, lang: str) -> discord.Embed:
    """The person's friends and requests. Names only: the manager's usernames, never anyone's Discord identity."""
    title = t("Your friends", lang)
    if overview.is_empty:
        text = t("You have no friends on the manager yet. Add someone with /friend add.", lang)
        return discord.Embed(title=title, description=text, colour=ACCENT)
    embed = discord.Embed(title=title, colour=ACCENT)
    sections = (
        (t("Requests for you", lang), [_line(f, lang) for f in overview.incoming]),
        (t("Your requests", lang), [_line(f, lang) for f in overview.outgoing]),
        (t("Friends", lang), [_line(f, lang, friends=True) for f in overview.friends]),
    )
    for name, lines in sections:
        if lines:
            embed.add_field(name=name, value=field_lines(lines, "", lang), inline=False)
    people = len(overview.incoming) + len(overview.outgoing) + len(overview.friends)
    if people > SELECT_OPTIONS:  # the menu below cannot take them all: say so, and where the rest is
        embed.set_footer(text=t("The menu offers the first {count} people. Find the others in the manager.", lang, count=SELECT_OPTIONS))
    return embed


class FriendsView(AuthorView):
    """A drop-down with the people on the list (requests for the person first). Choosing one replaces the message with
    buttons for that person."""

    def __init__(self, author_id: int, lang: str, overview: FriendsOverview):
        super().__init__(author_id, lang, timeout=SELECT_TIMEOUT)
        people = [
            *((INCOMING, f) for f in overview.incoming),
            *((OUTGOING, f) for f in overview.outgoing),
            *((FRIEND, f) for f in overview.friends),
        ][:SELECT_OPTIONS]
        self._people = {self._key(kind, friend): (kind, friend) for kind, friend in people}
        self.select = discord.ui.Select(
            placeholder=t("Choose a person", lang),
            options=[
                discord.SelectOption(
                    label=clip(friend.username, CHOICE_NAME),
                    value=self._key(kind, friend),
                    description=t(KIND_LABELS[kind], lang),
                )
                for kind, friend in people
            ],
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    @staticmethod
    def _key(kind: str, friend: FriendView) -> str:
        return f"{kind}:{friend.id}"

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await interaction.client.on_app_command_error(interaction, error)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        kind, friend = self._people[self.select.values[0]]
        text = f"**{safe_text(friend.username, NAME_LIMIT)}**{SEPARATOR}{t(KIND_LABELS[kind], self._lang)}"
        view = FriendActionView(self._author_id, self._lang, kind, friend)
        await interaction.response.edit_message(embed=notice_embed(text), view=view)
        self.stop()


class FriendActionView(AuthorView):
    """The buttons for one chosen person. A second press is never needed: the button is the confirmation."""

    def __init__(self, author_id: int, lang: str, kind: str, friend: FriendView):
        super().__init__(author_id, lang, timeout=SELECT_TIMEOUT)
        self._friend, self._kind = friend, kind
        for action in ACTIONS[kind]:
            button = discord.ui.Button(label=t(action.label, lang), style=action.style)
            button.callback = self._callback_for(action)
            self.add_item(button)

    def _callback_for(self, action: FriendAction) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            bot = interaction.client
            token = bot.access.user_token(interaction)  # a removed link is answered privately, before anything is deferred
            await interaction.response.defer()
            user_id = interaction.user.id
            user = safe_text(self._friend.username, NAME_LIMIT)
            # The buttons can be minutes old, and the manager's remove deletes whatever row joins the two people: a
            # request answered or a friendship ended meanwhile must not be acted on again.
            bot.friend_directory.forget(user_id)
            overview = await bot.access.call_as(
                interaction, token, lambda user_token: bot.friend_directory.overview(user_id, user_token)
            )
            if self._friend.id not in {p.id for p in getattr(overview, STILL_THERE[self._kind])}:
                gone = notice_embed(t(GONE_TEXTS[self._kind], self._lang, user=user), WARNING)
                await interaction.edit_original_response(embed=gone, view=None)
                self.stop()
                return
            await bot.access.call_as(
                interaction, token, lambda user_token: action.call(bot.manager, user_token, self._friend.id)
            )
            bot.friend_directory.forget(user_id)
            embed = notice_embed(t(action.result, self._lang, user=user), SUCCESS)
            await interaction.edit_original_response(embed=embed, view=None)
            self.stop()

        return callback

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await interaction.client.on_app_command_error(interaction, error)
