"""Translations. The English text is the key; each language has a catalog keyed by it.

`MESSAGES` holds replies, embeds and buttons, `COMMANDS` the names and descriptions of slash commands, options and
choices. A text without an entry is shown in English. tests/test_i18n.py keeps the catalogs complete.
"""

import discord
from discord import app_commands

from apbot.i18n import de, fr, nl

LANGUAGES = ("en", "de", "fr", "nl")
DEFAULT_LANGUAGE = "en"

_CATALOGS = {"de": de, "fr": fr, "nl": nl}


def language_of(locale: discord.Locale | str | None) -> str:
    """The bot's language for a Discord locale such as "de", "en-GB" or "pt-BR"; English when there is no translation."""
    text = str(getattr(locale, "value", locale) or "")
    language = text.split("-")[0].lower()
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def translatable(text: str) -> str:
    """Marks a text for the catalogs without translating it yet (for texts picked from a table, then passed to t)."""
    return text


def t(text: str, lang: str, **values: object) -> str:
    """The text in a language, with its {placeholders} filled in."""
    catalog = _CATALOGS.get(lang)
    translated = catalog.MESSAGES.get(text, text) if catalog else text
    return translated.format(**values) if values else translated


class BotTranslator(app_commands.Translator):
    """Gives Discord the localized names and descriptions of commands (locale_str) for each language."""

    async def translate(
        self, string: app_commands.locale_str, locale: discord.Locale, context: app_commands.TranslationContext
    ) -> str | None:
        catalog = _CATALOGS.get(language_of(locale))
        return catalog.COMMANDS.get(string.message) if catalog else None
