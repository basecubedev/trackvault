"""The system clock, behind the application's ``Clock`` port.

Use cases receive a clock instead of reading the wall clock themselves, so tests
stay deterministic and a stored instant is always explainable.
"""

from datetime import UTC, datetime


class SystemClock:
    """Reads the real current time, always as an unambiguous UTC instant."""

    def now(self) -> datetime:
        """Return the current instant in UTC."""
        return datetime.now(UTC)
