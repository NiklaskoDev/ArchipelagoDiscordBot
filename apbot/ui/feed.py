"""What the activity feed looks like in a thread: one plain line per entry, and the lines grouped into messages.

Everything a player or the server wrote goes through `safe_text`, so a chat line cannot format the thread, ping anyone
or look like a line of the bot's own.
"""

from collections.abc import Callable, Sequence

from apbot.i18n import t, translatable
from apbot.manager.views import FeedEntry
from apbot.ui.format import clip, safe_text
from apbot.ui.style import MESSAGE, NAME_LIMIT, SEPARATOR

TEXT_LIMIT = 300  # what a player said, a note or a server message, before it is cut
MAX_MESSAGES = 5  # one batch of entries becomes at most this many messages

FEED_KIND_LABELS = {
    "goal": translatable("Goals reached"),
    "release": translatable("Releases"),
    "collect": translatable("Collects"),
    "join": translatable("Joins"),
    "part": translatable("Leaves"),
    "server": translatable("Server messages"),
    "hint": translatable("Hints"),
    "item": translatable("Items sent"),
    "chat": translatable("Chat"),
    "status": translatable("Status changes"),
    "handover": translatable("Slots offered"),
    "handover_done": translatable("Slots taken over"),
}
_HINT_STATUS_LABELS = {
    "found": translatable("found"),
    "not found": translatable("not found"),
    "unspecified": translatable("not found"),
    "priority": translatable("priority"),
    "no priority": translatable("no priority"),
    "avoid": translatable("avoid"),
}
_PLAYER_STATUS_LABELS = {
    "playing": translatable("Playing"),
    "stuck": translatable("Stuck"),
    "away": translatable("Away"),
}


def _name(entry: FeedEntry, field: str) -> str:
    return safe_text(entry.get(field), NAME_LIMIT) or "?"


def _text(entry: FeedEntry, field: str) -> str:
    return safe_text(entry.get(field), TEXT_LIMIT)


def _with_note(line: str, entry: FeedEntry) -> str:
    note = _text(entry, "note")
    return f"{line}{SEPARATOR}{note}" if note else line


def _hint(entry: FeedEntry, lang: str) -> str:
    raw_status = entry.get("status").strip().lower()
    status = t(_HINT_STATUS_LABELS[raw_status], lang) if raw_status in _HINT_STATUS_LABELS else _name(entry, "status")
    return t(
        "Hint: **{receiver}**'s {item} is at {location} in **{finder}**'s world ({status})",
        lang,
        receiver=_name(entry, "receiver"),
        item=_name(entry, "item"),
        location=_name(entry, "location"),
        finder=_name(entry, "finder"),
        status=status,
    )


def _status(entry: FeedEntry, lang: str) -> str:
    player = _name(entry, "player")
    raw = entry.get("status")
    if not raw:
        return t("**{player}** cleared their status", lang, player=player)
    label = t(_PLAYER_STATUS_LABELS[raw], lang) if raw in _PLAYER_STATUS_LABELS else _name(entry, "status")
    return _with_note(t("**{player}** changed their status to {status}", lang, player=player, status=label), entry)


def _handover(entry: FeedEntry, lang: str) -> str:
    values = {"player": _name(entry, "player"), "owner": _name(entry, "from")}
    if entry.get("to"):
        line = t("**{owner}** offers **{player}** to **{to}**", lang, **values, to=_name(entry, "to"))
    else:
        line = t("**{owner}** offers **{player}** to anyone who takes it", lang, **values)
    return _with_note(line, entry)


def _handover_done(entry: FeedEntry, lang: str) -> str:
    values = {"player": _name(entry, "player"), "to": _name(entry, "to")}
    if entry.get("from"):
        return t("**{to}** took over **{player}** from **{previous}**", lang, **values, previous=_name(entry, "from"))
    return t("**{to}** took over **{player}**", lang, **values)


_FORMATTERS: dict[str, Callable[[FeedEntry, str], str]] = {
    "goal": lambda e, lang: t("**{player}** reached their goal", lang, player=_name(e, "player")),
    "release": lambda e, lang: t(
        "**{player}** released all remaining items from their world", lang, player=_name(e, "player")
    ),
    "collect": lambda e, lang: t(
        "**{player}** collected their items from other worlds", lang, player=_name(e, "player")
    ),
    "join": lambda e, lang: t("**{player}** joined ({game})", lang, player=_name(e, "player"), game=_name(e, "game")),
    "part": lambda e, lang: t("**{player}** left", lang, player=_name(e, "player")),
    "server": lambda e, lang: t("Server: {message}", lang, message=_text(e, "text")),
    "hint": _hint,
    "item": lambda e, lang: t(
        "**{finder}** sent {item} to **{receiver}** ({location})",
        lang,
        finder=_name(e, "finder"),
        item=_name(e, "item"),
        receiver=_name(e, "receiver"),
        location=_name(e, "location"),
    ),
    "chat": lambda e, lang: t("**{player}**: {message}", lang, player=_name(e, "player"), message=_text(e, "text")),
    "status": _status,
    "handover": _handover,
    "handover_done": _handover_done,
}


def feed_line(entry: FeedEntry, lang: str) -> str:
    """One entry as a line of text in `lang`."""
    return _FORMATTERS[entry.kind](entry, lang)


def batch_lines(lines: Sequence[str], lang: str) -> list[str]:
    """Groups lines into as few messages as fit Discord's limit. After MAX_MESSAGES the rest is not sent: the last
    message says how many entries it leaves out (never silently)."""
    messages: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        line = clip(line, MESSAGE)
        cost = len(line) + (1 if current else 0)
        if current and size + cost > MESSAGE:
            messages.append(current)
            current, size = [line], len(line)
        else:
            current.append(line)
            size += cost
    if current:
        messages.append(current)
    kept = messages[:MAX_MESSAGES]
    hidden = sum(len(message) for message in messages[MAX_MESSAGES:])
    if hidden:
        last = kept[-1]
        while last and len("\n".join([*last, _overflow_line(hidden, lang)])) > MESSAGE:
            last.pop()
            hidden += 1
        last.append(_overflow_line(hidden, lang))
    return ["\n".join(message) for message in kept]


def _overflow_line(hidden: int, lang: str) -> str:
    return t("…and {count} more events", lang, count=hidden)
