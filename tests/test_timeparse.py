from datetime import UTC, datetime

import pytest

from apbot.errors import UserFacingError
from apbot.services.timeparse import parse_local_datetime


def utc(year, month, day, hour, minute=0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


def test_a_time_is_read_on_the_clock_of_the_servers_time_zone():
    assert parse_local_datetime("2030-05-17 20:00", "Europe/Berlin") == utc(2030, 5, 17, 18)  # summer time, UTC+2
    assert parse_local_datetime("2030-01-17 20:00", "Europe/Berlin") == utc(2030, 1, 17, 19)  # winter time, UTC+1
    assert parse_local_datetime("2030-05-17 20:00", "UTC") == utc(2030, 5, 17, 20)
    assert parse_local_datetime("2030-05-17 20:00", "America/New_York") == utc(2030, 5, 18, 0)


def test_surrounding_spaces_and_a_t_between_date_and_time_are_fine():
    assert parse_local_datetime("  2030-05-17T20:15 ", "UTC") == utc(2030, 5, 17, 20, 15)


@pytest.mark.parametrize(
    "text", ["", "tomorrow", "17.05.2030 20:00", "2030-5-17 20:00", "2030-05-17", "2030-05-17 20:00:00", "2030-05-17 8pm"]
)
def test_text_in_another_format_is_refused_with_the_format_to_use(text):
    with pytest.raises(UserFacingError) as error:
        parse_local_datetime(text, "UTC")
    assert "YYYY-MM-DD HH:MM" in error.value.message("en")


@pytest.mark.parametrize("text", ["2030-02-30 10:00", "2030-13-01 10:00", "2030-05-17 25:00", "2030-05-17 20:61"])
def test_a_date_or_time_that_does_not_exist_is_refused(text):
    with pytest.raises(UserFacingError) as error:
        parse_local_datetime(text, "UTC")
    assert error.value.message("en") == "That date or time does not exist"


def test_the_hour_that_clocks_skip_is_refused():
    with pytest.raises(UserFacingError) as error:
        parse_local_datetime("2030-03-31 02:30", "Europe/Berlin")  # clocks jump from 02:00 to 03:00
    assert "skip" in error.value.message("en")
    assert parse_local_datetime("2030-03-31 03:30", "Europe/Berlin") == utc(2030, 3, 31, 1, 30)


def test_a_time_that_happens_twice_is_the_first_one():
    assert parse_local_datetime("2030-10-27 02:30", "Europe/Berlin") == utc(2030, 10, 27, 0, 30)  # still summer time


@pytest.mark.parametrize("zone", ["Mars/Base", "", "../etc/passwd"])
def test_a_time_zone_the_system_does_not_know_is_explained(zone):
    with pytest.raises(UserFacingError) as error:
        parse_local_datetime("2030-05-17 20:00", zone)
    assert "/setup timezone" in error.value.message("en")
