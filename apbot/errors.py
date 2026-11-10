"""Errors that end up in front of a person, in their language."""

from apbot.i18n import t
from apbot.manager.errors import ManagerError, ManagerUnavailable, TokenInvalid, TooManyRequests
from apbot.ui.format import safe_text

DETAIL_LIMIT = 200


class UserFacingError(Exception):
    """Something the person can fix or should know. `key` is the English text, `values` fill its {placeholders}."""

    def __init__(self, key: str, **values: object):
        super().__init__(key)
        self.key = key
        self.values = values

    def message(self, lang: str) -> str:
        return t(self.key, lang, **self.values)


def user_message(error: BaseException, lang: str) -> str | None:
    """What to tell the person about an error, or None when it is a bug (the caller logs it and says "went wrong")."""
    if isinstance(error, UserFacingError):
        return error.message(lang)
    if isinstance(error, ManagerUnavailable):
        return t("The manager cannot be reached right now. Try again in a moment.", lang)
    if isinstance(error, TokenInvalid):
        return t("The manager does not accept the bot's access any more. Ask a server admin to check it.", lang)
    if isinstance(error, TooManyRequests):
        return t("The manager is busy. Try again in a moment.", lang)
    if isinstance(error, ManagerError):
        return t("The manager answered: {detail}", lang, detail=safe_text(error.detail, DETAIL_LIMIT))
    return None
