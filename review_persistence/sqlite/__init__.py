"""SQLite implementation of the review queue persistence contract.

Phase B provided the connection foundation, the row/domain mapper, and
insert-if-absent case storage; Phase C added the workflow authorization context
and complete bundle reconstruction; Phase D added atomic, versioned resolution
and the append-only event history; Phase E added immutable advisory storage for
Sprint 09 semantic suggestions, completing the repository Protocol.
"""

from review_persistence.sqlite.context_mapper import (
    canonical_json,
    entity_records_from_payload,
    entity_records_to_payload,
    normalized_context_fingerprint,
)
from review_persistence.sqlite.database import (
    Clock,
    ReviewDatabase,
    open_review_database,
    utc_timestamp,
)
from review_persistence.sqlite.event_mapper import (
    REVIEW_EVENT_INSERT_COLUMNS,
    REVIEW_EVENT_SELECT_COLUMNS,
    event_to_row,
    row_to_review_event,
)
from review_persistence.sqlite.mapper import (
    REVIEW_CASE_COLUMNS,
    case_payload_json,
    case_to_row,
    review_case_from_payload,
    row_to_persisted_case,
)
from review_persistence.sqlite.review_repository import (
    SqliteReviewCaseRepository,
    StoredWorkflowContext,
)
from review_persistence.sqlite.semantic_mapper import (
    SEMANTIC_SUGGESTION_COLUMNS,
    assert_is_sprint_09_suggestion,
    canonical_suggestion_json,
    row_to_semantic_suggestion,
    suggestion_payload,
    suggestion_to_row,
)

__all__ = [
    "REVIEW_CASE_COLUMNS",
    "REVIEW_EVENT_INSERT_COLUMNS",
    "REVIEW_EVENT_SELECT_COLUMNS",
    "SEMANTIC_SUGGESTION_COLUMNS",
    "Clock",
    "ReviewDatabase",
    "SqliteReviewCaseRepository",
    "StoredWorkflowContext",
    "assert_is_sprint_09_suggestion",
    "canonical_json",
    "canonical_suggestion_json",
    "case_payload_json",
    "case_to_row",
    "entity_records_from_payload",
    "entity_records_to_payload",
    "event_to_row",
    "normalized_context_fingerprint",
    "open_review_database",
    "review_case_from_payload",
    "row_to_persisted_case",
    "row_to_review_event",
    "row_to_semantic_suggestion",
    "suggestion_payload",
    "suggestion_to_row",
    "utc_timestamp",
]
