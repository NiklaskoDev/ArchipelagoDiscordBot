"""What the manager can answer with, as exceptions. The manager's `detail` is English and meant for people."""

READ_ONLY_DETAIL = "This API token can only read"


class ManagerError(Exception):
    """The manager answered with an error, or could not be asked. `status` is None when there was no answer."""

    def __init__(self, status: int | None, detail: str):
        super().__init__(f"{status}: {detail}" if status else detail)
        self.status = status
        self.detail = detail


class ManagerUnavailable(ManagerError):
    """No answer (network, timeout) or a server-side failure (5xx). Worth retrying for reads."""


class TokenInvalid(ManagerError):
    """401: the token is wrong, revoked or its account is disabled."""


class ReadOnlyToken(ManagerError):
    """403: a read token was used for a change."""


class Forbidden(ManagerError):
    """403: the account may not do this."""


class NotFound(ManagerError):
    """404: it does not exist, or is not visible to this account."""


class Conflict(ManagerError):
    """409: not possible in the current state."""


class TooManyRequests(ManagerError):
    """429."""


class BadRequest(ManagerError):
    """400, 413 or 422: the request itself is wrong."""
