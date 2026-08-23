from __future__ import annotations

from entity_resolution.conflicts import has_severe_conflict
from entity_resolution.decisions import (
    decide_pair_match,
    has_strong_identity_evidence,
    is_weak_only_evidence,
)
from entity_resolution.engine import resolve_entities
from entity_resolution.models import (
    EntityRecord,
    MatchDecisionType,
    ResolutionResult,
)

__all__ = [
    "EntityRecord",
    "MatchDecisionType",
    "ResolutionResult",
    "decide_pair_match",
    "has_severe_conflict",
    "has_strong_identity_evidence",
    "is_weak_only_evidence",
    "resolve_entities",
]
