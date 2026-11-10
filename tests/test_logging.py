import logging

from apbot.logging_setup import REDACTED, RedactingFilter, redact, setup_logging

# Built from parts so secret scanners do not mistake this test value for a real token.
DISCORD_TOKEN = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4OQ", "GaBcDe", "abcdefghijklmnopqrstuvwxyz0123456789"])
FERNET_KEY = "ZmRzYWZkc2FmZHNhZmRzYWZkc2FmZHNhZmRzYWZkc2E="


def logged(message, *args, exc_info=None):
    """The text a handler with the redacting filter writes for one record."""
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, message, args, exc_info)
    assert handler.filter(record)
    return logging.Formatter("%(message)s").format(record)


def test_manager_token_is_redacted():
    assert logged("token apm_abc123-_XYZ was used") == f"token {REDACTED} was used"


def test_discord_token_is_redacted():
    assert DISCORD_TOKEN not in logged("login with %s", DISCORD_TOKEN)


def test_fernet_key_is_redacted():
    assert FERNET_KEY not in logged("key %s", FERNET_KEY)
    assert REDACTED in logged("key %s", FERNET_KEY)


def test_secret_in_a_traceback_is_redacted():
    try:
        raise RuntimeError("failed with apm_secretInException")
    except RuntimeError:
        import sys

        text = logged("boom", exc_info=sys.exc_info())
    assert "apm_secretInException" not in text
    assert "RuntimeError" in text


def test_ordinary_text_is_left_alone():
    assert redact("Game 12 is running at host.example:38281") == "Game 12 is running at host.example:38281"


def test_setup_logging_quiets_http_libraries_and_redacts():
    setup_logging("DEBUG")
    try:
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        handler = logging.getLogger().handlers[0]
        assert any(isinstance(f, RedactingFilter) for f in handler.filters)
    finally:
        logging.getLogger().handlers.clear()
        logging.getLogger().setLevel(logging.WARNING)
