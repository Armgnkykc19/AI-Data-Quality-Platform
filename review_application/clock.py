"""The injectable clock shared by the application and persistence layers.

Sprint 09 precedent (``semantic_review.service``): a zero-argument callable
returning an aware datetime, defaulted at the call site so a test can freeze
time without patching a module.

It lives in the application layer rather than next to the SQLite connection
because a clock is not a storage concern. Keeping it here is also what lets
``ReviewQueueService`` stamp a resolution without importing a storage backend:
one timestamp is written to the case and to its history event, and both come
from the clock the caller injected, never from SQLite's own ``localtime``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def system_utc_now() -> datetime:
    return datetime.now(UTC)


def utc_timestamp(clock: Clock = system_utc_now) -> str:
    """Second-resolution UTC stamp, formatted as Sprint 09 already formats them."""
    return clock().replace(microsecond=0).isoformat().replace("+00:00", "Z")
