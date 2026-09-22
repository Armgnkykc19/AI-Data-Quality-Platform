"""SQLite schema contract for the persistent review queue.

This module defines the contract only: version constants, DDL text, and the
token sets the DDL constrains. Nothing here opens a database file.

There is no migration framework by design. The project mirrors the mechanism it
already uses for versioned artifacts (``human_review.reporting``): a declared
version plus a supported set, checked on open and failing closed on an
unrecognized value.

Schema 2.0.0 -- tenant ownership
--------------------------------

Version 1.0.0 assumed one SQLite file was one review queue, and encoded that
with ``CHECK (context_id = 1)`` on the workflow context plus a globally unique
``review_cases.review_case_id``. Both assumptions are gone.

Review data is now owned by a ``review_queues`` row, which is owned by an
``organizations`` row (see ``review_persistence.identity_schema``). Ownership
flows in exactly one direction::

    event / suggestion -> review case -> review queue -> organization

and no child table repeats ``organization_id``, so no two rows can disagree
about which tenant a case belongs to.

The load-bearing change is that ``review_case_id`` is **not globally unique**.
``human_review.ids.stable_review_case_id`` derives it from a SHA-256 digest of
the reviewed record pair, and record identifiers come from customer data --
``entity_resolution.records`` falls back to ``row-{n}`` when a source carries
no identifier column. Two organizations uploading two unrelated files will
therefore produce the same ``RC-...`` routinely, not exceptionally. A composite
``PRIMARY KEY (review_queue_id, review_case_id)`` is what makes both storable;
a globally unique column would have made the second tenant's registration fail
or, worse, silently attach to the first tenant's case.

Child tables use **composite** foreign keys onto that pair, so an event or a
suggestion cannot reference a case in another queue even if application code
asked it to. Tenant containment is a database guarantee here, not a predicate a
query might forget.
"""

from __future__ import annotations

from human_review.models import ReviewStatus
from review_application.errors import (
    ReviewSchemaMigrationRequiredError,
    ReviewSchemaVersionError,
)
from review_application.models import ReviewEventType
from review_persistence.identity_schema import (
    IDENTITY_INDEX_STATEMENTS,
    IDENTITY_TABLE_STATEMENTS,
    IDENTITY_TABLES,
    REVIEW_QUEUES_TABLE,
)
from semantic_review.models import FORBIDDEN_HUMAN_DECISIONS, SemanticSuggestionType

# Still 2.0.0 after Sprint 14 Phase A tightened the resolution-sequence index.
# 2.0.0 shipped with Sprint 13 tenant ownership (Phase B, then PR #15). That is
# a repository release, not an external production database, and those are
# different facts. Bumping to 2.1.0 would invent a migration between two
# in-repo shapes of the same tenant schema, and this project still has no
# in-place migrator. The chosen contract is therefore Option B: keep the
# semantic version and refuse to open a 2.0.0 file whose unique index does not
# enforce the queue-global sequence invariant. ``initialize`` asserts the
# live index SQL, not only the version string. Nothing is rewritten, sequences
# are never renumbered, and ``verify-queue`` remains a read-only detector --
# it is not the startup guarantee. See ``assert_resolution_sequence_index``.
DATABASE_SCHEMA_VERSION = "2.0.0"
SUPPORTED_DATABASE_SCHEMA_VERSIONS = frozenset({DATABASE_SCHEMA_VERSION})

# Versions this build recognizes but cannot serve. Separated from "unknown" so
# an operator holding one is told what is wrong with it rather than that their
# database is unrecognizable. Neither is ever upgraded in place by an
# application start; see ``review_persistence.sqlite.database``.
#
# 1.0.0 shipped, and predates tenant ownership: its review data belongs to no
# organization or queue. There is no non-destructive upgrade -- an owner would
# have to be invented for every row -- so none is offered, and the error says
# so instead of pointing at a migration that does not exist.
MIGRATION_REQUIRED_SCHEMA_VERSIONS = frozenset({"1.0.0"})

SCHEMA_META_TABLE = "schema_meta"
REVIEW_WORKFLOW_CONTEXT_TABLE = "review_workflow_context"
REVIEW_CASES_TABLE = "review_cases"
REVIEW_CASE_EVENTS_TABLE = "review_case_events"
SEMANTIC_SUGGESTIONS_TABLE = "semantic_suggestions"

REVIEW_TABLES: tuple[str, ...] = (
    REVIEW_WORKFLOW_CONTEXT_TABLE,
    REVIEW_CASES_TABLE,
    REVIEW_CASE_EVENTS_TABLE,
    SEMANTIC_SUGGESTIONS_TABLE,
)

ALL_TABLES: tuple[str, ...] = (
    SCHEMA_META_TABLE,
    *IDENTITY_TABLES,
    *REVIEW_TABLES,
)

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
    """Fail closed on a database this build does not understand.

    A version this build once wrote gets its own error, because the operator
    response differs: they are holding real review data and need to be told
    what can be done with it, while an unknown version means the file was
    written by a different build entirely. Both refuse to open, and neither is
    ever upgraded here.

    The recognized-but-unservable message says plainly that **no migration
    exists**. It used to say "run the explicit operator migration", which named
    nothing: there is no such command in this repository and there is no
    non-destructive one to write, because 1.0.0 review data belongs to no
    organization or review queue and an owner would have to be invented for
    every row. Pointing an operator at a tool that does not exist is worse than
    telling them the truth, which is that the path forward is a new database.

    Nothing is ever upgraded here; this function only reads a string.
    """
    if version in SUPPORTED_DATABASE_SCHEMA_VERSIONS:
        return

    if version in MIGRATION_REQUIRED_SCHEMA_VERSIONS:
        raise ReviewSchemaMigrationRequiredError(
            f"Review database schema version '{version}' predates tenant ownership and "
            f"cannot be served by this build, which requires '{DATABASE_SCHEMA_VERSION}'. "
            "Its review data belongs to no organization or review queue, so there is no "
            "non-destructive upgrade: an owner would have to be invented for every row. "
            "No automatic or operator migration is provided, and nothing here will alter "
            "the file. Provision a new database, create the organization and review queue "
            "explicitly, and register the workflow into them.",
            stored_version=version,
            required_version=DATABASE_SCHEMA_VERSION,
        )

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
# config is loaded from disk at check time. It is kept because the WorkflowBundle
# and repository Protocol both carry it, and because authorizing against a
# different ER config than the queue was generated with would be wrong in a way
# nothing else would catch.
#
# review_queue_id is the PRIMARY KEY, which replaces the 1.0.0 spelling
# ``CHECK (context_id = 1)``. The old constraint said "one context per
# database"; this one says "exactly one context per queue", which is the
# invariant that was actually meant. Two queues now hold two independent
# authorization graphs, and neither constrains the other.
CREATE_REVIEW_WORKFLOW_CONTEXT = f"""
CREATE TABLE IF NOT EXISTS {REVIEW_WORKFLOW_CONTEXT_TABLE} (
    review_queue_id TEXT PRIMARY KEY NOT NULL,
    entity_records_json TEXT NOT NULL,
    resolution_snapshot_json TEXT NOT NULL,
    entity_resolution_config_path TEXT NULL,
    schema_version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (review_queue_id)
        REFERENCES {REVIEW_QUEUES_TABLE} (review_queue_id) ON DELETE RESTRICT
)
""".strip()


# case_payload_json holds the unmodified ReviewCase.to_dict() payload. Evidence
# tuples are intentionally not normalized into tables: they are never queried,
# and a second serialization would compete with the Sprint 08 report contract.
#
# PRIMARY KEY (review_queue_id, review_case_id): review_case_id is deterministic
# from the reviewed record pair, and record ids come from customer data, so the
# same RC- value in two queues is expected rather than exceptional. Both columns
# are declared NOT NULL explicitly -- SQLite does not imply it for the parts of
# a composite primary key.
#
# UNIQUE (review_queue_id, record_a_id, record_b_id) replaces the 1.0.0
# UNIQUE (record_a_id, record_b_id): one case per pair per queue, which is the
# invariant that was meant. Globally unique record pairs would have made two
# tenants who happen to share a record identifier collide.
CREATE_REVIEW_CASES = f"""
CREATE TABLE IF NOT EXISTS {REVIEW_CASES_TABLE} (
    review_queue_id TEXT NOT NULL,
    review_case_id TEXT NOT NULL,
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
    PRIMARY KEY (review_queue_id, review_case_id),
    UNIQUE (review_queue_id, record_a_id, record_b_id),
    FOREIGN KEY (review_queue_id)
        REFERENCES {REVIEW_QUEUES_TABLE} (review_queue_id) ON DELETE RESTRICT
)
""".strip()


# The CHECK below is the schema-level guarantee behind the locked decision that
# persistence never synthesizes a decision: a resolution row is unrepresentable
# without the domain-generated audit entry, and a suggestion row is
# unrepresentable with one.
#
# The foreign key is composite and names both key columns of review_cases, so
# an event can only ever point at a case in its own queue. A single-column
# reference on review_case_id would be ambiguous now that the value repeats
# across queues -- and would be exactly the cross-tenant edge this schema
# exists to make unrepresentable.
#
# event_id stays a global AUTOINCREMENT. It is the append order of the history
# and nothing else; making it queue-local would buy symmetry at the cost of the
# one property it has to keep, which is that a later row always sorts after an
# earlier one.
#
# It *is* published, on ReviewEventRead, to a caller already authorized for the
# queue the event belongs to -- and that was re-examined when tenant
# authorization landed, because a global sequence is an inference channel: the
# gap between two ids one member caused tells them how many events everyone
# else wrote in between.
#
# It is accepted, and here is the whole of the reasoning. What leaks is a count
# of installation-wide write activity, to someone who already holds a valid
# membership; no tenant is named, no case is identified, no content is exposed,
# and nothing about *which* tenant was busy can be inferred. Closing it means
# making the id queue-local, which means a composite primary key, which means a
# DDL change, a schema version bump, a migration for every existing database,
# and a contract change for a published response field the frontend already
# reads. That is a large, durable cost against a side channel that reveals
# aggregate busyness. It is recorded here rather than silently tolerated, and
# it is the right thing to revisit if these ids ever become visible to anyone
# who is not already inside a tenant.
CREATE_REVIEW_CASE_EVENTS = f"""
CREATE TABLE IF NOT EXISTS {REVIEW_CASE_EVENTS_TABLE} (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_queue_id TEXT NOT NULL,
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
    FOREIGN KEY (review_queue_id, review_case_id)
        REFERENCES {REVIEW_CASES_TABLE} (review_queue_id, review_case_id) ON DELETE RESTRICT,
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
#
# PRIMARY KEY (review_queue_id, suggestion_id): a Sprint 09 suggestion id is a
# content address derived from the request and its fingerprint, so two queues
# reviewing structurally identical pairs can produce the same id for two
# genuinely different observations. Scoping uniqueness to the queue keeps the
# replay-is-a-no-op property inside a queue -- which is where it means
# something -- without letting one tenant's stored observation answer for
# another's.
CREATE_SEMANTIC_SUGGESTIONS = f"""
CREATE TABLE IF NOT EXISTS {SEMANTIC_SUGGESTIONS_TABLE} (
    review_queue_id TEXT NOT NULL,
    suggestion_id TEXT NOT NULL,
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
    PRIMARY KEY (review_queue_id, suggestion_id),
    FOREIGN KEY (review_queue_id, review_case_id)
        REFERENCES {REVIEW_CASES_TABLE} (review_queue_id, review_case_id) ON DELETE RESTRICT
)
""".strip()


CREATE_TABLE_STATEMENTS: tuple[str, ...] = (
    CREATE_SCHEMA_META,
    # Identity and tenant roots first: review_queues must exist before anything
    # can declare a foreign key into it.
    *IDENTITY_TABLE_STATEMENTS,
    CREATE_REVIEW_WORKFLOW_CONTEXT,
    CREATE_REVIEW_CASES,
    CREATE_REVIEW_CASE_EVENTS,
    CREATE_SEMANTIC_SUGGESTIONS,
)


# Partial unique index on (queue, sequence) -- deliberately *not* (queue, case,
# sequence), which is what this index said before Sprint 14 Phase A and which
# protected nothing.
#
# The invariant that actually has to hold is queue-global.
# ``review_application.history.reconstruct_history`` sorts every resolved case
# in the queue by ``resolution_sequence`` and requires 1, 2, 3, ... with no gap
# and no repeat, because that is what ``ReviewWorkflow`` produces: it takes the
# state's ``next_resolution_sequence``, uses it, and increments by one.
#
# Including ``review_case_id`` in the key made the index enforce "one ordinal
# per case", which no writer can violate anyway -- a case is resolvable only
# while PENDING, and a second resolution event for one case is rejected several
# layers up. Meanwhile two *different* cases could both claim ordinal 1, since
# (Q, caseA, 1) and (Q, caseB, 1) are different keys. Nothing stopped that at
# the database, and the result is not a tidy duplicate: every later
# ``load_workflow_bundle`` for the queue raises out of the contiguity check, so
# the queue can no longer be read or resolved at all.
#
# Dropping the case id makes the index enforce the invariant the reader
# actually depends on. It is a backstop rather than the primary control -- the
# application service holds one write transaction across load, authorize and
# write, so two writers cannot compute the same ordinal in the first place --
# and it is kept precisely because that reasoning is about application code,
# while this is about what the database will accept.
#
# Still partial: lifecycle and semantic rows carry NULL sequences, many per
# queue, and NULLs must stay unconstrained. That is also the statement that a
# semantic suggestion consumes no resolution ordinal.
RESOLUTION_SEQUENCE_INDEX_NAME = "ux_review_case_events_resolution_sequence"

UNIQUE_RESOLUTION_SEQUENCE_INDEX = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {RESOLUTION_SEQUENCE_INDEX_NAME}
    ON {REVIEW_CASE_EVENTS_TABLE} (review_queue_id, resolution_sequence)
    WHERE resolution_sequence IS NOT NULL
""".strip()


def resolution_sequence_index_enforces_queue_global(sql: str | None) -> bool:
    """True only for a unique index on (review_queue_id, resolution_sequence).

    The Sprint 13 shape named the same index and included ``review_case_id``.
    Name equality is therefore not a compatibility check.

    Uniqueness is checked explicitly rather than assumed from the name. An
    index on the right two columns that is not UNIQUE enforces nothing at all,
    and accepting it would let the guard report an invariant the database does
    not hold. ``sqlite_master`` stores the statement SQLite parsed -- without
    ``IF NOT EXISTS`` -- so a unique index always begins ``CREATE UNIQUE
    INDEX``.
    """
    if not sql:
        return False
    normalized = " ".join(sql.lower().split())
    if not normalized.startswith("create unique index"):
        return False
    if "review_case_id" in normalized:
        return False
    return "review_queue_id" in normalized and "resolution_sequence" in normalized


def assert_resolution_sequence_index(connection: object) -> None:
    """Refuse a 2.0.0 database that does not enforce queue-global sequences.

    Called on every open of an existing database. Missing or weak indexes fail
    closed. The file is not altered: no DROP, no CREATE, no sequence rewrite.
    """
    row = connection.execute(  # type: ignore[attr-defined]
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        (RESOLUTION_SEQUENCE_INDEX_NAME,),
    ).fetchone()
    sql = None if row is None else str(row["sql"] if row["sql"] is not None else "")
    if resolution_sequence_index_enforces_queue_global(sql):
        return
    raise ReviewSchemaVersionError(
        "Review database declares schema version "
        f"'{DATABASE_SCHEMA_VERSION}' but does not enforce a queue-global unique "
        f"resolution_sequence index ({RESOLUTION_SEQUENCE_INDEX_NAME}). This build "
        "will not start a writable runtime against that file. The database is not "
        "upgraded, rewritten, or repaired. Provision a new database and re-register "
        "the workflow. Duplicate sequences are never renumbered in place."
    )


# Every review index leads with review_queue_id, because every review query
# does. An index on status alone would span tenants and answer a question no
# scoped query asks.
REVIEW_INDEX_STATEMENTS: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS ix_review_cases_status "
    f"ON {REVIEW_CASES_TABLE} (review_queue_id, status)",
    f"CREATE INDEX IF NOT EXISTS ix_review_cases_record_a_id "
    f"ON {REVIEW_CASES_TABLE} (review_queue_id, record_a_id)",
    f"CREATE INDEX IF NOT EXISTS ix_review_cases_record_b_id "
    f"ON {REVIEW_CASES_TABLE} (review_queue_id, record_b_id)",
    f"CREATE INDEX IF NOT EXISTS ix_review_case_events_case "
    f"ON {REVIEW_CASE_EVENTS_TABLE} (review_queue_id, review_case_id, event_id)",
    UNIQUE_RESOLUTION_SEQUENCE_INDEX,
    f"CREATE INDEX IF NOT EXISTS ix_semantic_suggestions_case "
    f"ON {SEMANTIC_SUGGESTIONS_TABLE} (review_queue_id, review_case_id)",
)


CREATE_INDEX_STATEMENTS: tuple[str, ...] = (
    *IDENTITY_INDEX_STATEMENTS,
    *REVIEW_INDEX_STATEMENTS,
)


SCHEMA_STATEMENTS: tuple[str, ...] = CREATE_TABLE_STATEMENTS + CREATE_INDEX_STATEMENTS
