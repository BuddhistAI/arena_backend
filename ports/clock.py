"""Clock port: what time it is now, in naive UTC like the arena's timestamp
columns.

A clock is any callable returning that time. Routes take it with
``Depends(get_clock)`` and workers take it as an argument; tests use
``FakeClock`` for both, so they decide what "now" is (and which UTC day).
"""

import datetime
from typing import Callable

Clock = Callable[[], datetime.datetime]


def utc_now() -> datetime.datetime:
    """Now in naive UTC, like the arena's timestamp columns."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def get_clock() -> Clock:
    """FastAPI dependency for the clock port: the system clock."""
    return utc_now


def _naive_utc(moment: datetime.datetime) -> datetime.datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)


class FakeClock:
    """Fake for tests: stands still at the time it was last set to."""

    def __init__(self, now: datetime.datetime = datetime.datetime(2026, 11, 26, 12, 0)) -> None:
        self.now = _naive_utc(now)

    def __call__(self) -> datetime.datetime:
        return self.now

    def set(self, now: datetime.datetime) -> None:
        """Make ``now`` the current time. An aware time is converted to UTC.

        Args:
            now: The new current time; naive times are taken as UTC.
        """
        self.now = _naive_utc(now)

    def advance(self, delta: datetime.timedelta) -> None:
        """Move the current time forward by ``delta``.

        Args:
            delta: How far to move the clock.
        """
        self.now += delta
