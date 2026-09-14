"""SQLite schema contract for the persistent review queue.

Phase A defines the contract only: version constants, DDL text, and the token
sets the DDL constrains. Nothing here opens a database file.

There is no migration framework by design. Sprint 10 mirrors the mechanism the
project already uses for versioned artifacts (``human_review.reporting``): a
declared version plus a supported set, checked on open and failing closed on an
unrecognized value.
"""

from __future__ import annotations

from human_review.models import ReviewStatus
from review_application.errors import ReviewSchemaVersionError
from review_application.models import ReviewEventType
from semantic_review.models import FORBIDDEN_HUMAN_DECISIONS, SemanticSuggestionType

DATABASE_SCHEMA_VERSION = "1.0.0"
SUPPORTED_DATABASE_SCHEMA_VERSIONS = frozenset({DATABASE_SCHEMA_VERSION})

SCHEMA_META_TABLE = "schema_meta"
REVIEW_WORKFLOW_CONTEXT_TABLE = "review_workflow_context"
REVIEW_CASES_TABLE = "review_cases"
REVIEW_CASE_EVENTS_TABLE = "review_case_events"
SEMANTIC_SUGGESTIONS_TABLE = "semantic_suggestions"

ALL_TABLES: tuple[str, ...] = (
    SCHEMA_META_TABLE,
    REVIEW_WORKFLOW_CONTEXT_TABLE,
    REVIEW_CASES_TABLE,
    REVIEW_CASE_EVENTS_TABLE,
    SEMANTIC_SUGGESTIONS_TABLE,
)

# One SQLite file is one review queue, so it carries exactly one authorization
# context. There is no tenant, dataset, or batch identifier in this project that
# would make a second context meaningful, and inventing one would let a caller
# authorize a MATCH against the wrong record set.
WORKFLOW_CONTEXT_ID = 1

# Token sets are derived from the live enums rather than retyped, so a CHECK
# constraint can never drift away from the code that produces the value.
ALLOWED_REVIEW_STATUS_VALUES = frozenset(status.value for status in ReviewStatus)
ALLOWED_REVIEW_EVENT_TYPES = frozenset(event.value for event in ReviewEventType)
ALLOWED_SEMANTIC_SUGGESTION_VALUES = frozenset(item.value for item in SemanticSuggestionType)

# Authoritative Human Review tokens. The semantic_suggestions CHECK excludes
# them by construction: an advisory row can never spell a human decision.
FORBIDDEN_SEMANTIC_SUGGESTION_VALUES = frozenset(FORBIDDEN_HUMAN_DECISIONS)

RESOLUTION_EVENT_TYPES = frozenset({"MATCH", "NO_MATCH", "DEFERRED"})

# SQLite leaves foreign keys off by default, which would make every REFERENCES
# clause below inert. The connection factory must issue these per connection.
REQUIRED_PRAGMAS: tuple[str, ...] = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
)


def _sql_token_list(values: frozenset[str]) -> str:
    """Render a sorted, quoted SQL IN-list so DDL text stays deterministic."""
    return ", ".join(f"'{value}'" for value in sorted(values))


def assert_supported_schema_version(version: str) -> None:
    """Fail closed on a database this build does not understand."""
    if version not in SUPPORTED_DATABASE_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_DATABASE_SCHEMA_VERSIONS))
        raise ReviewSchemaVersionError(
            f"Unsupported review database schema version '{version}'. Supported: {supported}."
        )


CREATE_SCHEMA_META = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_META_TABLE} (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
)
""".strip()


# The material Sprint 08 MATCH authorization needs beyond the cases themselves.
# assert_human_match_authorization_boundary reads entity records (for severe
# identity conflicts) and the AUTO_MATCH edges (to project the connected
# component transitively); without both, a MATCH that should fail closed would
# be evaluated against a partial graph and quietly pass.
#
# Both columns hold the reduced Sprint 08 report representation -- what
# entity_records_to_dict() and resolution_snapshot() already produce -- so this
# is not a second Entity Resolution model. The full ResolutionResult is
# deliberately not stored: Sprint 08 itself persists only the reduction.
#
# entity_resolution_config_path is provenance, not an authorization input: the
# config is loaded from disk at check time. It is kept because the Phase A
# WorkflowBundle and repository Protocol both carry it, and because authorizing
# against a different ER config than the queue was generated with would be
# wrong in a way nothing else would catch.
CREATE_REVIEW_WORKFLOW_CONTEXT = f"""
CREATE TABLE IF NOT EXISTS {REVIEW_WORKFLOW_CONTEXT_TABLE} (
    context_id INTEGER PRIMARY KEY CHECK (context_id = {WORKFLOW_CONTEXT_ID}),
    entity_records_json TEXT NOT NULL,
    resolution_snapshot_json TEXT NOT NULL,
    entity_resolution_config_path TEXT NULL,
    schema_version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
)
""".strip()


# case_payload_json holds the unmodified ReviewCase.to_dict() payload. Evidence
# tuples are intentionally not normalized into tables: they are never queried,
# and a second serialization would compete with the Sprint 08 report contract.
CREATE_REVIEW_CASES = f"""
CREATE TABLE IF NOT EXISTS {REVIEW_CASES_TABLE} (
    review_case_id TEXT PRIMARY KEY,
    record_a_id TEXT NOT NULL,
    record_b_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ({_sql_token_list(ALLOWED_REVIEW_STATUS_VALUES)})),
    machine_decision TEXT NOT NULL,
    machine_score REAL NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    case_payload_json TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE (record_a_id, record_b_id)
)
""".strip()


# The CHECK below is the schema-level guarantee behind the locked decision that
# persistence never synthesizes a decision: a resolution row is unrepresentable
# without the domain-generated audit entry, and a suggestion row is
# unrepresentable with one.
CREATE_REVIEW_CASE_EVENTS = f"""
CREATE TABLE IF NOT EXISTS {REVIEW_CASE_EVENTS_TABLE} (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_case_id TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ({_sql_token_list(ALLOWED_REVIEW_EVENT_TYPES)})),
    resolution_sequence INTEGER NULL
        CHECK (resolution_sequence IS NULL OR resolution_sequence >= 1),
    reviewer_id TEXT NULL,
    audit_entry_json TEXT NULL,
    suggestion_id TEXT NULL,
    occurred_at_utc TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY (review_case_id)
        REFERENCES {REVIEW_CASES_TABLE} (review_case_id) ON DELETE RESTRICT,
    CHECK (
        (
            event_type IN ({_sql_token_list(RESOLUTION_EVENT_TYPES)})
            AND audit_entry_json IS NOT NULL
            AND resolution_sequence IS NOT NULL
            AND suggestion_id IS NULL
        )
        OR (
            event_type = '{ReviewEventType.SEMANTIC_SUGGESTION_RECORDED.value}'
            AND suggestion_id IS NOT NULL
            AND audit_entry_json IS NULL
            AND resolution_sequence IS NULL
            AND reviewer_id IS NULL
        )
        OR (
            event_type = '{ReviewEventType.CASE_CREATED.value}'
            AND audit_entry_json IS NULL
            AND resolution_sequence IS NULL
            AND suggestion_id IS NULL
            AND reviewer_id IS NULL
        )
    )
)
""".strip()


# No version column: a suggestion is an immutable observation, never updated.
# No column here is read by any resolution path.
CREATE_SEMANTIC_SUGGESTIONS = f"""
CREATE TABLE IF NOT EXISTS {SEMANTIC_SUGGESTIONS_TABLE} (
    suggestion_id TEXT PRIMARY KEY,
    review_case_id TEXT NOT NULL,
    record_a_id TEXT NOT NULL,
    record_b_id TEXT NOT NULL,
    suggestion TEXT NOT NULL
        CHECK (suggestion IN ({_sql_token_list(ALLOWED_SEMANTIC_SUGGESTION_VALUES)})),
    failure_code TEXT NULL,
    live INTEGER NOT NULL CHECK (live IN (0, 1)),
    provider TEXT NOT NULL,
    requested_model TEXT,
    suggestion_payload_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY (review_case_id)
        REFERENCES {REVIEW_CASES_TABLE} (review_case_id) ON DELETE RESTRICT
)
""".strip()


CREATE_TABLE_STATEMENTS: tuple[str, ...] = (
    CREATE_SCHEMA_META,
    CREATE_REVIEW_WORKFLOW_CONTEXT,
    CREATE_REVIEW_CASES,
    CREATE_REVIEW_CASE_EVENTS,
    CREATE_SEMANTIC_SUGGESTIONS,
)


# Partial unique index: two resolutions of the same case can never claim the
# same ordinal, while the many NULL sequences on lifecycle rows stay unaffected.
UNIQUE_RESOLUTION_SEQUENCE_INDEX = f"""
CREATE UNIQUE INDEX IF NOT EXISTS ux_review_case_events_resolution_sequence
    ON {REVIEW_CASE_EVENTS_TABLE} (review_case_id, resolution_sequence)
    WHERE resolution_sequence IS NOT NULL
""".strip()


CREATE_INDEX_STATEMENTS: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS ix_review_cases_status ON {REVIEW_CASES_TABLE} (status)",
    f"CREATE INDEX IF NOT EXISTS ix_review_cases_record_a_id ON {REVIEW_CASES_TABLE} (record_a_id)",
    f"CREATE INDEX IF NOT EXISTS ix_review_cases_record_b_id ON {REVIEW_CASES_TABLE} (record_b_id)",
    f"CREATE INDEX IF NOT EXISTS ix_review_case_events_case "
    f"ON {REVIEW_CASE_EVENTS_TABLE} (review_case_id, event_id)",
    UNIQUE_RESOLUTION_SEQUENCE_INDEX,
    f"CREATE INDEX IF NOT EXISTS ix_semantic_suggestions_case "
    f"ON {SEMANTIC_SUGGESTIONS_TABLE} (review_case_id)",
)


SCHEMA_STATEMENTS: tuple[str, ...] = CREATE_TABLE_STATEMENTS + CREATE_INDEX_STATEMENTS
