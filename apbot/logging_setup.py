"""Logging that never writes a secret, whichever library produced the record."""

import logging
import re

REDACTED = "[redacted]"

# Manager API tokens, Discord bot tokens (three dot-separated base64url parts) and Fernet keys (44 characters of
# base64url ending in "=").
_SECRET_PATTERNS = (
    re.compile(r"apm_[A-Za-z0-9_-]+"),
    re.compile(r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{25,}"),
    re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}="),
)
_QUIET_LOGGERS = ("httpx", "httpcore")


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    """Replaces secrets in the message and in the traceback of every record that passes through a handler."""

    def __init__(self):
        super().__init__()
        self._formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = self._formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
