"""Embeds. Pure builders: manager values and a language in, a discord.Embed out, nothing sent and no clock read.

Everything a person or the manager wrote goes through `safe_text`; fixed texts go through `t`.
"""

from collections.abc import Sequence

import discord

from apbot.i18n import t, translatable
from apbot.manager.views import GameDetail, GameSummary, SignupSummary, SlotView
from apbot.ui.format import chunk_lines, escape, fit_lines, progress, safe_text, ts
from apbot.ui.style import (
    ACCENT,
    DESCRIPTION,
    EMBED_TOTAL,
    ERROR,
    FIELD_VALUE,
    FIELDS,
    LIST_LINES,
    NAME_LIMIT,
    SEPARATOR,
    STATE_COLOURS,
)

TITLE_LIMIT = 100  # escaping can double a name, and Discord allows 256 characters in a title
MORE_LINE_RESERVE = 100  # room for the "…and N more" line at the end of a list
SLOTS_EMBED_RESERVE = 120  # field names and the footer of the slots embed, which len() counts too

_STATE_LABELS = {
    "running": translatable("Running"),
    "starting": translatable("Starting"),
    "stopping": translatable("Stopping"),
    "stopped": translatable("Stopped"),
    "sleeping": translatable("Sleeping"),
    "crashed": translatable("Crashed"),
}
_OUTCOME_LABELS = {"finished": translatable("Finished"), "aborted": translatable("Aborted")}
_SLOT_STATUS_LABELS = {
    "online": translatable("Online"),
    "offline": translatable("Offline"),
    "goal": translatable("Goal reached"),
}
_PLAYER_STATUS_LABELS = {
    "playing": translatable("Playing"),
    "stuck": translatable("Stuck"),
    "away": translatable("Away"),
}


def _label(table: dict[str, str], key: str, lang: str) -> str:
    return t(table[key], lang) if key in table else safe_text(key, NAME_LIMIT)


def _more_line(hidden: int, lang: str) -> str:
    return t("…and {count} more", lang, count=hidden)


def _list_embed(title: str, lines: Sequence[str], total: int, lang: str) -> discord.Embed:
    """A list in the description: the lines that fit, then how many were left out (never silently)."""
    shown, _ = fit_lines(lines, DESCRIPTION - MORE_LINE_RESERVE)
    description = "\n".join(shown)
    if total > len(shown):
        description += "\n" + _more_line(total - len(shown), lang)
    return discord.Embed(title=title, description=description, colour=ACCENT)


def _game_line(game: GameSummary, lang: str) -> str:
    return SEPARATOR.join(
        (
            f"**{safe_text(game.name, NAME_LIMIT)}**",
            _label(_STATE_LABELS, game.state, lang),
            t("Worlds {count}", lang, count=game.world_count),
            t("Players {claimed}/{total}", lang, claimed=game.claimed_count, total=game.player_count),
            t("Goals {done}/{total}", lang, done=game.goal_count, total=game.player_count),
        )
    )


def games_list_embed(games: Sequence[GameSummary], lang: str) -> discord.Embed:
    """The games to list (see views.listed_games), at most LIST_LINES of them."""
    title = t("Games", lang)
    if not games:
        return discord.Embed(
            title=title,
            description=t("No games are running. Organizers can add one in the manager.", lang),
            colour=ACCENT,
        )
    return _list_embed(title, [_game_line(g, lang) for g in games[:LIST_LINES]], len(games), lang)


def _slot_line(slot: SlotView, lang: str) -> str:
    parts = [
        f"**{safe_text(slot.name, NAME_LIMIT)}**",
        safe_text(slot.game, NAME_LIMIT),
        safe_text(slot.owner, NAME_LIMIT) if slot.owner else t("Unclaimed", lang),
        f"{slot.checked}/{slot.locations}",
    ]
    if slot.status:
        parts.append(_label(_SLOT_STATUS_LABELS, slot.status, lang))
    if slot.player_status:
        parts.append(_label(_PLAYER_STATUS_LABELS, slot.player_status, lang))
    return SEPARATOR.join(parts)


def _slots_embed(lines: list[str], allowed: int, lang: str) -> discord.Embed:
    """The slot lines in as many fields as needed. `allowed` is what the message has left of Discord's 6000 characters,
    so lines that do not fit are left out and counted in the footer."""
    shown, _ = fit_lines(lines, allowed - SLOTS_EMBED_RESERVE)
    while True:
        embed = discord.Embed(colour=ACCENT)
        for index, chunk in enumerate(chunk_lines(shown, FIELD_VALUE)):
            name = t("Slots", lang) if index == 0 else t("Slots, continued", lang)
            embed.add_field(name=name, value=chunk, inline=False)
        if len(shown) < len(lines):
            embed.set_footer(text=_more_line(len(lines) - len(shown), lang))
        if (len(embed) <= allowed and len(embed.fields) <= FIELDS) or not shown:
            return embed
        shown = shown[:-1]


def _status_line(game: GameDetail, lang: str) -> str:
    state = _label(_STATE_LABELS, game.state, lang)
    if game.finished_at is None:
        return state
    outcome = _label(_OUTCOME_LABELS, game.outcome, lang) if game.outcome else state
    return f"{outcome}{SEPARATOR}{ts(game.finished_at, 'R')}"


def game_detail_embeds(game: GameDetail, lang: str) -> list[discord.Embed]:
    """The game's facts, and its slots in a second embed. Together they stay inside Discord's 6000 characters."""
    info = discord.Embed(
        title=safe_text(game.name, TITLE_LIMIT),
        description=_status_line(game, lang),
        colour=STATE_COLOURS.get(game.state, ACCENT),
    )
    if game.address:
        address = escape(game.address)
        if game.tls:
            address += SEPARATOR + t("encrypted (wss)", lang)
        info.add_field(name=t("Address", lang), value=address)
    if game.ap_version:
        info.add_field(name=t("Archipelago version", lang), value=safe_text(game.ap_version, NAME_LIMIT))
    if game.start_at is not None:
        info.add_field(name=t("Planned start", lang), value=f"{ts(game.start_at, 'F')}\n{ts(game.start_at, 'R')}")
    info.add_field(name=t("Progress", lang), value=progress(game.checked, game.total))
    info.add_field(name=t("Goals", lang), value=f"{game.goal_count}/{game.player_count}")
    info.add_field(
        name=t("Players", lang),
        value=t("{claimed}/{total} claimed", lang, claimed=game.claimed_count, total=game.player_count),
    )
    embeds = [info]
    if game.slots:
        embeds.append(_slots_embed([_slot_line(s, lang) for s in game.slots], EMBED_TOTAL - len(info), lang))
    return embeds


def _signup_line(signup: SignupSummary, lang: str) -> str:
    parts = [
        f"**{safe_text(signup.name, NAME_LIMIT)}**",
        t("Closes {when}", lang, when=ts(signup.deadline, "R")) if signup.deadline else t("No deadline", lang),
        t("Entries {count}", lang, count=signup.entry_count),
        t("Players {count}", lang, count=signup.user_count),
        t("Slots per player {count}", lang, count=signup.slots_per_user),
    ]
    if signup.creator:
        parts.append(t("by {name}", lang, name=safe_text(signup.creator, NAME_LIMIT)))
    return SEPARATOR.join(parts)


def signups_embed(signups: Sequence[SignupSummary], lang: str) -> discord.Embed:
    """The rounds to list (see views.open_signups), at most LIST_LINES of them."""
    title = t("Sign-ups", lang)
    if not signups:
        return discord.Embed(
            title=title,
            description=t("No sign-up rounds are open. Organizers can open one in the manager.", lang),
            colour=ACCENT,
        )
    return _list_embed(title, [_signup_line(s, lang) for s in signups[:LIST_LINES]], len(signups), lang)


def error_embed(message: str) -> discord.Embed:
    """`message` is already in the reader's language: what happened and what to do about it."""
    return discord.Embed(description=message, colour=ERROR)
