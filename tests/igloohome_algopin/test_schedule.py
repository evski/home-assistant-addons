"""Home-timezone windows converted to UTC."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.igloohome_algopin.schedule import (
    WindowError,
    clean_access_name,
    clean_pin,
    clean_variance,
    validate_window,
)

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
KOLKATA = ZoneInfo("Asia/Kolkata")


def test_new_york_hourly_converts_to_utc():
    window = validate_window(
        "hourly",
        datetime(2026, 1, 15, 14, 0),
        datetime(2026, 1, 15, 18, 0),
        NEW_YORK,
        datetime(2026, 1, 15, 19, 0, tzinfo=UTC),
    )

    assert window.start_api == "2026-01-15T19:00:00+00:00"
    assert window.end_api == "2026-01-15T23:00:00+00:00"
    assert window.hours == 4


def test_london_daily_july_is_29_utc_days():
    window = validate_window(
        "daily",
        datetime(2026, 7, 1, 15, 0),
        datetime(2026, 7, 30, 15, 0),
        LONDON,
        datetime(2026, 6, 1, tzinfo=UTC),
    )

    assert window.start_api == "2026-07-01T14:00:00+00:00"
    assert window.end_api == "2026-07-30T14:00:00+00:00"
    assert window.days == 29


def test_london_daily_across_dst_is_rejected():
    with pytest.raises(WindowError, match="same UTC hour"):
        validate_window(
            "daily",
            datetime(2026, 3, 28, 12, 0),
            datetime(2026, 4, 27, 12, 0),
            LONDON,
            datetime(2026, 3, 1, tzinfo=UTC),
        )


def test_kolkata_local_hour_that_is_not_a_utc_hour_is_rejected():
    with pytest.raises(WindowError, match="hour boundary"):
        validate_window(
            "hourly",
            datetime(2026, 1, 15, 14, 0),
            datetime(2026, 1, 15, 18, 0),
            KOLKATA,
            datetime(2026, 1, 15, 8, 0, tzinfo=UTC),
        )


def test_kolkata_half_hour_local_can_land_on_a_utc_hour():
    window = validate_window(
        "hourly",
        datetime(2026, 1, 15, 14, 30),
        datetime(2026, 1, 15, 18, 30),
        KOLKATA,
        datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
    )

    assert window.start_api == "2026-01-15T09:00:00+00:00"
    assert window.end_api == "2026-01-15T13:00:00+00:00"
    assert window.hours == 4


def test_naive_datetime_uses_home_zone_and_aware_utc_keeps_the_instant():
    naive = validate_window(
        "hourly",
        datetime(2026, 1, 15, 14, 0),
        datetime(2026, 1, 15, 16, 0),
        NEW_YORK,
        datetime(2026, 1, 15, 19, 0, tzinfo=UTC),
    )
    aware = validate_window(
        "hourly",
        datetime(2026, 1, 15, 19, 0, tzinfo=UTC),
        datetime(2026, 1, 15, 21, 0, tzinfo=UTC),
        NEW_YORK,
        datetime(2026, 1, 15, 19, 0, tzinfo=UTC),
    )

    assert naive.start_api == aware.start_api == "2026-01-15T19:00:00+00:00"
    assert naive.hours == aware.hours == 2


def test_iso_string_is_accepted():
    window = validate_window(
        "hourly",
        "2026-01-15T14:00:00",
        "2026-01-15T15:00:00",
        NEW_YORK,
        datetime(2026, 1, 15, 19, 0, tzinfo=UTC),
    )

    assert window.hours == 1
    assert window.start_api == "2026-01-15T19:00:00+00:00"


@pytest.mark.parametrize(
    ("hours", "ok"),
    [(1, True), (672, True), (673, False)],
)
def test_hourly_duration_limits(hours, ok):
    start = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    call = lambda: validate_window("hourly", start, start + timedelta(hours=hours), UTC, start)
    if ok:
        assert call().hours == hours
    else:
        with pytest.raises(WindowError, match="1 to 672"):
            call()


def test_hourly_start_may_be_one_hour_in_the_past_but_not_more():
    now = datetime(2026, 1, 15, 16, 0, tzinfo=UTC)
    allowed = validate_window(
        "hourly",
        datetime(2026, 1, 15, 10, 0),
        datetime(2026, 1, 15, 12, 0),
        NEW_YORK,
        now,
    )
    assert allowed.start_api == "2026-01-15T15:00:00+00:00"

    with pytest.raises(WindowError, match="1 hour"):
        validate_window(
            "hourly",
            datetime(2026, 1, 15, 9, 0),
            datetime(2026, 1, 15, 12, 0),
            NEW_YORK,
            now,
        )


@pytest.mark.parametrize(
    ("days", "ok"),
    [(28, False), (29, True), (367, True), (368, False)],
)
def test_daily_duration_limits(days, ok):
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    call = lambda: validate_window("daily", start, start + timedelta(days=days), UTC, start)
    if ok:
        assert call().days == days
    else:
        with pytest.raises(WindowError, match="29 to 367"):
            call()


def test_leftover_minutes_and_reversed_window():
    with pytest.raises(WindowError, match="hour boundary"):
        validate_window(
            "hourly",
            datetime(2026, 1, 15, 14, 15),
            datetime(2026, 1, 15, 16, 0),
            NEW_YORK,
            datetime(2026, 1, 15, 19, 0, tzinfo=UTC),
        )
    with pytest.raises(WindowError, match="after start"):
        validate_window(
            "hourly",
            datetime(2026, 1, 15, 16, 0),
            datetime(2026, 1, 15, 14, 0),
            NEW_YORK,
            datetime(2026, 1, 15, 19, 0, tzinfo=UTC),
        )


def test_access_name_variance_and_pin():
    assert clean_access_name("  Guest   stay ") == "Guest stay"
    with pytest.raises(WindowError):
        clean_access_name("   ")
    with pytest.raises(WindowError):
        clean_access_name("x" * 201)
    assert clean_variance("2") == 2
    with pytest.raises(WindowError):
        clean_variance(4)
    assert clean_pin("1357902") == "1357902"
    with pytest.raises(WindowError):
        clean_pin("12")
    with pytest.raises(WindowError):
        clean_pin("abcd")
