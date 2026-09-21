"""Read-only integrity verification for one review queue.

This module answers one question -- "is this queue coherent?" -- and it answers
it without changing anything. It issues no INSERT, UPDATE or DELETE, creates no
index, renumbers no sequence, and repairs nothing. That is not a limitation to
be lifted later: a review queue holds human decisions, and a tool that silently
"fixed" them would destroy the record of what was actually decided. Detection
and repair are different operations with different risks, and only the first
one is safe to automate.

Why it exists now. Sprint 14 Phase A tightened the resolution-sequence index
without bumping the schema version, because 2.0.0 has never shipped and this
project's rule is to extend an unreleased version in place. The honest cost of
that rule is that ``ReviewDatabase.initialize`` runs the DDL only for a file
with none of our tables, so a database created *before* the change keeps the
old, weaker index and still passes the version check. There is no migration to
detect that, so this is what detects it: :func:`verify_review_queue` reports a
queue whose expected indexes are missing.

The findings are deliberately two-layered.

**Queue-level domain integrity** is the point: the cases, their history, the
sequence invariant, the authorization context, and whether the bundle Sprint 08
authorization reads can be reconstructed at all. These are statements about one
tenant's review data.

**Database-level structural integrity** -- ``PRAGMA foreign_key_check`` and
``PRAGMA integrity_check`` -- is reported separately and labelled as such,
because it is a property of the whole file rather than of this queue. Mixing
the two would tell an operator that their queue is corrupt when another
tenant's rows are the problem, or the reverse.

Output is sanitized. Findings name identifiers, counts and constraint names.
They never carry a record value, a reviewer's free-text label, a password hash,
a session token, or an entity record payload -- an integrity report is
something an operator pastes into an issue.
"""

from __future__ import annotations

from dataclasses import dataclass

from review_application.errors import ReviewApplicationError
from review_persistence.identity_schema import ORGANIZATIONS_TABLE, REVIEW_QUEUES_TABLE
from review_persistence.schema import (
    REVIEW_CASE_EVENTS_TABLE,
    REVIEW_CASES_TABLE,
    SEMANTIC_SUGGESTIONS_TABLE,
    assert_supported_schema_version,
)
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository

__all__ = [
    "EXPECTED_REVIEW_INDEXES",
    "IntegrityFinding",
    "QueueIntegrityReport",
    "verify_review_queue",
]

# Indexes every queue-scoped read depends on, by name. The unique one is the
# Sprint 14 Phase A constraint and is the reason this list is checked at all: a
# database created before it exists will be missing exactly that entry.
EXPECTED_REVIEW_INDEXES: tuple[str, ...] = (
    "ux_review_case_events_resolution_sequence",
    "ix_review_cases_status",
    "ix_review_case_events_case",
    "ix_semantic_suggestions_case",
)


@dataclass(frozen=True)
class IntegrityFinding:
    """One thing that is wrong, named by a stable code.

    ``code`` is for an operator to grep and for a test to assert on; ``detail``
    is a sentence built here from identifiers and counts only.
    """

    code: str
    detail: str


@dataclass(frozen=True)
class QueueIntegrityReport:
    """What was checked and what was found. Empty findings means healthy."""

    review_queue_id: str
    organization_id: str | None
    checks_run: tuple[str, ...]
    findings: tuple[IntegrityFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings

    def codes(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.findings)


def verify_review_queue(
    database: ReviewDatabase,
    *,
    review_queue_id: str,
) -> QueueIntegrityReport:
    """Check one queue and report. Never writes, never raises for bad data.

    A malformed queue is a *finding*, not an exception: the whole purpose is to
    describe a broken queue rather than to fail on encountering one. The only
    things that propagate are failures to read at all.

    The checks run in dependency order and later ones are skipped when an
    earlier one has already established that they cannot be meaningful -- there
    is no point reporting a missing workflow context for a queue that does not
    exist.
    """
    findings: list[IntegrityFinding] = []
    checks: list[str] = []
    connection = database.connect()

    checks.append("schema_version_supported")
    try:
        assert_supported_schema_version(database.schema_version())
    except ReviewApplicationError as exc:
        findings.append(IntegrityFinding("SCHEMA_UNSUPPORTED", str(exc)))
        # Nothing below can be trusted against a schema this build cannot serve.
        return QueueIntegrityReport(
            review_queue_id=review_queue_id,
            organization_id=None,
            checks_run=tuple(checks),
            findings=tuple(findings),
        )

    checks.append("queue_exists_and_is_owned")
    row = connection.execute(
        f"SELECT q.organization_id AS organization_id, o.status AS status "
        f"FROM {REVIEW_QUEUES_TABLE} AS q "
        f"LEFT JOIN {ORGANIZATIONS_TABLE} AS o ON o.organization_id = q.organization_id "
        "WHERE q.review_queue_id = ?",
        (review_queue_id,),
    ).fetchone()
    if row is None:
        findings.append(
            IntegrityFinding(
                "QUEUE_NOT_FOUND",
                f"No review queue {review_queue_id} is stored in this database.",
            )
        )
        return QueueIntegrityReport(
            review_queue_id=review_queue_id,
            organization_id=None,
            checks_run=tuple(checks),
            findings=tuple(findings),
        )

    organization_id = str(row["organization_id"])
    if row["status"] is None:
        # Unrepresentable while the foreign key is enforced, which is why it is
        # worth reporting: it means the constraint was off when the row landed.
        findings.append(
            IntegrityFinding(
                "QUEUE_ORGANIZATION_MISSING",
                f"Review queue {review_queue_id} names organization {organization_id}, "
                "which is not stored. Ownership is what makes review data belong to a "
                "tenant, so this queue currently belongs to nobody.",
            )
        )

    findings.extend(_check_expected_indexes(connection))
    checks.append("expected_indexes_present")

    findings.extend(_check_sequence_uniqueness(connection, review_queue_id))
    checks.append("resolution_sequence_queue_global_unique")

    findings.extend(_check_orphaned_children(connection, review_queue_id))
    checks.append("child_rows_reference_a_stored_case")

    findings.extend(_check_resolved_cases_and_history(connection, review_queue_id))
    checks.append("resolved_cases_agree_with_history")

    findings.extend(_check_bundle_reconstructs(database, review_queue_id))
    checks.append("workflow_bundle_reconstructs")

    findings.extend(_check_structural_integrity(connection))
    checks.append("database_structural_integrity")

    return QueueIntegrityReport(
        review_queue_id=review_queue_id,
        organization_id=organization_id,
        checks_run=tuple(checks),
        findings=tuple(findings),
    )


def _check_expected_indexes(connection) -> list[IntegrityFinding]:  # type: ignore[no-untyped-def]
    """The check that catches a database created before a constraint existed.

    Named indexes rather than a DDL comparison: an index name is stable and an
    operator can act on it, while a textual DDL diff would report formatting.
    """
    present = {
        str(row["name"])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    missing = [name for name in EXPECTED_REVIEW_INDEXES if name not in present]
    if not missing:
        return []
    return [
        IntegrityFinding(
            "INDEX_MISSING",
            f"Expected indexes are absent: {sorted(missing)}. A database created before "
            "an index was added keeps the schema it was created with, because nothing "
            "alters an existing database on startup. Provision a new database and "
            "re-register the workflow, or add the index deliberately after verifying the "
            "data satisfies it.",
        )
    ]


def _check_sequence_uniqueness(connection, review_queue_id: str) -> list[IntegrityFinding]:  # type: ignore[no-untyped-def]
    """Duplicate ordinals, reported rather than renumbered.

    Renumbering would change which decision came first, which is an audit
    statement. So this reports the duplication and stops; deciding what the real
    order was is a human judgement about human decisions.
    """
    duplicates = connection.execute(
        f"SELECT resolution_sequence AS seq, COUNT(*) AS n FROM {REVIEW_CASE_EVENTS_TABLE} "
        "WHERE review_queue_id = ? AND resolution_sequence IS NOT NULL "
        "GROUP BY resolution_sequence HAVING COUNT(*) > 1 ORDER BY resolution_sequence",
        (review_queue_id,),
    ).fetchall()
    if not duplicates:
        return []
    detail = ", ".join(f"{row['seq']} x{row['n']}" for row in duplicates)
    return [
        IntegrityFinding(
            "RESOLUTION_SEQUENCE_DUPLICATED",
            f"Resolution ordinals are claimed more than once in queue {review_queue_id}: "
            f"{detail}. History reconstruction requires 1, 2, 3, ... with no repeat, so "
            "this queue cannot be loaded or resolved. Nothing here renumbers it: the "
            "ordinal is an audit statement about which decision came first.",
        )
    ]


def _check_orphaned_children(connection, review_queue_id: str) -> list[IntegrityFinding]:  # type: ignore[no-untyped-def]
    """Events and suggestions whose case is not in the same queue."""
    findings: list[IntegrityFinding] = []
    for table, label in (
        (REVIEW_CASE_EVENTS_TABLE, "event"),
        (SEMANTIC_SUGGESTIONS_TABLE, "semantic suggestion"),
    ):
        orphans = connection.execute(
            f"SELECT COUNT(*) AS n FROM {table} AS child "  # noqa: S608 - table is a constant
            f"LEFT JOIN {REVIEW_CASES_TABLE} AS c "
            "ON c.review_queue_id = child.review_queue_id "
            "AND c.review_case_id = child.review_case_id "
            "WHERE child.review_queue_id = ? AND c.review_case_id IS NULL",
            (review_queue_id,),
        ).fetchone()
        if orphans["n"]:
            findings.append(
                IntegrityFinding(
                    "CHILD_ROW_ORPHANED",
                    f"{orphans['n']} {label} row(s) in queue {review_queue_id} reference a "
                    "review case that is not stored in that queue. The composite foreign "
                    "key makes this unrepresentable, so it indicates the constraint was "
                    "not enforced when the rows were written.",
                )
            )
    return findings


def _check_resolved_cases_and_history(connection, review_queue_id: str) -> list[IntegrityFinding]:  # type: ignore[no-untyped-def]
    """A resolved case must not carry more than one resolution event.

    The eventless resolved case is *not* reported. It is the legacy shape of a
    database written before the event table existed, and
    ``review_application.history`` reads it deliberately by projecting the
    resolution the domain already stamped on the case.
    """
    findings: list[IntegrityFinding] = []
    duplicated = connection.execute(
        f"SELECT review_case_id, COUNT(*) AS n FROM {REVIEW_CASE_EVENTS_TABLE} "
        "WHERE review_queue_id = ? AND resolution_sequence IS NOT NULL "
        "GROUP BY review_case_id HAVING COUNT(*) > 1",
        (review_queue_id,),
    ).fetchall()
    for row in duplicated:
        findings.append(
            IntegrityFinding(
                "CASE_RESOLVED_MORE_THAN_ONCE",
                f"Review case {row['review_case_id']} carries {row['n']} resolution "
                "events. A case is resolvable only while PENDING, so history was written "
                "by something other than the workflow.",
            )
        )

    pending_with_resolution = connection.execute(
        f"SELECT COUNT(*) AS n FROM {REVIEW_CASES_TABLE} AS c "
        f"JOIN {REVIEW_CASE_EVENTS_TABLE} AS e "
        "ON e.review_queue_id = c.review_queue_id AND e.review_case_id = c.review_case_id "
        "WHERE c.review_queue_id = ? AND c.status = 'PENDING' "
        "AND e.resolution_sequence IS NOT NULL",
        (review_queue_id,),
    ).fetchone()
    if pending_with_resolution["n"]:
        findings.append(
            IntegrityFinding(
                "PENDING_CASE_HAS_RESOLUTION_EVENT",
                f"{pending_with_resolution['n']} case(s) in queue {review_queue_id} are "
                "PENDING but have a resolution event. The case row and its history "
                "disagree about whether a decision was made.",
            )
        )
    return findings


def _check_bundle_reconstructs(  # type: ignore[no-untyped-def]
    database: ReviewDatabase,
    review_queue_id: str,
) -> list[IntegrityFinding]:
    """The end-to-end check: can Sprint 08 authorization material be rebuilt?

    Every check above looks at one property. This one asks the question that
    matters operationally -- would a reviewer be able to resolve a case in this
    queue right now -- by doing exactly what the application service does
    first. A queue that passes everything else and fails here is still unusable.

    ``load_workflow_bundle`` is a read. It opens a DEFERRED transaction and
    writes nothing.
    """
    repository = SqliteReviewCaseRepository(database, review_queue_id=review_queue_id)
    try:
        bundle = repository.load_workflow_bundle()
    except ReviewApplicationError as exc:
        return [
            IntegrityFinding(
                "BUNDLE_NOT_RECONSTRUCTABLE",
                f"The authorization bundle for queue {review_queue_id} could not be "
                f"loaded: {type(exc).__name__}. No decision can be authorized in this "
                "queue until that is resolved.",
            )
        ]

    findings: list[IntegrityFinding] = []
    if not bundle.entity_records:
        findings.append(
            IntegrityFinding(
                "CONTEXT_HAS_NO_RECORDS",
                f"The workflow context for queue {review_queue_id} carries no entity "
                "records, so MATCH authorization would fail closed for every case.",
            )
        )
    return findings


def _check_structural_integrity(connection) -> list[IntegrityFinding]:  # type: ignore[no-untyped-def]
    """Whole-file checks, labelled as whole-file rather than queue-level.

    ``foreign_key_check`` and ``integrity_check`` describe the database, not one
    tenant. Reporting them under a queue's name would tell an operator their
    queue is broken when another tenant's rows, or the file itself, is the
    problem -- so the codes say so.
    """
    findings: list[IntegrityFinding] = []

    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        tables = sorted({str(row[0]) for row in violations})
        findings.append(
            IntegrityFinding(
                "DATABASE_FOREIGN_KEY_VIOLATIONS",
                f"{len(violations)} foreign-key violation(s) in this database, in tables "
                f"{tables}. This is a database-wide finding and is not scoped to one "
                "queue.",
            )
        )

    result = connection.execute("PRAGMA integrity_check").fetchone()
    verdict = str(result[0]) if result is not None else "unknown"
    if verdict != "ok":
        findings.append(
            IntegrityFinding(
                "DATABASE_INTEGRITY_CHECK_FAILED",
                f"SQLite reports the database file as not ok ({verdict}). This is a "
                "database-wide finding and is not scoped to one queue.",
            )
        )
    return findings
