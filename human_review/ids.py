from __future__ import annotations

import hashlib

from entity_resolution.models import RecordPair

_REVIEW_CASE_ID_SEPARATOR = "\x1e"


def stable_review_case_id(pair: RecordPair) -> str:
    """Deterministic review case identity from ordered record pair.

    Uses a SHA-256 digest of canonical ordered record IDs so arbitrary record
    identifiers (including those containing ``--``) cannot collide when encoded.
    """
    payload = f"{pair.record_a_id}{_REVIEW_CASE_ID_SEPARATOR}{pair.record_b_id}".encode()
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"RC-{digest}"
