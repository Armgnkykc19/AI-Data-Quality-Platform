"""Stored timestamps must sort as strings the way the instants sort in time.

Session expiry is decided in SQL. ``session_repository`` compares
``idle_expires_at_utc`` and ``absolute_expires_at_utc`` against a bound
parameter with ``>``, and ``Session.__post_init__`` compares the two columns to
each other -- all as strings. SQLite has no date type, so those comparisons are
byte comparisons, and they are only equivalent to comparing instants while every
stored stamp shares one shape.

That shape was an implicit invariant: ``format_utc_timestamp`` happened to
produce fixed-width, ``Z``-suffixed, second-resolution UTC, and every SQL
predicate silently depended on it. A change to the formatter -- microseconds, a
preserved offset, a non-padded field -- would not raise anything. It would make
some expiry comparisons wrong, which is a session that outlives its bound or
dies early, with no failing test and no error in a log.

So the shape is pinned here, and the property that actually matters is pinned
directly: for any two instants, string order equals chronological order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from identity.clock import (
    TIMESTAMP_LENGTH,
    TIMESTAMP_PATTERN,
    assert_canonical_timestamp,
    format_utc_timestamp,
    parse_utc_timestamp,
    utc_now,
    utc_timestamp,
)
from identity.errors import IdentityValidationError

# --------------------------------------------------------------------------
# The shape
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC),
        datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
        datetime(999, 1, 2, 3, 4, 5, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
    ],
)
def test_every_formatted_stamp_has_the_canonical_shape(moment: datetime) -> None:
    formatted = format_utc_timestamp(moment)

    assert len(formatted) == TIMESTAMP_LENGTH
    assert TIMESTAMP_PATTERN.match(formatted)
    assert formatted.endswith("Z")
    assert "+" not in formatted
    assert "." not in formatted


def test_a_non_utc_instant_is_converted_rather_than_carrying_its_offset() -> None:
    """An offset in a stored stamp would invert ordering against a ``Z`` stamp.

    ``"+03:00"`` sorts after ``"Z"`` while describing an earlier instant, so one
    such row is enough to make an expiry comparison wrong.
    """
    in_istanbul = datetime(2026, 9, 12, 11, 0, 0, tzinfo=timezone(timedelta(hours=3)))

    formatted = format_utc_timestamp(in_istanbul)

    assert formatted == "2026-09-12T08:00:00Z"


def test_sub_second_precision_is_dropped_not_rendered() -> None:
    """A fractional part would make the width variable."""
    moment = datetime(2026, 9, 12, 8, 0, 0, 500_000, tzinfo=UTC)

    assert format_utc_timestamp(moment) == "2026-09-12T08:00:00Z"


def test_the_truncating_clock_and_the_formatter_agree() -> None:
    """``utc_now`` truncates so computed expiries match their stored strings.

    Otherwise ``now + 60m`` formatted, and the format of the same arithmetic on
    an untruncated clock, could differ by a second -- which makes a boundary
    test at exactly 60 minutes depend on the microsecond the clock returned.
    """
    noisy = datetime(2026, 9, 12, 8, 0, 0, 999_999, tzinfo=UTC)

    assert utc_now(lambda: noisy).microsecond == 0
    assert utc_timestamp(lambda: noisy) == format_utc_timestamp(noisy)


# --------------------------------------------------------------------------
# The property the SQL depends on
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("earlier", "later"),
    [
        # Second, minute, hour, day, month and year rollovers.
        (datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC), datetime(2026, 9, 12, 8, 0, 1, tzinfo=UTC)),
        (datetime(2026, 9, 12, 8, 0, 59, tzinfo=UTC), datetime(2026, 9, 12, 8, 1, 0, tzinfo=UTC)),
        (datetime(2026, 9, 12, 8, 59, 59, tzinfo=UTC), datetime(2026, 9, 12, 9, 0, 0, tzinfo=UTC)),
        (datetime(2026, 9, 12, 23, 59, 59, tzinfo=UTC), datetime(2026, 9, 13, 0, 0, 0, tzinfo=UTC)),
        (datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC), datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)),
        (datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC), datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)),
        # Single-digit against double-digit month and day, which is where an
        # unpadded format would break the ordering.
        (datetime(2026, 9, 9, 9, 9, 9, tzinfo=UTC), datetime(2026, 10, 10, 10, 10, 10, tzinfo=UTC)),
        (datetime(2026, 1, 9, 0, 0, 0, tzinfo=UTC), datetime(2026, 1, 10, 0, 0, 0, tzinfo=UTC)),
        # A leap day, and the full session window this project actually uses.
        (datetime(2028, 2, 28, 12, 0, 0, tzinfo=UTC), datetime(2028, 2, 29, 12, 0, 0, tzinfo=UTC)),
        (
            datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC) + timedelta(hours=8),
        ),
        (
            datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC) + timedelta(minutes=60),
        ),
    ],
)
def test_string_order_matches_chronological_order(earlier: datetime, later: datetime) -> None:
    assert earlier < later, "fixture must be chronological"

    assert format_utc_timestamp(earlier) < format_utc_timestamp(later)


def test_equal_instants_format_equal() -> None:
    moment = datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC)

    assert format_utc_timestamp(moment) == format_utc_timestamp(moment)


def test_a_long_ascending_run_sorts_identically_as_strings_and_as_instants() -> None:
    """The ordering property over a whole sequence, not just adjacent pairs."""
    start = datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC)
    instants = [start + timedelta(seconds=17 * step) for step in range(500)]

    formatted = [format_utc_timestamp(moment) for moment in instants]

    assert formatted == sorted(formatted)
    assert [parse_utc_timestamp(value) for value in formatted] == instants


def test_a_round_trip_through_the_parser_preserves_the_instant() -> None:
    moment = datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC)

    assert parse_utc_timestamp(format_utc_timestamp(moment)) == moment


# --------------------------------------------------------------------------
# The guard itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-12T08:00:00+00:00",  # offset rather than Z
        "2026-09-12T08:00:00.500Z",  # fractional seconds
        "2026-9-12T08:00:00Z",  # unpadded month
        "2026-09-12 08:00:00Z",  # space instead of T
        "2026-09-12T08:00Z",  # no seconds
        "2026-09-12T08:00:00",  # no zone
        "",
        "not a timestamp",
    ],
)
def test_a_non_canonical_stamp_is_refused(value: str) -> None:
    """Refused rather than coerced. A coerced stamp is a silently wrong one."""
    with pytest.raises(IdentityValidationError):
        assert_canonical_timestamp(value)


def test_the_canonical_stamp_is_returned_unchanged() -> None:
    assert assert_canonical_timestamp("2026-09-12T08:00:00Z") == "2026-09-12T08:00:00Z"
