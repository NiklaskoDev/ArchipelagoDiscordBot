import pytest

from apbot.config import ConfigError, load_config

VALID = {
    "DISCORD_TOKEN": "discord-secret-value",
    "MANAGER_URL": "http://archipelago-manager:8080",
    "MANAGER_API_TOKEN": "apm_secretTokenValue",
}


def load(**changes):
    env = {**VALID, **changes}
    return load_config({k: v for k, v in env.items() if v is not None})


def test_valid_settings_with_defaults():
    config = load()
    assert config.manager_url == "http://archipelago-manager:8080"
    assert str(config.data_dir).replace("\\", "/") == "/data"
    assert config.log_level == "INFO"
    assert config.dev_guild_id is None


def test_optional_settings_are_applied():
    config = load(DATA_DIR="/var/bot", LOG_LEVEL="debug", DISCORD_DEV_GUILD_ID="123456789")
    assert str(config.data_dir).replace("\\", "/") == "/var/bot"
    assert config.log_level == "DEBUG"
    assert config.dev_guild_id == 123456789


@pytest.mark.parametrize("name", ["DISCORD_TOKEN", "MANAGER_URL", "MANAGER_API_TOKEN"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_required_variable_is_named(name, value):
    with pytest.raises(ConfigError) as error:
        load(**{name: value})
    assert error.value.variable == name
    assert name in str(error.value)


def test_error_never_contains_a_value():
    with pytest.raises(ConfigError) as error:
        load(MANAGER_API_TOKEN="not-a-manager-token-123")
    assert "not-a-manager-token-123" not in str(error.value)
    with pytest.raises(ConfigError) as error:
        load(MANAGER_URL="http://user:hunter2@manager:8080")
    assert "hunter2" not in str(error.value)


def test_manager_token_must_start_with_apm():
    with pytest.raises(ConfigError) as error:
        load(MANAGER_API_TOKEN="abc123")
    assert error.value.variable == "MANAGER_API_TOKEN"
    with pytest.raises(ConfigError):
        load(MANAGER_API_TOKEN="apm_has space")


@pytest.mark.parametrize(
    "url",
    ["http://manager:8080/api", "http://manager:8080/?x=1", "ftp://manager", "manager:8080", "http://", "https://m#frag"],
)
def test_manager_url_must_be_an_origin(url):
    with pytest.raises(ConfigError) as error:
        load(MANAGER_URL=url)
    assert error.value.variable == "MANAGER_URL"


def test_manager_url_trailing_slash_is_removed():
    assert load(MANAGER_URL="https://manager.example/").manager_url == "https://manager.example"


@pytest.mark.parametrize("value", ["abc", "0", "-5", "12.5"])
def test_dev_guild_id_must_be_a_positive_number(value):
    with pytest.raises(ConfigError) as error:
        load(DISCORD_DEV_GUILD_ID=value)
    assert error.value.variable == "DISCORD_DEV_GUILD_ID"


def test_unknown_log_level_is_refused():
    with pytest.raises(ConfigError) as error:
        load(LOG_LEVEL="LOUD")
    assert error.value.variable == "LOG_LEVEL"


def test_printing_the_config_does_not_show_the_tokens():
    text = repr(load())
    assert "discord-secret-value" not in text
    assert "apm_secretTokenValue" not in text
