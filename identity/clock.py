"""The injectable clock, and the one UTC timestamp format this database uses.

Sprint 09 precedent (``semantic_review.service``): a zero-argument callable
returning an aware datetime, defaulted at the call site so a test can freeze
time without patching a module.

This module is a move, not a new abstraction. It lived in
``review_application.clock`` until session expiry needed it, and
``review_application`` sits *above* ``identity`` in the dependency graph -- so
importing it from here would invert the direction. It moved down rather than
being copied, and ``review_application.clock`` re-exports every name it used to
define, so no existing import changed.

Copying would have been the real mistake. Review timestamps and session
timestamps land in the same SQLite file and are compared against each other by
CHECK constraints and by SQL predicates that rely on fixed-width lexicographic
ordering. Two formatters that drifted by one character -- a microsecond, a
``+00:00`` instead of a ``Z`` -- would silently break those comparisons rather
than raise anything.

A clock is not an identity concept. It lives here because ``identity`` is the
lowest layer that needs one, which is the same reason the repository protocol
for review cases lives in the application layer rather than in persistence.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

__all__ = [
    "Clock",
    "parse_utc_timestamp",
    "system_utc_now",
    "utc_now",
    "utc_timestamp",
]

Clock = Callable[[], datetime]


def system_utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now(clock: Clock = system_utc_now) -> datetime:
    """The clock's instant, truncated to the resolution this project stores.

    Truncating here rather than only when formatting is what keeps a computed
    expiry and its stored string describing the same moment: ``now + 60m``
    formatted to second resolution must equal the second-resolution stamp of
    the same arithmetic, or a boundary test at exactly 60 minutes becomes a
    coin flip on the microsecond the clock happened to return.
    """
    return clock().astimezone(UTC).replace(microsecond=0)


def utc_timestamp(clock: Clock = system_utc_now) -> str:
    """Second-resolution UTC stamp, formatted as Sprint 09 already formats them."""
    return format_utc_timestamp(utc_now(clock))


def format_utc_timestamp(moment: datetime) -> str:
    """The one way an instant becomes a stored string.

    Fixed width and always ``Z``-suffixed, which is what makes the stored
    strings sort lexicographically in the same order as the instants they
    describe. Several SQL predicates and CHECK constraints depend on that.
    """
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc_timestamp(value: str) -> datetime:
    """Read a stored stamp back as an aware datetime.

    Used where a comparison has to happen in Python rather than in SQL --
    session expiry, principally. ``datetime.fromisoformat`` accepts the ``Z``
    suffix on Python 3.11+, which is the floor this project declares.
    """
    return datetime.fromisoformat(value).astimezone(UTC)
