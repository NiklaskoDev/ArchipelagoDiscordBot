"""Reading a date and time that a person typed, in the time zone of their server."""

import re
import zoneinfo
from datetime import UTC, datetime

from apbot.errors import UserFacingError

_LOCAL_DATETIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$")
DATETIME_FORMAT = "YYYY-MM-DD HH:MM"


def parse_local_datetime(text: str, timezone: str) -> float:
    """The unix time of "YYYY-MM-DD HH:MM" on the clock of `timezone`. Raises UserFacingError for text in another
    format, a date that does not exist, a time that never happens on that clock (the hour skipped when clocks go
    forward) or a time zone the system does not know. A time that happens twice (clocks go back) is the first one."""
    match = _LOCAL_DATETIME.match(text.strip())
    if match is None:
        raise UserFacingError("Write the date as YYYY-MM-DD HH:MM, for example 2030-05-17 20:00")
    try:
        zone = zoneinfo.ZoneInfo(timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise UserFacingError("This server's time zone is not known. An admin can set it with /setup timezone") from None
    try:
        local = datetime(*map(int, match.groups()), tzinfo=zone)
    except ValueError:
        raise UserFacingError("That date or time does not exist") from None
    moment = local.astimezone(UTC)
    if moment.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise UserFacingError("That time does not exist in this server's time zone (the clocks skip it)")
    return moment.timestamp()
