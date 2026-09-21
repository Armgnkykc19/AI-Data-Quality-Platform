"""The injectable clock, re-exported from where it now lives.

The implementation moved to ``identity.clock`` in Sprint 13 Phase C, because
session expiry needed it and ``identity`` sits below this package in the
dependency graph -- importing upward from there would have inverted the
direction, and copying the formatter would have given the same database two
timestamp formats that could drift apart.

This module stays because every existing caller imports from it:
``ReviewQueueService`` stamps a resolution through it, and
``review_persistence.sqlite.database`` opens a database through it. Keeping the
names here means the move cost no call site anything.

Nothing new should be added to this module. New callers import
``identity.clock`` directly.
"""

from __future__ import annotations

from identity.clock import Clock, system_utc_now, utc_timestamp

__all__ = ["Clock", "system_utc_now", "utc_timestamp"]
