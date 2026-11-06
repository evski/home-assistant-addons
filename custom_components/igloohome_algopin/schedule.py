"""Home-timezone windows converted to the UTC hour format the home API requires.

Inputs are interpreted in the Home Assistant timezone. Naive values use that
zone. Aware values keep their absolute instant. The result is formatted as
``YYYY-MM-DDTHH:00:00+00:00``. The home API allows only hour resolution, so a
converted instant whose UTC minute, second, or microsecond is not zero is
rejected rather than rounded.

Hourly duration is 1 to 672 hours. The hourly reference allows a start up to
one hour in the past. Daily duration is 29 to 367 whole days, and the UTC
hour of the start and the end must match (the daily reference requires the
hour on startDate and endDate to match). A local window that crosses a DST
change, or a timezone whose offset is not a whole hour, can fail those UTC
checks. That rejection is deliberate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo

from .const import (
    DAILY_MAX_DAYS,
    DAILY_MIN_DAYS,
    HOURLY_MAX_HOURS,
    HOURLY_MIN_HOURS,
    HOURLY_PAST_GRACE,
)


class WindowError(ValueError):
    """The caller sent a date, duration, name, variance, or PIN this API will not accept."""


@dataclass(frozen=True)
class UtcWindow:
    """An hour-aligned interval already converted to UTC."""

    start: datetime
    end: datetime
    start_api: str
    end_api: str
    hours: int

    @property
    def days(self) -> int:
        return self.hours // 24


def to_utc(value: datetime | str, home_tz: tzinfo) -> datetime:
    """Interpret ``value`` in ``home_tz`` and return the same instant in UTC."""

    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise WindowError("start and end are required")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as err:
            raise WindowError("start and end must be ISO datetimes") from err
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise WindowError("start and end must be datetimes")

    if parsed.tzinfo is None:
        local = parsed.replace(tzinfo=home_tz)
    else:
        local = parsed.astimezone(home_tz)
    return local.astimezone(timezone.utc)


def format_utc_hour(moment: datetime) -> str:
    """Format an aware UTC hour as the home API date string."""

    utc = moment.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:00:00+00:00")


def validate_window(
    kind: str,
    start: datetime | str,
    end: datetime | str,
    home_tz: tzinfo,
    now: datetime,
) -> UtcWindow:
    """Validate a home-local window and convert it to UTC API timestamps."""

    if kind not in ("hourly", "daily"):
        raise WindowError("PIN type must be hourly or daily")
    if now.tzinfo is None:
        raise WindowError("current time must be timezone-aware")

    start_utc = to_utc(start, home_tz)
    end_utc = to_utc(end, home_tz)
    for label, moment in (("start", start_utc), ("end", end_utc)):
        if moment.minute or moment.second or moment.microsecond:
            shown = moment.strftime("%Y-%m-%d %H:%M:%S UTC")
            raise WindowError(
                f"{label} converts to {shown}; the API only accepts an hour boundary in UTC"
            )

    if end_utc <= start_utc:
        raise WindowError("end must be after start")

    seconds = (end_utc - start_utc).total_seconds()
    if seconds % 3600 != 0:
        raise WindowError("duration must be a whole number of hours in UTC")
    hours = int(seconds // 3600)

    if kind == "hourly":
        if hours < HOURLY_MIN_HOURS or hours > HOURLY_MAX_HOURS:
            raise WindowError("hourly AlgoPIN duration must be from 1 to 672 hours")
        earliest = now.astimezone(timezone.utc) - HOURLY_PAST_GRACE
        if start_utc < earliest:
            raise WindowError("hourly start may be at most 1 hour in the past")
    else:
        if start_utc.hour != end_utc.hour or hours % 24 != 0:
            raise WindowError(
                "daily start and end must use the same UTC hour and a whole number of days"
            )
        days = hours // 24
        if days < DAILY_MIN_DAYS or days > DAILY_MAX_DAYS:
            raise WindowError("daily AlgoPIN duration must be from 29 to 367 days")

    return UtcWindow(
        start=start_utc,
        end=end_utc,
        start_api=format_utc_hour(start_utc),
        end_api=format_utc_hour(end_utc),
        hours=hours,
    )


def clean_access_name(value: str) -> str:
    """Require a non-empty accessName. The AlgoPIN reference does not publish a max length."""

    name = " ".join(str(value).split())
    if not name:
        raise WindowError("access name is required")
    if len(name) > 200:
        raise WindowError(
            "access name must be 200 characters or fewer "
            "(local limit; the AlgoPIN reference does not publish a maximum)"
        )
    return name


def clean_variance(value: object) -> int:
    """Hourly and daily variances are 1, 2, or 3."""

    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as err:
        raise WindowError("variance must be 1, 2, or 3") from err
    if number not in (1, 2, 3):
        raise WindowError("variance must be 1, 2, or 3")
    return number


_PIN = re.compile(r"^\d{4,9}$")


def clean_pin(value: str) -> str:
    """Delete-PIN bridge jobs take the PIN as 4 to 9 digits."""

    pin = str(value).strip()
    if not _PIN.fullmatch(pin):
        raise WindowError("the revoke PIN must be 4 to 9 digits")
    return pin
