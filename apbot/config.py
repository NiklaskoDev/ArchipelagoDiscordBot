"""Settings read from the environment.

Errors name the variable, never its value: the values are secrets or may contain them.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MANAGER_TOKEN_PATTERN = re.compile(r"^apm_[A-Za-z0-9_-]+$")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_DATA_DIR = "/data"


class ConfigError(Exception):
    """A setting is missing or invalid. The message names the variable only."""

    def __init__(self, variable: str, problem: str):
        super().__init__(f"{variable}: {problem}")
        self.variable = variable


@dataclass(frozen=True)
class Config:
    discord_token: str
    manager_url: str
    manager_api_token: str
    data_dir: Path
    log_level: str
    dev_guild_id: int | None

    def __repr__(self) -> str:
        # The default dataclass repr would print both tokens.
        return f"Config(manager_url={self.manager_url!r}, data_dir={str(self.data_dir)!r}, log_level={self.log_level!r})"


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(name, "is required but not set")
    return value


def _manager_url(raw: str) -> str:
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("MANAGER_URL", "must look like http://host:port or https://host")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.password:
        raise ConfigError("MANAGER_URL", "must be an address without a path, query or credentials")
    return raw.rstrip("/")


def _dev_guild_id(raw: str) -> int | None:
    if not raw:
        return None
    if not raw.isdecimal() or int(raw) <= 0:
        raise ConfigError("DISCORD_DEV_GUILD_ID", "must be a Discord server id (a positive number)")
    return int(raw)


def load_config(env: Mapping[str, str]) -> Config:
    """Reads and validates the settings; raises ConfigError naming the first problem."""
    discord_token = _required(env, "DISCORD_TOKEN")
    manager_url = _manager_url(_required(env, "MANAGER_URL"))
    manager_token = _required(env, "MANAGER_API_TOKEN")
    if not MANAGER_TOKEN_PATTERN.match(manager_token):
        raise ConfigError("MANAGER_API_TOKEN", "must be a manager API token (it starts with apm_)")

    log_level = env.get("LOG_LEVEL", "").strip().upper() or "INFO"
    if log_level not in LOG_LEVELS:
        raise ConfigError("LOG_LEVEL", f"must be one of {', '.join(LOG_LEVELS)}")

    return Config(
        discord_token=discord_token,
        manager_url=manager_url,
        manager_api_token=manager_token,
        data_dir=Path(env.get("DATA_DIR", "").strip() or DEFAULT_DATA_DIR),
        log_level=log_level,
        dev_guild_id=_dev_guild_id(env.get("DISCORD_DEV_GUILD_ID", "").strip()),
    )
