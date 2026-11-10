"""Text helpers for Discord: escaping what other people wrote, cutting to size, timestamps."""

import re
from collections.abc import Sequence

from discord.utils import escape_markdown

from apbot.ui.style import ELLIPSIS

ZERO_WIDTH_SPACE = "\u200b"
_WHITESPACE = re.compile(r"\s+")
# Numbered lists only count at the start of a line, and discord.py's escape_markdown leaves them alone (it escapes
# headings, quotes and "-" lists itself).
_NUMBERED_LIST = re.compile(r"^\d+[.)]\s")
TIMESTAMP_STYLES = "tTdDfFR"


def escape(text: str) -> str:
    """Makes text from the manager or a player harmless inside a message: one line, no markdown, no mentions.

    Mentions are broken up with a zero-width space (<@123> and @everyone alike); the bot also sends everything with
    AllowedMentions.none(), so this is the second line of defence and keeps the text from looking like a ping.
    """
    text = _WHITESPACE.sub(" ", text).strip()
    text = escape_markdown(text, ignore_links=False)
    text = text.replace("@", "@" + ZERO_WIDTH_SPACE).replace("<", "<" + ZERO_WIDTH_SPACE)
    if _NUMBERED_LIST.match(text):
        text = ZERO_WIDTH_SPACE + text
    return text


def clip(text: str, limit: int) -> str:
    """At most `limit` characters; a cut text ends in an ellipsis."""
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + ELLIPSIS


def safe_text(text: str, limit: int) -> str:
    """Escaped text of at most about `limit` characters. Cut before escaping, so a backslash never ends up dangling."""
    return escape(clip(_WHITESPACE.sub(" ", text).strip(), limit))


def ts(unix: float, style: str = "f") -> str:
    """A Discord timestamp: each reader sees it in their own time zone. Styles: t T d D f F, R (relative)."""
    if style not in TIMESTAMP_STYLES or len(style) != 1:
        raise ValueError(f"unknown timestamp style {style!r}")
    return f"<t:{int(unix)}:{style}>"


def progress(done: int, total: int) -> str:
    """"12/340 (4%)", or "0/0" when there is nothing to do yet."""
    if total <= 0:
        return f"{done}/{total}"
    return f"{done}/{total} ({done * 100 // total}%)"


def fit_lines(lines: Sequence[str], limit: int) -> tuple[list[str], int]:
    """The leading lines that fit into `limit` characters when joined by newlines, and how many were left out."""
    shown: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + (1 if shown else 0)
        if used + cost > limit:
            break
        shown.append(line)
        used += cost
    return shown, len(lines) - len(shown)


def chunk_lines(lines: Sequence[str], limit: int) -> list[str]:
    """Joins lines with newlines into texts of at most `limit` characters, never splitting a line (one longer than the
    limit is cut)."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        line = clip(line, limit)
        joined = f"{current}\n{line}" if current else line
        if len(joined) > limit:
            chunks.append(current)
            joined = line
        current = joined
    if current:
        chunks.append(current)
    return chunks
