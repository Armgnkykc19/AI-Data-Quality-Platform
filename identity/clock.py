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

import re
from collections.abc import Callable
from datetime import UTC, datetime

from identity.errors import IdentityValidationError

__all__ = [
    "Clock",
    "TIMESTAMP_LENGTH",
    "TIMESTAMP_PATTERN",
    "assert_canonical_timestamp",
    "parse_utc_timestamp",
    "system_utc_now",
    "utc_now",
    "utc_timestamp",
]

Clock = Callable[[], datetime]

# The canonical persisted form: ``YYYY-MM-DDTHH:MM:SSZ``, exactly 20 characters.
#
# These are not decoration. Session expiry is evaluated in SQL by comparing
# these strings -- ``idle_expires_at_utc > ?`` in
# ``review_persistence.sqlite.session_repository`` -- and a CHECK constraint
# compares two of them to each other. String comparison is only equivalent to
# chronological comparison while every stamp is the same width, in the same
# zone, with the same fractional-second policy.
#
# Each property below is load-bearing for that equivalence:
#
# * fixed width -- ``"2026-1-2..."`` would sort before ``"2026-10-..."``;
# * zero-padded, four-digit year -- same reason, at the other end;
# * always UTC and always ``Z`` -- ``+03:00`` sorts after ``Z`` while
#   describing an *earlier* instant, so one offset stamp inverts the ordering;
# * second resolution with no fractional part -- a mixture of ``:00Z`` and
#   ``:00.5Z`` compares the shorter as smaller by prefix, which happens to be
#   right, but ``:00.5Z`` against ``:01Z`` only works because of the digit at
#   position 18. Keeping fractions out entirely removes the reasoning.
#
# A future change to the formatter would break SQL ordering silently -- no
# exception, no failing insert, just expiry comparisons that are wrong for some
# pairs of timestamps. So the shape is asserted rather than assumed.
TIMESTAMP_LENGTH = 20
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def assert_canonical_timestamp(value: str) -> str:
    """Return ``value`` if it is a canonical stored stamp, or raise.

    Cheap enough to run on every format call: one length check and one
    anchored match on a 20-character string.
    """
    if len(value) != TIMESTAMP_LENGTH or not TIMESTAMP_PATTERN.match(value):
        raise IdentityValidationError(
            f"{value!r} is not a canonical UTC timestamp. Stored stamps must be exactly "
            f"{TIMESTAMP_LENGTH} characters of the form YYYY-MM-DDTHH:MM:SSZ, because SQL "
            "predicates and CHECK constraints compare them as strings and rely on "
            "lexicographic order matching chronological order."
        )
    return value


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

    The result is checked rather than trusted. This function is the only
    producer of stored timestamps, so a change to it -- a different resolution,
    a preserved offset, a locale-dependent format -- would propagate to every
    comparison in the database and fail nowhere. The assertion turns that into
    an immediate error at the one place it can still be attributed.
    """
    formatted = moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return assert_canonical_timestamp(formatted)


def parse_utc_timestamp(value: str) -> datetime:
    """Read a stored stamp back as an aware datetime.

    Used where a comparison has to happen in Python rather than in SQL --
    session expiry, principally. ``datetime.fromisoformat`` accepts the ``Z``
    suffix on Python 3.11+, which is the floor this project declares.
    """
    return datetime.fromisoformat(value).astimezone(UTC)
