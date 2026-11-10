import re
import string

import discord
import pytest
from discord import app_commands
from fakes import make_bot
from i18n_scan import scan

from apbot.cogs.games import GamesCog
from apbot.cogs.signups import SignupsCog
from apbot.i18n import LANGUAGES, BotTranslator, de, fr, language_of, nl, t

CATALOGS = {"de": de, "fr": fr, "nl": nl}
COMMAND_NAME = re.compile(r"^[-_\w]{1,32}$")
NAME_LIKE = re.compile(r"^[a-z0-9_-]{1,32}$")  # an English text that is a command, option or choice name
MAX_DESCRIPTION = 100
SAME_IN_EVERY_LANGUAGE = {"Offline", "Online", "Slots"}


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


MESSAGES, COMMANDS, PROBLEMS = scan()


def test_the_source_scan_finds_texts():
    assert "Games" in MESSAGES and "games" in COMMANDS
    assert not PROBLEMS


@pytest.mark.parametrize("lang", CATALOGS)
def test_every_text_is_translated_and_no_translation_is_left_over(lang):
    catalog = CATALOGS[lang]
    assert set(catalog.MESSAGES) == MESSAGES
    assert set(catalog.COMMANDS) == COMMANDS


@pytest.mark.parametrize("lang", CATALOGS)
def test_translations_keep_the_placeholders(lang):
    for english, translated in CATALOGS[lang].MESSAGES.items():
        assert placeholders(translated) == placeholders(english), english
        assert translated.strip(), english
        assert translated != english or english in SAME_IN_EVERY_LANGUAGE, english


@pytest.mark.parametrize("lang", CATALOGS)
def test_translated_names_are_valid_for_discord(lang):
    for english, translated in CATALOGS[lang].COMMANDS.items():
        if NAME_LIKE.match(english):
            assert COMMAND_NAME.match(translated), translated
            assert translated == translated.lower(), translated
        else:
            assert len(translated) <= MAX_DESCRIPTION, translated


def test_english_descriptions_fit_discords_limit():
    assert all(len(text) <= MAX_DESCRIPTION for text in COMMANDS if not NAME_LIKE.match(text))


def test_t_uses_the_language_and_falls_back_to_english():
    assert t("Games", "en") == "Games"
    assert t("Games", "de") == "Spiele"
    assert t("A text nobody translated", "fr") == "A text nobody translated"
    assert t("Goals {done}/{total}", "nl", done=1, total=2) == "Doelen 1/2"
    assert t("Games", "xx") == "Games"


@pytest.mark.parametrize(
    ("locale", "language"),
    [
        ("de", "de"),
        ("en-GB", "en"),
        ("pt-BR", "en"),
        ("fr", "fr"),
        ("nl", "nl"),
        ("DE", "de"),
        (None, "en"),
        ("", "en"),
        (discord.Locale.german, "de"),
        (discord.Locale.dutch, "nl"),
        (discord.Locale.british_english, "en"),
        (discord.Locale.brazil_portuguese, "en"),
    ],
)
def test_language_of(locale, language):
    assert language_of(locale) == language
    assert language in LANGUAGES


async def test_translator_gives_discord_the_localized_text():
    translator = BotTranslator()
    context = app_commands.TranslationContext(app_commands.TranslationContextLocation.command_name, None)
    assert await translator.translate(app_commands.locale_str("games"), discord.Locale.german, context) == "spiele"
    assert await translator.translate(app_commands.locale_str("games"), discord.Locale.american_english, context) is None
    assert await translator.translate(app_commands.locale_str("unknown"), discord.Locale.french, context) is None


async def command_payloads(tmp_path, manager) -> list[dict]:
    """What the bot would send to Discord when syncing: names and descriptions with their localizations."""
    bot = make_bot(tmp_path, manager)
    await bot.add_cog(GamesCog(bot))
    await bot.add_cog(SignupsCog(bot))
    translator = BotTranslator()
    payloads = [await command.get_translated_payload(bot.tree, translator) for command in bot.tree.get_commands()]
    await bot.close()
    return payloads


async def test_every_command_has_all_three_localizations(tmp_path, manager):
    payloads = await command_payloads(tmp_path, manager)
    assert {p["name"] for p in payloads} == {"games", "game", "signups"}
    for payload in payloads:
        assert set(payload["name_localizations"]) == {"de", "fr", "nl"}
        assert set(payload["description_localizations"]) == {"de", "fr", "nl"}
        for option in payload.get("options", []):
            assert set(option["name_localizations"]) == {"de", "fr", "nl"}
            assert set(option["description_localizations"]) == {"de", "fr", "nl"}


async def test_localized_names_are_unique_and_valid_in_each_language(tmp_path, manager):
    """Discord refuses a command whose localized name repeats a sibling's or has characters it does not allow."""
    payloads = await command_payloads(tmp_path, manager)
    for locale in ("de", "fr", "nl"):
        names = [p["name_localizations"][locale] for p in payloads]
        assert len(names) == len(set(names)), (locale, names)
        assert all(COMMAND_NAME.match(name) and name == name.lower() for name in names)
        for payload in payloads:
            options = [o["name_localizations"][locale] for o in payload.get("options", [])]
            assert len(options) == len(set(options)), (locale, payload["name"], options)
