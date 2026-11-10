"""Embeds. Pure builders: manager values and a language in, a discord.Embed out, nothing sent and no clock read.

Everything a person or the manager wrote goes through `safe_text`; fixed texts go through `t`.
"""

from collections.abc import Collection, Sequence

import discord

from apbot.db.guilds import GuildSettings
from apbot.db.links import STATUS_ACTIVE, UserLink
from apbot.i18n import LANGUAGE_NAMES, t, translatable
from apbot.manager.views import GameDetail, GameSummary, SignupSummary, SlotView
from apbot.services.manager_watch import MIN_MANAGER_VERSION, ManagerReport, version_at_least
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
    ELLIPSIS,
    SEPARATOR,
    STATE_COLOURS,
    SUCCESS,
    WARNING,
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
PLAYER_STATUS_LABELS = {
    "playing": translatable("Playing"),
    "stuck": translatable("Stuck"),
    "away": translatable("Away"),
}
ALERT_KIND_LABELS = {
    "item_received": translatable("An item arrives for one of my slots"),
    "hint_found": translatable("An item I was hinted is found"),
    "stuck_in_my_world": translatable("A player is stuck and waits for items in my world"),
    "game_online": translatable("One of my games comes online"),
    "slot_offered": translatable("A slot is offered to me"),
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


def private_games_embed(games: Sequence[GameSummary], lang: str) -> discord.Embed:
    """The games only the person's own account shows, at most LIST_LINES of them. Sent to that person only."""
    return _list_embed(
        t("Private games you can see", lang), [_game_line(g, lang) for g in games[:LIST_LINES]], len(games), lang
    )


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
        parts.append(_label(PLAYER_STATUS_LABELS, slot.player_status, lang))
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
        address = safe_text(game.address, NAME_LIMIT)
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


def field_lines(lines: Sequence[str], empty: str, lang: str) -> str:
    """Lines for one field: those that fit, and how many were left out; `empty` when there are none."""
    if not lines:
        return empty
    shown, _ = fit_lines(lines, FIELD_VALUE - MORE_LINE_RESERVE)
    text = "\n".join(shown)
    return f"{text}\n{_more_line(len(lines) - len(shown), lang)}" if len(shown) < len(lines) else text


def overview_embed(games: Sequence[GameSummary], signups: Sequence[SignupSummary], lang: str) -> discord.Embed:
    """The pinned overview of a server: the games (see views.listed_games) and the open sign-up rounds. Nothing in it
    changes by itself with time, so the same content gives the same embed."""
    embed = discord.Embed(title=t("Overview", lang), colour=ACCENT)
    game_lines = [_game_line(g, lang) for g in games]
    signup_lines = [_signup_line(s, lang) for s in signups]
    embed.add_field(
        name=t("Games", lang),
        value=field_lines(game_lines, t("No games are running. Organizers can add one in the manager.", lang), lang),
        inline=False,
    )
    embed.add_field(
        name=t("Sign-ups", lang),
        value=field_lines(
            signup_lines, t("No sign-up rounds are open. Organizers can open one in the manager.", lang), lang
        ),
        inline=False,
    )
    return embed


def error_embed(message: str) -> discord.Embed:
    """`message` is already in the reader's language: what happened and what to do about it."""
    return discord.Embed(description=message, colour=ERROR)


def notice_embed(message: str, colour: discord.Colour = ACCENT) -> discord.Embed:
    """`message` is already in the reader's language."""
    return discord.Embed(description=message, colour=colour)


def game_state_embed(game: GameSummary, lang: str) -> discord.Embed:
    """What a game is doing right now, as answered after waking, starting, stopping or restarting it."""
    return discord.Embed(
        title=safe_text(game.name, TITLE_LIMIT),
        description=_label(_STATE_LABELS, game.state, lang),
        colour=STATE_COLOURS.get(game.state, ACCENT),
    )


def server_answer_embed(lines: Sequence[str], hidden: int, lang: str) -> discord.Embed:
    """What the game's server answered a command: its lines (already escaped), and how many were left out."""
    if not lines:
        return discord.Embed(description=t("The server did not answer. Try again in a moment.", lang), colour=WARNING)
    text = "\n".join(lines)
    if hidden:
        text += "\n" + _more_line(hidden, lang)
    return discord.Embed(description=text, colour=ACCENT)


def linked_embed(manager_username: str, lang: str) -> discord.Embed:
    return notice_embed(t("Linked as **{username}**", lang, username=safe_text(manager_username, NAME_LIMIT)), SUCCESS)


def alerts_text(kinds: Collection[str], lang: str) -> str:
    """The alerts a person chose, one per line, in the order of ALERT_KIND_LABELS."""
    chosen = [t(label, lang) for kind, label in ALERT_KIND_LABELS.items() if kind in kinds]
    return "\n".join(chosen) if chosen else t("None", lang)


def link_status_embed(link: UserLink | None, lang: str, alerts: Collection[str] = ()) -> discord.Embed:
    """Everything the bot stores about one person (/mydata). The token itself is never part of it, only its last
    characters."""
    title = t("Your stored data", lang)
    if link is None:
        description = t("No manager account is linked to your Discord account. Nothing else is stored about you.", lang)
        return discord.Embed(title=title, description=description, colour=ACCENT)
    embed = discord.Embed(title=title, colour=ACCENT)
    embed.add_field(name=t("Manager account", lang), value=safe_text(link.manager_username, NAME_LIMIT))
    if link.token_hint:
        embed.add_field(name=t("Token", lang), value=f"{ELLIPSIS}{escape(link.token_hint)}")
    status = t("Active", lang) if link.status == STATUS_ACTIVE else t("Not working. Run /link again.", lang)
    embed.add_field(name=t("Status", lang), value=status)
    embed.add_field(name=t("Language", lang), value=LANGUAGE_NAMES.get(link.locale, link.locale))
    embed.add_field(name=t("Linked", lang), value=ts(link.linked_at, "f"))
    embed.add_field(name=t("Last checked", lang), value=ts(link.checked_at, "f"))
    embed.add_field(name=t("Alerts", lang), value=alerts_text(alerts, lang), inline=False)
    return embed


def _channel_or_unset(channel_id: int | None, lang: str) -> str:
    return f"<#{channel_id}>" if channel_id else t("Not set", lang)


def _role_or_unset(role_id: int | None, lang: str) -> str:
    return f"<@&{role_id}>" if role_id else t("Not set", lang)


def settings_embed(settings: GuildSettings, lang: str) -> discord.Embed:
    embed = discord.Embed(title=t("Server settings", lang), colour=ACCENT)
    embed.add_field(name=t("Language", lang), value=LANGUAGE_NAMES.get(settings.language, settings.language))
    embed.add_field(name=t("Time zone", lang), value=escape(settings.timezone))
    embed.add_field(name=t("Games channel", lang), value=_channel_or_unset(settings.games_channel_id, lang))
    embed.add_field(name=t("Sign-ups channel", lang), value=_channel_or_unset(settings.signups_channel_id, lang))
    embed.add_field(name=t("Overview channel", lang), value=_channel_or_unset(settings.overview_channel_id, lang))
    embed.add_field(name=t("Organizer role", lang), value=_role_or_unset(settings.organizer_role_id, lang))
    embed.add_field(name=t("Admin role", lang), value=_role_or_unset(settings.admin_role_id, lang))
    embed.add_field(name=t("Scheduled events", lang), value=t("On", lang) if settings.scheduled_events else t("Off", lang))
    return embed


def manager_check_embed(report: ManagerReport | None, lang: str) -> discord.Embed:
    """The result of asking the manager about itself and about the bot's account; None when it did not answer."""
    embed = discord.Embed(title=t("Manager", lang))
    if report is None:
        embed.colour = ERROR
        embed.add_field(name=t("Reachable", lang), value=t("No. The manager did not answer.", lang), inline=False)
        return embed
    problem = False
    embed.add_field(name=t("Reachable", lang), value=t("Yes", lang), inline=False)
    if version_at_least(report.version, MIN_MANAGER_VERSION):
        version = safe_text(report.version, NAME_LIMIT)
    else:
        problem = True
        shown = safe_text(report.version, NAME_LIMIT) if report.version else t("Unknown", lang)
        version = t("{version}. The bot needs {minimum} or newer.", lang, version=shown, minimum=MIN_MANAGER_VERSION)
    embed.add_field(name=t("Version", lang), value=version, inline=False)
    embed.add_field(name=t("Ready", lang), value=t("Yes", lang) if report.ready else t("No", lang), inline=False)
    if report.username is None:
        embed.colour = ERROR
        account = t("Not accepted. Check MANAGER_API_TOKEN.", lang)
    else:
        account = f"{safe_text(report.username, NAME_LIMIT)} ({safe_text(report.role or '?', NAME_LIMIT)})"
        if report.role != "user":
            problem = True
            warning = t("The bot account should be a plain user so private games never show up in public lists.", lang)
            account = "\n".join((account, warning))
    embed.add_field(name=t("Bot account", lang), value=account, inline=False)
    web_address = escape(report.site_url) if report.site_url else t("Not set in the manager", lang)
    embed.add_field(name=t("Web address", lang), value=web_address, inline=False)
    if embed.colour is None:
        embed.colour = WARNING if problem else SUCCESS
    return embed
