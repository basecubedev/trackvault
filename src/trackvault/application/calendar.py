"""The supported calendar range, and how a period becomes an instant window.

One authority, for one reason: a route that validates a year the use case behind
it cannot then process is a crash with a green validator in front of it. That is
exactly what two copies of a `MAX_YEAR` constant produced -- the routes accepted
9999 and the window arithmetic computed `year + 1`, which is not a year Python
has.

```
period (local year, optional month)
        │  the configured aggregation zone
        ▼
half-open UTC instant window
```

The window is half open, so consecutive periods tile without a track landing in
two of them, and it is computed with `zoneinfo` rather than with a fixed offset:
an offset is not a constant, and doing this arithmetic with one is how a
daylight-saving transition moves a track into the wrong month.

The upper bound is `None` for the last supported year rather than the first
instant of the next one. There is no next one -- `datetime` stops at 9999 -- and
an open upper bound is the honest expression of "everything from here on"
anyway.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

MIN_QUERY_YEAR = 1970
"""The earliest year a query may name.

It rejects a typo rather than making a claim about history: an archive of
satellite-navigation recordings cannot predate satellite navigation.
"""

MAX_QUERY_YEAR = 9999
"""The latest year a query may name.

The last year `datetime` can express. Supporting it is a decision, not an
accident: the alternative -- stopping at 9998 to keep `year + 1` working -- would
hide an arithmetic limitation inside a business constant.
"""

MONTHS_IN_YEAR = 12


def period_window(year: int, month: int | None, zone: ZoneInfo) -> tuple[datetime, datetime | None]:
    """Return the half-open UTC window one local period covers.

    Args:
        year: The year, read in ``zone``.
        month: The month of that year, or ``None`` for the whole year.
        zone: The aggregation timezone the boundaries are drawn in.

    Returns:
        The first instant of the period and the first instant after it. The
        second is ``None`` when the period runs to the end of the supported
        calendar, which is an open upper bound rather than an impossible date.

    Raises:
        ValueError: If the year is outside the supported range or the month is
            outside the calendar. Both are caller mistakes, and answering them
            with an empty period would hide the mistake instead.
    """
    if not MIN_QUERY_YEAR <= year <= MAX_QUERY_YEAR:
        raise ValueError("a year must lie within the supported calendar range")
    if month is not None and not 1 <= month <= MONTHS_IN_YEAR:
        raise ValueError("a month must be within the calendar")

    start = datetime(year, month or 1, 1, tzinfo=zone)
    end = _first_instant_after(year, month, zone)
    return start.astimezone(UTC), None if end is None else end.astimezone(UTC)


def _first_instant_after(year: int, month: int | None, zone: ZoneInfo) -> datetime | None:
    """Return the local instant one period ends at, or ``None`` past the calendar."""
    if month is None or month == MONTHS_IN_YEAR:
        if year == MAX_QUERY_YEAR:
            return None
        return datetime(year + 1, 1, 1, tzinfo=zone)
    return datetime(year, month + 1, 1, tzinfo=zone)


__all__ = [
    "MAX_QUERY_YEAR",
    "MIN_QUERY_YEAR",
    "MONTHS_IN_YEAR",
    "period_window",
]
