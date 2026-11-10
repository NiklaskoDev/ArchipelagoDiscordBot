"""The Unraid template must stay in step with the settings the code reads, and must never carry a secret."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "unraid" / "ap-discord-bot.xml"
RAW = "https://raw.githubusercontent.com/NiklaskoDev/ArchipelagoDiscordBot/main/unraid"
SECRETS = {"DISCORD_TOKEN", "MANAGER_API_TOKEN", "BOT_ENCRYPTION_KEY"}
REQUIRED = {"DISCORD_TOKEN", "MANAGER_URL", "MANAGER_API_TOKEN", "BOT_ENCRYPTION_KEY", "PUID", "PGID", "/data"}


def template_root() -> ET.Element:
    return ET.parse(TEMPLATE).getroot()


def template_configs() -> dict[str, ET.Element]:
    return {config.get("Target"): config for config in template_root().iter("Config")}


def variables_read() -> set[str]:
    config_py = (ROOT / "apbot" / "config.py").read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    return (
        set(re.findall(r'(?:env\.get\(|_required\(env,\s*)"([A-Z][A-Z0-9_]*)"', config_py))
        | set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):-", entrypoint))
        | set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", env_example, re.M))
    )


def test_scan_finds_the_known_settings():
    known = {
        "DISCORD_TOKEN", "MANAGER_URL", "MANAGER_API_TOKEN", "BOT_ENCRYPTION_KEY", "DATA_DIR", "LOG_LEVEL",
        "DISCORD_DEV_GUILD_ID", "PUID", "PGID", "UMASK", "TZ",
    }
    assert known <= variables_read()


def test_template_variables_match_the_settings_the_code_reads():
    variables = {target for target, config in template_configs().items() if config.get("Type") == "Variable"}
    assert variables == variables_read()


def test_exactly_the_secrets_are_masked():
    masked = {target for target, config in template_configs().items() if config.get("Mask") == "true"}
    assert masked == SECRETS
    assert {config.get("Mask") for target, config in template_configs().items() if target not in SECRETS} == {"false"}


def test_secrets_carry_no_default_or_value():
    for target in SECRETS:
        config = template_configs()[target]
        assert config.get("Default") == ""
        assert (config.text or "") == ""


def test_required_settings():
    for target, config in template_configs().items():
        assert config.get("Required") == ("true" if target in REQUIRED else "false"), target


def test_defaults_are_in_both_default_and_value():
    configs = template_configs()
    for target, expected in {"PUID": "99", "PGID": "100", "UMASK": "022", "LOG_LEVEL": "INFO", "DATA_DIR": "/data"}.items():
        assert configs[target].get("Default") == expected, target
        assert configs[target].text == expected, target


def test_one_data_path_in_appdata():
    paths = [config for config in template_root().iter("Config") if config.get("Type") == "Path"]
    assert len(paths) == 1
    data = paths[0]
    assert data.get("Target") == "/data"
    assert data.get("Mode") == "rw"
    assert data.get("Default") == "/mnt/user/appdata/ap-discord-bot"


def test_header_points_at_the_image_and_the_raw_template():
    root = template_root()
    assert root.findtext("Repository") == "ghcr.io/niklaskodev/archipelagodiscordbot:latest"
    assert root.findtext("TemplateURL") == f"{RAW}/ap-discord-bot.xml"


def test_icon_is_this_repos_file():
    assert template_root().findtext("Icon") == f"{RAW}/icon.png"
    assert (ROOT / "unraid" / "icon.png").is_file()


def test_readme_tells_how_to_fetch_the_template():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "curl -fsSL -o /boot/config/plugins/dockerMan/templates-user/ap-discord-bot.xml" in readme
    assert template_root().findtext("TemplateURL") in readme


def test_template_stays_out_of_the_image():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "unraid" in ignored
