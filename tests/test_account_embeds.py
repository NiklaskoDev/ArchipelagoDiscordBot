"""The embeds added with linking, settings and game control."""

import discord
from fakes import game_json, json_text

from apbot.db.guilds import GuildSettings
from apbot.db.links import UserLink
from apbot.manager.views import parse_game_summary
from apbot.services.manager_watch import ManagerReport
from apbot.ui.embeds import (
    game_state_embed,
    link_status_embed,
    linked_embed,
    manager_check_embed,
    notice_embed,
    settings_embed,
)
from apbot.ui.format import ZERO_WIDTH_SPACE


def link(**overrides) -> UserLink:
    values = dict(
        discord_user_id=1, manager_user_id=10, manager_username="alice", token_hint="abcd", status="active",
        locale="de", linked_at=1_700_000_000.0, checked_at=1_700_000_500.0, token_ciphertext=b"cipher-bytes",
    )
    return UserLink(**{**values, **overrides})


def report(**overrides) -> ManagerReport:
    values = dict(version="1.3.0", ready=True, username="apbot", role="user", site_url="https://m.example")
    return ManagerReport(**{**values, **overrides})


def fields(embed: discord.Embed) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


def test_stored_data_embed_has_every_stored_field_and_not_the_ciphertext():
    embed = link_status_embed(link(), "en")
    assert fields(embed) == {
        "Manager account": "alice",
        "Token": "…abcd",
        "Status": "Active",
        "Language": "Deutsch",
        "Linked": "<t:1700000000:f>",
        "Last checked": "<t:1700000500:f>",
        "Alerts": "None",
    }
    assert "cipher-bytes" not in json_text(embed)


def test_stored_data_is_localized():
    embed = link_status_embed(link(status="invalid"), "fr")
    assert embed.title == "Tes données enregistrées"
    assert fields(embed)["Statut"] == "Ne fonctionne plus. Relance /link."


def test_stored_data_without_a_token_hint_has_no_token_field():
    assert "Token" not in fields(link_status_embed(link(token_hint=None), "en"))


def test_stored_data_escapes_the_manager_username():
    assert fields(link_status_embed(link(manager_username="*bold* @everyone"), "en"))["Manager account"] == (
        r"\*bold\* @" + ZERO_WIDTH_SPACE + "everyone"
    )


def test_stored_data_of_someone_not_linked():
    embed = link_status_embed(None, "en")
    assert not embed.fields
    assert embed.description == "No manager account is linked to your Discord account. Nothing else is stored about you."


def test_linked_embed_names_the_account_in_the_success_colour():
    embed = linked_embed("alice", "nl")
    assert embed.description == "Gekoppeld als **alice**"
    assert embed.colour == discord.Colour(0x2E7D32)


def test_settings_embed_shows_defaults_and_set_values():
    default = fields(settings_embed(GuildSettings(1), "en"))
    assert default == {
        "Language": "English", "Time zone": "UTC", "Games channel": "Not set", "Sign-ups channel": "Not set",
        "Overview channel": "Not set", "Organizer role": "Not set", "Admin role": "Not set",
        "Scheduled events": "Off",
    }
    custom = settings_embed(
        GuildSettings(1, language="nl", timezone="Europe/Berlin", games_channel_id=5, signups_channel_id=6,
                      overview_channel_id=7, organizer_role_id=8, admin_role_id=9),
        "de",
    )
    assert fields(custom) == {
        "Sprache": "Nederlands", "Zeitzone": "Europe/Berlin", "Spielekanal": "<#5>", "Anmeldekanal": "<#6>",
        "Übersichtskanal": "<#7>", "Organisator-Rolle": "<@&8>", "Admin-Rolle": "<@&9>",
        "Geplante Events": "Aus",
    }


def test_manager_check_with_everything_fine_is_green():
    embed = manager_check_embed(report(), "en")
    assert embed.colour == discord.Colour(0x2E7D32)
    assert fields(embed)["Bot account"] == "apbot (user)"


def test_manager_check_without_an_answer_is_red_and_has_one_line():
    embed = manager_check_embed(None, "en")
    assert embed.colour == discord.Colour(0xC62828)
    assert fields(embed) == {"Reachable": "No. The manager did not answer."}


def test_manager_check_with_an_unknown_version_is_a_warning():
    embed = manager_check_embed(report(version=None), "en")
    assert fields(embed)["Version"] == "Unknown. The bot needs 1.3.0 or newer."
    assert embed.colour == discord.Colour(0xB26A00)


def test_manager_check_escapes_what_the_manager_sent():
    embed = manager_check_embed(report(username="*x*", role="@everyone", version="1.3.0"), "en")
    assert "@everyone" not in fields(embed)["Bot account"]
    assert fields(embed)["Bot account"].startswith(r"\*x\* (@" + ZERO_WIDTH_SPACE + "everyone)")


def test_game_state_embed_shows_name_and_state_with_the_state_colour():
    embed = game_state_embed(parse_game_summary(game_json(3, "Friday *Run*", state="crashed")), "en")
    assert (embed.title, embed.description) == (r"Friday \*Run\*", "Crashed")
    assert embed.colour == discord.Colour(0xC62828)


def test_notice_embed_is_plain_text_in_the_given_colour():
    assert notice_embed("Hello").colour == discord.Colour(0x2F6F9F)
    assert notice_embed("Hello", discord.Colour(1)).colour == discord.Colour(1)
