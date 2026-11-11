import re
import string

import discord
import pytest
from discord import app_commands
from fakes import make_bot
from i18n_scan import scan

from apbot.cogs.alerts import AlertsCog
from apbot.cogs.games import GamesCog
from apbot.cogs.generate import GenerateCog
from apbot.cogs.linking import LinkingCog
from apbot.cogs.setup import SetupCog
from apbot.cogs.signups import SignupsCog
from apbot.cogs.slots import SlotsCog
from apbot.cogs.textclient import TextClientCog
from apbot.cogs.tracker import TrackerCog
from apbot.i18n import LANGUAGES, BotTranslator, de, fr, language_of, nl, t

CATALOGS = {"de": de, "fr": fr, "nl": nl}
COMMAND_NAME = re.compile(r"^[-_\w]{1,32}$")
NAME_LIKE = re.compile(r"^[a-z0-9_-]{1,32}$")  # an English text that is a command, option or choice name
MAX_DESCRIPTION = 100
SAME_IN_EVERY_LANGUAGE = {
    "**{player}**: {message}", "{count} slot", "{count} slots", "Chat", "Hints", "Manager", "Offline", "Online", "Server: {message}", "Slots", "Status", "Token",
    "Version",
}


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
    await bot.add_cog(LinkingCog(bot))
    await bot.add_cog(SetupCog(bot))
    await bot.add_cog(AlertsCog(bot))
    await bot.add_cog(TrackerCog(bot))
    await bot.add_cog(TextClientCog(bot))
    await bot.add_cog(SlotsCog(bot))
    await bot.add_cog(GenerateCog(bot))
    translator = BotTranslator()
    payloads = [await command.get_translated_payload(bot.tree, translator) for command in bot.tree.get_commands()]
    await bot.close()
    return payloads


COMMAND_NAMES = {
    "games", "game", "signups", "signup", "wake", "start", "stop", "restart", "link", "unlink", "mydata", "setup", "alerts",
    "tracker", "hints", "hint", "hint_location", "remaining", "say", "release", "collect", "status", "claim", "handover",
    "generate", "game_archive", "game_delete",
}
LOCALES = {"de", "fr", "nl"}


def walk(payload: dict, path: str = ""):
    """The command and everything below it: subcommands, their options and the options' choices."""
    here = f"{path}/{payload['name']}"
    yield here, payload
    for child in [*payload.get("options", []), *payload.get("choices", [])]:
        yield from walk(child, here)


async def test_every_command_has_all_three_localizations(tmp_path, manager):
    payloads = await command_payloads(tmp_path, manager)
    assert {p["name"] for p in payloads} == COMMAND_NAMES
    seen = set()
    for payload in payloads:
        for path, item in walk(payload):
            if "description" in item:  # choices have a name only
                assert set(item["description_localizations"]) == LOCALES, path
            if "value" in item and "name_localizations" not in item:
                continue  # a choice that is the same in every language, such as a language's own name
            assert set(item["name_localizations"]) == LOCALES, path
            seen.add(path)
    # the walk really went into subcommands, their options and the options' choices
    assert {"/setup/channel", "/setup/channel/kind", "/setup/channel/kind/games", "/setup/role/kind/organizer"} <= seen


async def test_localized_names_are_unique_and_valid_in_each_language(tmp_path, manager):
    """Discord refuses a command whose localized name repeats a sibling's or has characters it does not allow."""
    payloads = await command_payloads(tmp_path, manager)
    for locale in LOCALES:
        names = [p["name_localizations"][locale] for p in payloads]
        assert len(names) == len(set(names)), (locale, names)
        assert all(COMMAND_NAME.match(name) and name == name.lower() for name in names)
        for payload in payloads:
            for path, item in walk(payload):
                children = [*item.get("options", []), *item.get("choices", [])]
                localized = [c["name_localizations"][locale] for c in children if "name_localizations" in c]
                assert len(localized) == len(set(localized)), (locale, path, localized)
                if "options" in item or "choices" in item:
                    assert all(COMMAND_NAME.match(name) and name == name.lower() for name in localized), (locale, path)


async def test_setup_is_server_only_and_needs_manage_server_by_default(tmp_path, manager):
    payloads = {p["name"]: p for p in await command_payloads(tmp_path, manager)}
    setup = payloads["setup"]
    assert setup["dm_permission"] is False
    assert int(setup["default_member_permissions"]) == discord.Permissions(manage_guild=True).value
    assert {option["name"] for option in setup["options"]} == {
        "show", "manager", "channel", "role", "role_clear", "language", "timezone", "feed", "events"
    }


async def test_commands_limited_to_organizers_only_work_in_a_server(tmp_path, manager):
    payloads = {p["name"]: p for p in await command_payloads(tmp_path, manager)}
    for name in ("start", "stop", "restart", "generate", "game_archive", "game_delete"):
        assert payloads[name]["dm_permission"] is False, name
    for name in ("games", "game", "signups", "wake", "link", "unlink", "mydata"):
        assert payloads[name]["dm_permission"] is True, name


async def test_the_player_tools_exist_with_their_options_and_work_in_direct_messages_too(tmp_path, manager):
    payloads = {p["name"]: p for p in await command_payloads(tmp_path, manager)}
    for name in ("tracker", "hints", "hint", "hint_location", "remaining", "say", "release", "collect", "status", "claim"):
        assert payloads[name]["dm_permission"] is True, name
    options = {name: [o["name"] for o in payloads[name]["options"]] for name in payloads}
    assert options["hint"] == ["game", "item", "slot"] and options["hint_location"] == ["game", "location", "slot"]
    assert options["say"] == ["game", "text", "slot"] and options["claim"] == ["game", "slot"]
    assert options["status"] == ["game", "status", "slot", "note"] and options["tracker"] == ["game", "slot"]
    assert [o["name"] for o in payloads["handover"]["options"]] == ["offer", "cancel"]
    offer = payloads["handover"]["options"][0]
    assert [o["name"] for o in offer["options"]] == ["game", "slot", "member", "username", "note"]
    choices = [c["name"] for c in next(o for o in payloads["status"]["options"] if o["name"] == "status")["choices"]]
    assert choices == ["playing", "stuck", "away", "clear"]


async def test_the_slot_option_is_optional_and_the_game_option_is_required(tmp_path, manager):
    payloads = {p["name"]: p for p in await command_payloads(tmp_path, manager)}
    for name in ("tracker", "hints", "hint", "remaining", "say", "release", "collect", "status"):
        required = {o["name"]: o.get("required", False) for o in payloads[name]["options"]}
        assert required["game"] is True and required["slot"] is False, name
    assert {o["name"]: o["required"] for o in payloads["claim"]["options"]}["slot"] is True


async def test_the_signup_commands_have_their_options_limits_and_suggestions(tmp_path, manager):
    payloads = {p["name"]: p for p in await command_payloads(tmp_path, manager)}
    signup = payloads["signup"]
    subcommands = {option["name"]: option for option in signup["options"]}
    assert {name: [o["name"] for o in sub["options"]] for name, sub in subcommands.items()} == {
        "show": ["round"],
        "join": ["round", "file", "config"],
        "withdraw": ["round", "entry"],
        "open": ["name", "deadline", "slots_per_user", "description"],
        "close": ["round"],
        "test": ["round"],
    }
    options = {name: {o["name"]: o for o in sub["options"]} for name, sub in subcommands.items()}
    assert all(options[name]["round"].get("autocomplete") for name in ("show", "join", "withdraw", "close", "test"))
    assert options["join"]["config"]["autocomplete"] and options["withdraw"]["entry"]["autocomplete"]
    assert options["join"]["file"]["type"] == 11  # an attachment
    assert not options["join"]["file"].get("required") and not options["join"]["config"].get("required")
    assert options["open"]["name"]["required"] is True and not options["open"]["deadline"].get("required")
    slots = options["open"]["slots_per_user"]
    assert (slots["min_value"], slots["max_value"]) == (1, 20) and not slots.get("required")
    assert options["open"]["description"]["max_length"] == 2000 and options["open"]["name"]["max_length"] == 120
    assert signup["dm_permission"] is True  # show, join and withdraw work in direct messages; the others check the server


async def test_the_generate_command_has_ten_config_options_with_suggestions_and_its_choices(tmp_path, manager):
    payloads = {p["name"]: p for p in await command_payloads(tmp_path, manager)}
    options = {o["name"]: o for o in payloads["generate"]["options"]}
    configs = ["config"] + [f"config{n}" for n in range(2, 11)]
    assert list(options) == [*configs, "name", "spoiler", "race", "seed", "start"]
    assert all(options[name]["autocomplete"] for name in configs)
    assert options["config"]["required"] is True
    assert not any(options[name].get("required") for name in options if name != "config")
    assert options["name"]["max_length"] == 120 and options["seed"]["max_length"] == 18
    assert [(c["name"], c["value"]) for c in options["spoiler"]["choices"]] == [
        ("none", 0), ("basic", 1), ("playthrough", 2), ("full", 3)
    ]


async def test_the_archive_and_delete_commands_have_their_options_choices_and_suggestions(tmp_path, manager):
    payloads = {p["name"]: p for p in await command_payloads(tmp_path, manager)}
    archive = {o["name"]: o for o in payloads["game_archive"]["options"]}
    assert list(archive) == ["action", "game"]  # the action first, so the suggestions know whether to restore
    assert [c["value"] for c in archive["action"]["choices"]] == ["archive", "restore"]
    assert archive["game"]["autocomplete"] and archive["game"]["required"] and archive["action"]["required"]
    [delete] = payloads["game_delete"]["options"]
    assert (delete["name"], delete["autocomplete"], delete["required"]) == ("game", True, True)
