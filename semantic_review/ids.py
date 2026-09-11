from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stable_request_id(fingerprint: str) -> str:
    return f"SR-{fingerprint[:16]}"


def stable_suggestion_id(
    *,
    request_id: str,
    suggestion: str,
    attempt_count: int,
    fingerprint: str,
) -> str:
    digest = canonical_json_digest(
        {
            "request_id": request_id,
            "suggestion": suggestion,
            "attempt_count": attempt_count,
            "fingerprint": fingerprint,
        }
    )
    return f"LS-{digest[:16]}"


def estimate_token_count(text: str) -> int:
    """Conservative character/4 heuristic. Not a vendor tokenizer; local cap only."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
