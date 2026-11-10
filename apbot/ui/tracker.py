"""The tracker embed: what one slot has received and checked, its hint points and rules, and its open hints.

Pure like the other builders: a TrackerView and a language in, an embed out. Every name from the manager goes through
`safe_text`.
"""

from collections.abc import Callable, Collection, Sequence

import discord

from apbot.i18n import t, translatable
from apbot.manager.views import HintView, TrackerView
from apbot.ui.embeds import MORE_LINE_RESERVE, TITLE_LIMIT, field_lines
from apbot.ui.format import fit_lines, progress, safe_text
from apbot.ui.style import ACCENT, FIELD_VALUE, NAME_LIMIT, SEPARATOR, SUCCESS

MAX_HINTS = 10  # hints listed per section; the rest is counted
SECTIONS = ("summary", "hints")

RULE_LABELS = {
    "available": translatable("Available"),
    "after_goal": translatable("After your goal"),
    "automatic": translatable("Automatic at your goal"),
    "disabled": translatable("Disabled"),
    "done": translatable("Nothing left"),
}
HINT_STATUS_LABELS = {
    0: translatable("no priority"),
    10: translatable("no priority"),
    20: translatable("avoid"),
    30: translatable("priority"),
    40: translatable("found"),
}


def hint_status_label(status: int, lang: str) -> str:
    return t(HINT_STATUS_LABELS[status], lang) if status in HINT_STATUS_LABELS else str(status)


def hints_for_me(hints: Sequence[HintView]) -> list[HintView]:
    """Hints for items this slot is waiting for, not found yet."""
    return [hint for hint in hints if hint.for_me and not hint.found]


def hints_in_my_world(hints: Sequence[HintView]) -> list[HintView]:
    """Hints for items in this slot's world that others wait for, not found yet. A hint for this slot's own item is
    listed once, under hints for me."""
    return [hint for hint in hints if hint.in_my_world and not hint.for_me and not hint.found]


def line_for_me(hint: HintView, lang: str) -> str:
    """A hint for this slot as one line: what it is, where it lies and how it is marked."""
    return t(
        "**{item}** at {location} in **{finder}**'s world ({status})",
        lang,
        item=safe_text(hint.item, NAME_LIMIT),
        location=safe_text(hint.location, NAME_LIMIT),
        finder=safe_text(hint.finder, NAME_LIMIT),
        status=hint_status_label(hint.status, lang),
    )


def _line_in_my_world(hint: HintView, lang: str) -> str:
    return t(
        "**{item}** for **{receiver}** at {location} ({status})",
        lang,
        item=safe_text(hint.item, NAME_LIMIT),
        receiver=safe_text(hint.receiver, NAME_LIMIT),
        location=safe_text(hint.location, NAME_LIMIT),
        status=hint_status_label(hint.status, lang),
    )


def _hint_field(hints: Sequence[HintView], line: Callable[[HintView, str], str], lang: str) -> str:
    """At most MAX_HINTS lines that fit into a field, then how many open hints were left out (never silently)."""
    if not hints:
        return t("None", lang)
    listed = [line(hint, lang) for hint in hints[:MAX_HINTS]]
    shown, _ = fit_lines(listed, FIELD_VALUE - MORE_LINE_RESERVE)
    hidden = len(hints) - len(shown)
    text = "\n".join(shown)
    return f"{text}\n{t('…and {count} more', lang, count=hidden)}" if hidden else text


def _points(tracker: TrackerView, lang: str) -> str:
    if tracker.hint_cost <= 0:
        return t("Hints are free", lang)
    points = tracker.hint_points if tracker.hint_points is not None else 0
    return t("{points}, a hint costs {cost}", lang, points=points, cost=tracker.hint_cost)


def tracker_embed(tracker: TrackerView, lang: str, *, sections: Collection[str] = SECTIONS) -> discord.Embed:
    """`sections`: "summary" (counts, hint points, rules) and/or "hints" (the open hints)."""
    description = safe_text(tracker.game, NAME_LIMIT)
    if tracker.goal:
        description += SEPARATOR + t("Goal reached", lang)
    embed = discord.Embed(
        title=safe_text(tracker.name, TITLE_LIMIT), description=description, colour=SUCCESS if tracker.goal else ACCENT
    )
    if "summary" in sections:
        embed.add_field(name=t("Items received", lang), value=str(tracker.received))
        embed.add_field(name=t("Locations checked", lang), value=progress(tracker.checked, tracker.total))
        embed.add_field(name=t("Hint points", lang), value=_points(tracker, lang))
        embed.add_field(name=t("Release", lang), value=t(RULE_LABELS[tracker.release], lang))
        embed.add_field(name=t("Collect", lang), value=t(RULE_LABELS[tracker.collect], lang))
        embed.add_field(
            name=t("Latest progression items", lang),
            value=field_lines(
                [safe_text(item, NAME_LIMIT) for item in reversed(tracker.recent_progression)], t("None", lang), lang
            ),
            inline=False,
        )
    if "hints" in sections:
        embed.add_field(
            name=t("Hints for you", lang), value=_hint_field(hints_for_me(tracker.hints), line_for_me, lang), inline=False
        )
        embed.add_field(
            name=t("Hints in your world", lang),
            value=_hint_field(hints_in_my_world(tracker.hints), _line_in_my_world, lang),
            inline=False,
        )
    return embed
