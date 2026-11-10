"""Embeds about sign-up rounds: one round in detail, what the manager says about a config, what happened when a config
was entered, and the deadline reminder.

Pure like the other builders. Every name and text that came from the manager or a player goes through `safe_text`.
"""

from collections.abc import Sequence

import discord

from apbot.i18n import t, translatable
from apbot.manager.views import CheckResult, EntryView, SignupDetail, SignupSummary
from apbot.ui.embeds import TITLE_LIMIT, field_lines
from apbot.ui.format import safe_text, ts
from apbot.ui.style import ACCENT, ERROR, NAME_LIMIT, SEPARATOR, SUCCESS, WARNING

GAMES_LIMIT = 100  # characters of the games of one entry or one player in a line
PROBLEM_LIMIT = 200  # characters of one problem the manager or the generator reported
DESCRIPTION_LIMIT = 400
MAX_PROBLEMS = 3  # per entry

ROUND_STATUS_LABELS = {
    "open": translatable("Open"),
    "closed": translatable("Closed"),
    "generating": translatable("Generating"),
    "done": translatable("Done"),
}
TEST_STATUS_LABELS = {
    "queued": translatable("Waiting in the queue"),
    "running": translatable("Running"),
    "succeeded": translatable("Succeeded"),
    "failed": translatable("Failed"),
    "cancelled": translatable("Cancelled"),
}
TEST_FINISHED = frozenset({"succeeded", "failed", "cancelled"})


def _label(table: dict[str, str], key: str, lang: str) -> str:
    return t(table[key], lang) if key in table else safe_text(key, NAME_LIMIT)


def _games_text(games: Sequence[str]) -> str:
    return safe_text(", ".join(games), GAMES_LIMIT)


def _slots_text(count: int, lang: str) -> str:
    return t("{count} slot", lang, count=count) if count == 1 else t("{count} slots", lang, count=count)


def _own_entry_block(entry: EntryView, lang: str) -> str:
    head = [f"**{safe_text(entry.name or '?', NAME_LIMIT)}**", _slots_text(entry.slots, lang)]
    if entry.games:
        head.insert(1, _games_text(entry.games))
    lines = [SEPARATOR.join(head)]
    lines += [t("Problem: {what}", lang, what=safe_text(issue, PROBLEM_LIMIT)) for issue in entry.issues[:MAX_PROBLEMS]]
    if entry.generation_error:
        lines.append(t("The generator said: {what}", lang, what=safe_text(entry.generation_error, PROBLEM_LIMIT)))
    return "\n".join(lines)


def _test_lines(signup: SignupDetail, lang: str) -> list[str]:
    """The organizer's view of the latest test: its state, and what the generator said about other players' entries
    (the organizer's own are listed under their entries). Empty when the round has no test."""
    if signup.test is None:
        return []
    lines = [_label(TEST_STATUS_LABELS, signup.test.status, lang)]
    if signup.test.status in TEST_FINISHED and not signup.test.current:
        lines[0] += SEPARATOR + t("the entries changed since", lang)
    if signup.test.error:
        lines.append(safe_text(signup.test.error, PROBLEM_LIMIT))
    for entry in signup.entries:
        if entry.generation_error and not entry.mine:
            who = safe_text(entry.username, NAME_LIMIT) if entry.username else "?"
            name = safe_text(entry.name or "?", NAME_LIMIT)
            lines.append(f"**{name}** ({who}): {safe_text(entry.generation_error, PROBLEM_LIMIT)}")
    return lines


def signup_detail_embed(signup: SignupDetail, lang: str) -> discord.Embed:
    """One round: its state, deadline and rules, the asker's own entries, and for the round's organizer the latest test
    and the generator's complaints. Other players' entries are never listed, only counted."""
    description = _label(ROUND_STATUS_LABELS, signup.status, lang)
    if signup.mystery:
        description += SEPARATOR + t("Mystery round: everyone gets what the organizer's weights roll", lang)
    if signup.description:
        description += "\n" + safe_text(signup.description, DESCRIPTION_LIMIT)
    embed = discord.Embed(title=safe_text(signup.name, TITLE_LIMIT), description=description, colour=ACCENT)
    if signup.deadline:
        deadline = f"{ts(signup.deadline, 'F')}\n{ts(signup.deadline, 'R')}"
    else:
        deadline = t("No deadline", lang)
    embed.add_field(name=t("Deadline", lang), value=deadline)
    embed.add_field(name=t("Entries", lang), value=str(signup.entry_count))
    embed.add_field(name=t("Players", lang), value=str(signup.user_count))
    used = t("{used} of {limit}", lang, used=signup.my_slot_count, limit=signup.slots_per_user)
    embed.add_field(name=t("Your slots", lang), value=used)
    if signup.allowed_games:
        embed.add_field(name=t("Games", lang), value=safe_text(", ".join(signup.allowed_games), GAMES_LIMIT * 2))
    if signup.creator:
        embed.add_field(name=t("Opened by", lang), value=safe_text(signup.creator, NAME_LIMIT))
    own = [_own_entry_block(entry, lang) for entry in signup.entries if entry.mine]
    embed.add_field(
        name=t("Your entries", lang),
        value=field_lines(own, t("You have not entered anything yet", lang), lang),
        inline=False,
    )
    test = _test_lines(signup, lang)
    if test:
        embed.add_field(name=t("Test", lang), value=field_lines(test, "", lang), inline=False)
    if signup.error:
        embed.add_field(name=t("Last generation", lang), value=safe_text(signup.error, PROBLEM_LIMIT), inline=False)
    return embed


def _check_lines(result: CheckResult, lang: str) -> tuple[list[str], list[str], list[str]]:
    """(problems, warnings, players) as lines; what is about one player starts with the player's name."""
    problems = [safe_text(text, PROBLEM_LIMIT) for text in result.errors]
    warnings = [safe_text(text, PROBLEM_LIMIT) for text in result.warnings]
    players = []
    for player in result.players:
        name = safe_text(player.name, NAME_LIMIT)
        problems += [f"**{name}**: {safe_text(text, PROBLEM_LIMIT)}" for text in player.errors]
        warnings += [f"**{name}**: {safe_text(text, PROBLEM_LIMIT)}" for text in player.warnings]
        games = _games_text(player.games) if player.games else t("no game", lang)
        players.append(f"**{name}**{SEPARATOR}{games}")
    return problems, warnings, players


def check_result_embed(result: CheckResult, filename: str, lang: str) -> discord.Embed:
    """What the manager's check of a config found. Red when something would stop the generator."""
    title = t("Check of {file}", lang, file=safe_text(filename, NAME_LIMIT))
    if result.ok:
        embed = discord.Embed(title=title, description=t("The config passed the check", lang), colour=SUCCESS)
    else:
        description = t("The config has problems. Nothing was saved or entered.", lang)
        embed = discord.Embed(title=title, description=description, colour=ERROR)
    problems, warnings, players = _check_lines(result, lang)
    if problems:
        embed.add_field(name=t("Problems", lang), value=field_lines(problems, "", lang), inline=False)
    if warnings:
        embed.add_field(name=t("Warnings", lang), value=field_lines(warnings, "", lang), inline=False)
    if result.missing_roms:
        games = safe_text(", ".join(result.missing_roms), GAMES_LIMIT * 2)
        embed.add_field(
            name=t("Base ROM missing on the server", lang),
            value=t("These games need it to generate: {games}", lang, games=games),
            inline=False,
        )
    if players:
        embed.add_field(name=t("Players", lang), value=field_lines(players, "", lang), inline=False)
    return embed


def entry_added_embed(
    entry: EntryView | None, signup: SignupDetail, warnings: Sequence[str], lang: str
) -> discord.Embed:
    """A config was entered in a round: how many of the person's slots are used now, and the manager's warnings."""
    message = t(
        "Entered **{entry}** in **{round}**. You use {used} of {limit} slots.",
        lang,
        entry=safe_text(entry.name if entry and entry.name else "?", NAME_LIMIT),
        round=safe_text(signup.name, NAME_LIMIT),
        used=signup.my_slot_count,
        limit=signup.slots_per_user,
    )
    embed = discord.Embed(description=message, colour=WARNING if warnings else SUCCESS)
    if warnings:
        lines = [safe_text(text, PROBLEM_LIMIT) for text in warnings]
        embed.add_field(name=t("Warnings", lang), value=field_lines(lines, "", lang), inline=False)
    return embed


def signup_reminder_embed(signup: SignupSummary, lang: str) -> discord.Embed:
    """The reminder posted in the sign-ups channel shortly before a round's deadline."""
    deadline = signup.deadline or 0
    embed = discord.Embed(
        title=t("Sign-ups for {name} close soon", lang, name=safe_text(signup.name, TITLE_LIMIT)),
        description=t("Sign-ups close {when} ({date}).", lang, when=ts(deadline, "R"), date=ts(deadline, "F")),
        colour=WARNING,
    )
    embed.add_field(name=t("Entries", lang), value=str(signup.entry_count))
    embed.add_field(name=t("Players", lang), value=str(signup.user_count))
    embed.add_field(name=t("Slots per player", lang), value=str(signup.slots_per_user))
    return embed
