"""SQLite storage for one queue's review cases, history, and workflow context.

The complete ``ReviewCaseRepository`` Protocol -- ``register_workflow``,
``register_case``, ``get_case``, ``list_cases``, ``load_workflow_bundle``,
``apply_resolution``, ``list_events``, ``record_semantic_suggestion`` and
``list_semantic_suggestions`` -- implemented against one ``review_queues`` row.

**Every statement is scoped to that queue.** The repository is constructed with
a ``review_queue_id`` and holds it for its lifetime; there is no method that
reaches outside it and no argument that could widen it. Two queues may contain
the same deterministic ``review_case_id`` -- ``stable_review_case_id`` derives
it from customer record identifiers, so this is ordinary rather than exotic --
and each instance sees only its own.

The Protocol itself is unchanged. That is the point of binding the queue at
construction rather than adding a parameter to nine methods: the application
service, the routes above it, and every test written against the contract keep
working untouched, while the possibility of an unscoped query disappears from
this module entirely.

There is exactly one write path that can change a case status, and it is
:meth:`apply_resolution`. It refuses anything but a ``ReviewCase`` the domain
already transitioned together with the event projecting that decision, so the
repository never decides MATCH, NO_MATCH or DEFER and has no primitive capable
of expressing one. Nothing deletes, and nothing updates an event: history is
append-only.

The semantic methods sit entirely outside that path.
:meth:`record_semantic_suggestion` writes to ``semantic_suggestions`` and
appends a non-resolution event; it never touches ``review_cases``. A Sprint 09
suggestion is advisory, and the only thing persistence adds to it is
durability.

``register_workflow`` is the canonical production entry point, because it is the
only one that stores the authorization context alongside the cases.
``register_case`` remains for low-level persistence of a single case; it does
not manufacture a context, and a queue built only from it cannot be loaded as a
bundle -- :meth:`load_workflow_bundle` fails closed instead of returning an
authorization-incomplete view.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from entity_resolution.models import EntityRecord
from human_review.errors import ReviewCaseNotFoundError
from human_review.models import ReviewCase, ReviewStatus, ReviewWorkflowState
from identity.errors import OrganizationNotActiveError
from identity.ids import assert_opaque_id
from identity.models import OrganizationStatus
from review_application.errors import (
    DuplicateCaseRegistrationError,
    PersistedCaseIntegrityError,
    ReviewConflictError,
    ReviewEventIntegrityError,
    ReviewPersistenceError,
    ReviewQueueNotFoundError,
    ReviewWorkflowContextConflictError,
    ReviewWorkflowContextMissingError,
    SemanticSuggestionConflictError,
    SemanticSuggestionIntegrityError,
)
from review_application.history import reconstruct_history
from review_application.models import (
    DECISION_TO_EVENT_TYPE,
    PersistedCase,
    ReviewEvent,
    WorkflowBundle,
)
from review_application.queues import REVIEW_QUEUE_ID_PREFIX
from review_persistence.identity_schema import ORGANIZATIONS_TABLE, REVIEW_QUEUES_TABLE
from review_persistence.schema import (
    DATABASE_SCHEMA_VERSION,
    REVIEW_CASE_EVENTS_TABLE,
    REVIEW_CASES_TABLE,
    REVIEW_WORKFLOW_CONTEXT_TABLE,
    SEMANTIC_SUGGESTIONS_TABLE,
)
from review_persistence.sqlite.context_mapper import (
    canonical_json,
    decode_json_column,
    entity_records_from_payload,
    entity_records_to_payload,
    normalized_context_fingerprint,
    snapshot_from_payload,
    snapshot_to_payload,
)
from review_persistence.sqlite.database import Clock, ReviewDatabase, _now, utc_timestamp
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
    row_to_persisted_case,
)
from review_persistence.sqlite.semantic_mapper import (
    SEMANTIC_SUGGESTION_COLUMNS,
    assert_is_sprint_09_suggestion,
    canonical_suggestion_json,
    row_to_semantic_suggestion,
    suggestion_to_row,
)
from semantic_review.models import SemanticSuggestion

# Every statement below is queue-scoped, and the scoping is not optional. A
# repository instance is bound to one review_queue_id for its whole lifetime
# and interpolates nothing: the queue arrives as a bound parameter in the same
# WHERE clause as the case id. There is no unscoped spelling of any of these,
# so a query that forgot its tenant predicate is not something this module can
# express.
_SELECT_COLUMNS = ", ".join(REVIEW_CASE_COLUMNS)
_INSERT_CASE = (
    f"INSERT INTO {REVIEW_CASES_TABLE} ({_SELECT_COLUMNS}) "
    f"VALUES ({', '.join('?' * len(REVIEW_CASE_COLUMNS))})"
)
_SELECT_CASES = f"SELECT {_SELECT_COLUMNS} FROM {REVIEW_CASES_TABLE} WHERE review_queue_id = ?"
_CASE_ORDER = " ORDER BY created_at_utc, review_case_id"

_CONTEXT_COLUMNS = (
    "review_queue_id",
    "entity_records_json",
    "resolution_snapshot_json",
    "entity_resolution_config_path",
    "schema_version",
    "created_at_utc",
    "updated_at_utc",
)
_INSERT_CONTEXT = (
    f"INSERT INTO {REVIEW_WORKFLOW_CONTEXT_TABLE} ({', '.join(_CONTEXT_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_CONTEXT_COLUMNS))})"
)
# One context per queue, addressed by the queue. The 1.0.0 spelling of this
# query was ``WHERE context_id = 1``, which could only ever describe a database
# holding a single workflow.
_SELECT_CONTEXT = (
    f"SELECT {', '.join(_CONTEXT_COLUMNS)} FROM {REVIEW_WORKFLOW_CONTEXT_TABLE} "
    "WHERE review_queue_id = ?"
)

# The queue plus the status of the organization that owns it, in one read.
# Ownership is what makes review data belong to a tenant at all, so the two
# facts a registration needs -- does this queue exist, and may its tenant be
# worked in -- are answered from one row rather than from two reads that could
# describe different moments. An INNER JOIN is correct rather than convenient:
# the foreign key makes an orphaned queue unrepresentable, so a queue with no
# organization row is not a case to handle but a database that is already wrong.
_SELECT_QUEUE_WITH_ORGANIZATION = (
    f"SELECT q.review_queue_id AS review_queue_id, o.status AS organization_status "
    f"FROM {REVIEW_QUEUES_TABLE} AS q "
    f"JOIN {ORGANIZATIONS_TABLE} AS o ON o.organization_id = q.organization_id "
    "WHERE q.review_queue_id = ?"
)

# The compare-and-swap. The version in the WHERE clause is the whole protection:
# a reviewer whose expected_version has since been superseded matches no row, so
# the second write affects nothing instead of overwriting the first decision.
# version = version + 1 is computed by SQLite, so two writers cannot arrive at
# the same next value from the same stale read.
#
# review_queue_id joins that predicate rather than replacing anything in it.
# Without it, a deterministic review_case_id that exists in two queues would
# make this statement address whichever row SQLite reached first -- a
# cross-tenant write with no error and no trace.
_CAS_UPDATE_CASE = (
    f"UPDATE {REVIEW_CASES_TABLE} SET "
    "status = ?, version = version + 1, case_payload_json = ?, updated_at_utc = ? "
    "WHERE review_queue_id = ? AND review_case_id = ? AND version = ?"
)

_INSERT_EVENT = (
    f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} ({', '.join(REVIEW_EVENT_INSERT_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(REVIEW_EVENT_INSERT_COLUMNS))})"
)
_SELECT_EVENTS = (
    f"SELECT {', '.join(REVIEW_EVENT_SELECT_COLUMNS)} FROM {REVIEW_CASE_EVENTS_TABLE} "
    "WHERE review_queue_id = ?"
)
# event_id is the AUTOINCREMENT append order, which is the only ordering that
# stays correct when two events share a timestamp.
_EVENT_ORDER = " ORDER BY event_id ASC"

_SUGGESTION_SELECT_COLUMNS = ", ".join(SEMANTIC_SUGGESTION_COLUMNS)
_INSERT_SUGGESTION = (
    f"INSERT INTO {SEMANTIC_SUGGESTIONS_TABLE} ({_SUGGESTION_SELECT_COLUMNS}) "
    f"VALUES ({', '.join('?' * len(SEMANTIC_SUGGESTION_COLUMNS))})"
)
_SELECT_SUGGESTIONS = (
    f"SELECT {_SUGGESTION_SELECT_COLUMNS} FROM {SEMANTIC_SUGGESTIONS_TABLE} "
    "WHERE review_queue_id = ?"
)
# Suggestion ids are content addresses, not sequence numbers, so they carry no
# time information. created_at_utc first gives observation order; the id breaks
# ties between two recorded in the same second. Neither confers authority.
_SUGGESTION_ORDER = " ORDER BY created_at_utc ASC, suggestion_id ASC"


def _assert_registrable(case: ReviewCase) -> None:
    """Registration stores new work. It does not record decisions.

    ``generate_review_cases`` is deterministic and always emits the PENDING
    form of every case, so a resolved case arriving at a registration
    primitive never came from the supported pipeline.

    Refusing it closes a real bypass. Registration inserts at version 1 with no
    resolution event, and ``review_application.history`` reads "resolved case,
    no event" as a legitimate import from before the event table existed. So a
    resolved ``ReviewCase`` inserted here would become durable, readable
    history that never passed
    ``assert_human_match_authorization_boundary`` -- a MATCH nobody authorized,
    indistinguishable afterwards from one that was. ``apply_resolution`` exists
    to be the only way a status changes, and its own guard is the mirror of
    this one: it refuses a case that is *still* PENDING.

    Resolved historical import is deliberately not offered. If it is ever
    genuinely needed it wants its own named, explicitly high-risk entry point,
    not a widened meaning for the primitive every ordinary registration uses.
    """
    if case.status is not ReviewStatus.PENDING:
        raise PersistedCaseIntegrityError(
            f"Review case {case.review_case_id} is {case.status.value}; registration "
            "stores PENDING cases only. A decision may be persisted only by "
            "apply_resolution, from a case ReviewWorkflow.resolve_case produced."
        )


@dataclass(frozen=True)
class StoredWorkflowContext:
    """The persisted authorization context, decoded but not yet domain-typed."""

    entity_records_payload: list[dict[str, Any]]
    resolution_snapshot: dict[str, Any]
    entity_resolution_config_path: str | None
    created_at_utc: str
    updated_at_utc: str

    @property
    def fingerprint(self) -> str:
        return normalized_context_fingerprint(self.entity_records_payload, self.resolution_snapshot)

    def entity_records(self) -> tuple[EntityRecord, ...]:
        return entity_records_from_payload(self.entity_records_payload)


class SqliteReviewCaseRepository:
    """Storage for **one** review queue and its authorization context.

    The queue is fixed at construction and cannot be changed, read from a
    method argument, or widened. Every statement this class issues carries it,
    so "which tenant's data is this" is answered once, by whoever built the
    repository, instead of by each of the nine Protocol methods separately.

    That is deliberately the opposite of passing ``review_queue_id`` to each
    method. A per-call parameter can be forgotten or supplied from the wrong
    place and still type-check; a constructor binding cannot, because there is
    no instance without one. It also leaves the ``ReviewCaseRepository``
    Protocol untouched -- ``ReviewQueueService`` and every route above it speak
    the same nine methods they always did, and gain tenant safety without
    knowing that tenants exist.

    A verified *tenant* scope -- which user, which organization, which role --
    is a later phase's job. This class establishes the persistence half: the
    queue binding it will sit on.
    """

    def __init__(
        self,
        database: ReviewDatabase,
        *,
        review_queue_id: str,
        clock: Clock = _now,
    ) -> None:
        self._database = database
        self._review_queue_id = assert_opaque_id(
            review_queue_id,
            prefix=REVIEW_QUEUE_ID_PREFIX,
            field_name="review_queue_id",
        )
        self._clock = clock

    @property
    def review_queue_id(self) -> str:
        """The one queue this repository can read or write. Never reassigned."""
        return self._review_queue_id

    # -- serialized write scope --------------------------------------------

    @contextmanager
    def unit_of_work(self) -> Iterator[None]:
        """One IMMEDIATE transaction spanning every call made inside it.

        IMMEDIATE takes the write lock at the start rather than on the first
        write, which is the property that matters here: the bundle a caller
        loads inside this scope cannot change before the resolution computed
        from it commits, because no other writer can be in between.

        ``ReviewDatabase.transaction`` is reentrant, so the methods below keep
        taking their own transactions and simply join this one. Nothing had to
        learn whether it was called first.

        Yields ``None`` on purpose. The application layer bounds a scope with
        this; handing it the connection would hand it SQL.
        """
        with self._database.transaction():
            yield None

    # -- workflow registration ---------------------------------------------

    def register_workflow(
        self,
        state: ReviewWorkflowState,
        *,
        entity_records: Sequence[EntityRecord],
        resolution_snapshot: Mapping[str, Any],
        entity_resolution_config_path: str | None = None,
        now_utc: str | None = None,
    ) -> tuple[PersistedCase, ...]:
        """Store the authorization context and the generated cases as one unit.

        Atomic by construction: context and cases are written inside a single
        transaction, so the database can never hold cases whose records are
        absent, nor a context describing cases that were never stored.

        Idempotent **within this queue**. Re-registering the same workflow
        rewrites nothing -- not the context, not its timestamps, not a single
        case. A stored case is returned exactly as it stands, so a resolved
        MATCH survives a re-run of deterministic case generation, which always
        emits the PENDING form.

        Idempotence and conflict are both queue-local, and that is the whole
        tenancy property on the write side. Registering an identical context
        into a *different* queue is a first registration there, not a replay;
        registering a changed context conflicts only with the queue that
        already holds one. Two tenants who happen to generate identical
        workflows are independent, and neither can observe the other.

        Raises ``ReviewQueueNotFoundError`` when the bound queue does not
        exist, ``ReviewWorkflowContextConflictError`` when *this queue's*
        stored context describes a different record set or AUTO_MATCH
        snapshot, and ``DuplicateCaseRegistrationError`` on a case identity
        conflict. None of them leaves a partial write behind.
        """
        records = tuple(entity_records)
        records_payload = entity_records_to_payload(records)
        snapshot_payload = snapshot_to_payload(resolution_snapshot)
        self._assert_records_cover_cases(state, records)
        # Checked for the whole batch before the transaction opens, for the same
        # reason record coverage is: one unregistrable case makes the whole
        # workflow unregistrable, and an operator is better told that before any
        # lock is taken. ``_register_case_in`` enforces it again per case, which
        # is what covers ``register_case`` and makes the rollback below the
        # atomicity guarantee rather than this loop.
        for case in state.cases:
            _assert_registrable(case)

        with self._database.transaction() as connection:
            # Checked inside the transaction and before anything is written.
            # The foreign key would refuse the rows anyway, but as a bare
            # IntegrityError; an operator addressing a queue that does not
            # exist deserves to be told that, not a constraint name.
            self._assert_queue_accepts_registration(connection)
            timestamp = now_utc if now_utc is not None else utc_timestamp(self._clock)
            self._store_context(
                connection,
                records_payload=records_payload,
                snapshot_payload=snapshot_payload,
                entity_resolution_config_path=entity_resolution_config_path,
                timestamp=timestamp,
            )
            return tuple(
                self._register_case_in(connection, case, timestamp) for case in state.cases
            )

    def _assert_queue_accepts_registration(self, connection: sqlite3.Connection) -> None:
        """Refuse to write review data into a queue that may not receive it.

        Two conditions, and they fail differently on purpose.

        The queue must exist. With tenant ownership flowing through the queue, a
        case whose queue is absent would be review data owned by no
        organization at all.

        The owning organization must also be ACTIVE. Registering a workflow is
        ordinary business work, and a suspended tenant is one whose queues may
        not be worked on -- the same policy ``review_api.tenancy`` applies to
        every HTTP request, enforced here so that suspension does not mean
        merely "unreachable over HTTP" while an operator command keeps filling
        the queue up. Nothing already stored is touched or invalidated.

        Both run inside the caller's IMMEDIATE transaction, so neither answer
        can change before the rows this guards are written.
        """
        row = connection.execute(
            _SELECT_QUEUE_WITH_ORGANIZATION, (self._review_queue_id,)
        ).fetchone()
        if row is None:
            raise ReviewQueueNotFoundError(
                f"Review queue {self._review_queue_id} is not stored. Create the queue "
                "through the operator bootstrap path before registering a workflow into it."
            )
        status = OrganizationStatus(str(row["organization_status"]))
        if status is not OrganizationStatus.ACTIVE:
            raise OrganizationNotActiveError(
                f"Review queue {self._review_queue_id} belongs to an organization that is "
                f"{status.value}, not ACTIVE. Registering a workflow is ordinary business "
                "work and is refused in a suspended organization. Nothing already stored "
                "in the queue has been changed."
            )

    @staticmethod
    def _assert_records_cover_cases(
        state: ReviewWorkflowState,
        records: Sequence[EntityRecord],
    ) -> None:
        """Refuse a workflow whose cases reference records it does not carry.

        The same rule WorkflowBundle enforces on load, applied at write time so
        an unusable context never reaches the database in the first place.
        """
        if not records:
            raise ReviewPersistenceError(
                "A workflow context requires entity records; MATCH authorization "
                "would fail closed without them."
            )
        known = {record.record_id for record in records}
        missing = sorted(
            {
                record_id
                for case in state.cases
                for record_id in (case.pair.record_a_id, case.pair.record_b_id)
                if record_id not in known
            }
        )
        if missing:
            raise ReviewPersistenceError(
                f"Workflow context omits records {missing} required by its own review "
                "cases; MATCH authorization would fail closed."
            )

    def _store_context(
        self,
        connection: sqlite3.Connection,
        *,
        records_payload: list[dict[str, Any]],
        snapshot_payload: dict[str, Any],
        entity_resolution_config_path: str | None,
        timestamp: str,
    ) -> None:
        incoming = normalized_context_fingerprint(records_payload, snapshot_payload)
        stored = self._select_context(connection)

        if stored is None:
            connection.execute(
                _INSERT_CONTEXT,
                (
                    self._review_queue_id,
                    canonical_json(records_payload),
                    canonical_json(snapshot_payload),
                    entity_resolution_config_path,
                    DATABASE_SCHEMA_VERSION,
                    timestamp,
                    timestamp,
                ),
            )
            return

        if stored.fingerprint != incoming:
            raise ReviewWorkflowContextConflictError(
                f"Review queue {self._review_queue_id} already stores a workflow "
                "authorization context describing a different record set or AUTO_MATCH "
                "snapshot than the one being registered. Refusing to replace it: human "
                "decisions already recorded in this queue were authorized against the "
                "stored context."
            )
        if (
            entity_resolution_config_path is not None
            and stored.entity_resolution_config_path is not None
            and entity_resolution_config_path != stored.entity_resolution_config_path
        ):
            raise ReviewWorkflowContextConflictError(
                f"Review queue {self._review_queue_id} was generated with "
                f"entity-resolution config {stored.entity_resolution_config_path!r}, not "
                f"{entity_resolution_config_path!r}. Authorizing against different "
                "thresholds than the queue was generated with is unsafe."
            )
        # Equivalent context: a true no-op, so updated_at_utc is left alone.

    # -- case registration -------------------------------------------------

    def register_case(self, case: ReviewCase, *, now_utc: str | None = None) -> PersistedCase:
        """Store one case, or return the stored one untouched.

        Lower-level than :meth:`register_workflow` and does not create a
        workflow context. Cases registered this way alone cannot be loaded as a
        bundle; :meth:`load_workflow_bundle` fails closed rather than inventing
        the missing authorization material.
        """
        with self._database.transaction() as connection:
            self._assert_queue_accepts_registration(connection)
            timestamp = now_utc if now_utc is not None else utc_timestamp(self._clock)
            return self._register_case_in(connection, case, timestamp)

    def _register_case_in(
        self,
        connection: sqlite3.Connection,
        case: ReviewCase,
        timestamp: str,
    ) -> PersistedCase:
        """Insert-if-absent for one case, inside a caller-owned transaction."""
        _assert_registrable(case)
        existing = self._select_case(connection, case.review_case_id)
        if existing is not None:
            self._assert_identity_matches(existing, case)
            return existing

        persisted = PersistedCase.initial(case, now_utc=timestamp)
        try:
            connection.execute(
                _INSERT_CASE,
                case_to_row(
                    persisted,
                    review_queue_id=self._review_queue_id,
                    schema_version=DATABASE_SCHEMA_VERSION,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise self._registration_conflict(connection, case, exc) from exc
        return persisted

    def _registration_conflict(
        self,
        connection: sqlite3.Connection,
        case: ReviewCase,
        exc: sqlite3.IntegrityError,
    ) -> DuplicateCaseRegistrationError:
        """Translate a UNIQUE(queue, record_a_id, record_b_id) violation.

        Scoped to this queue like everything else: the same record pair in
        another tenant's queue is not a clash and must not be reported as one.
        """
        clash = connection.execute(
            f"SELECT review_case_id FROM {REVIEW_CASES_TABLE} "
            "WHERE review_queue_id = ? AND record_a_id = ? AND record_b_id = ?",
            (self._review_queue_id, case.pair.record_a_id, case.pair.record_b_id),
        ).fetchone()
        if clash is not None:
            return DuplicateCaseRegistrationError(
                f"Record pair ({case.pair.record_a_id}, {case.pair.record_b_id}) is already "
                f"stored as review case {clash['review_case_id']}, not "
                f"{case.review_case_id}. Refusing to store a second case for one pair."
            )
        return DuplicateCaseRegistrationError(
            f"Review case {case.review_case_id} violates a storage constraint: {exc}"
        )

    @staticmethod
    def _assert_identity_matches(stored: PersistedCase, incoming: ReviewCase) -> None:
        """Immutable identity is review_case_id plus the ordered record pair.

        A deterministic id that arrives describing a different pair means the
        two cases are not the same case. Merging or overwriting would destroy
        one of them, so registration fails closed instead.
        """
        stored_identity = (
            stored.case.review_case_id,
            stored.case.pair.record_a_id,
            stored.case.pair.record_b_id,
        )
        incoming_identity = (
            incoming.review_case_id,
            incoming.pair.record_a_id,
            incoming.pair.record_b_id,
        )
        if stored_identity != incoming_identity:
            raise DuplicateCaseRegistrationError(
                f"Review case {incoming.review_case_id} is already stored with identity "
                f"{stored_identity}, which contradicts {incoming_identity}. "
                "Refusing to overwrite, merge, or regenerate the identifier."
            )

    # -- resolution --------------------------------------------------------

    def apply_resolution(
        self,
        resolved_case: ReviewCase,
        *,
        expected_version: int,
        event: ReviewEvent,
        now_utc: str | None = None,
    ) -> PersistedCase:
        """Persist a decision ``ReviewWorkflow.resolve_case`` has already made.

        The only inputs are the domain object the workflow returned and the
        event projecting its audit entry, so there is no way to ask this method
        for a status: it can only record one that already exists. Everything it
        is handed is cross-checked against the stored row before a single byte
        is written.

        The versioned UPDATE and the history INSERT run in one IMMEDIATE
        transaction. Either both land or neither does, which is what makes a
        version bump without its event -- or an event without its case --
        unrepresentable rather than merely unlikely.

        Raises ``ReviewConflictError`` when another writer has advanced the case
        since ``expected_version`` was read; nothing is written in that case.
        """
        self._assert_resolution_is_domain_approved(resolved_case, event)

        with self._database.transaction() as connection:
            stored = self._require_resolvable_case(
                self._select_case(connection, resolved_case.review_case_id),
                resolved_case,
                expected_version,
            )
            timestamp = now_utc if now_utc is not None else utc_timestamp(self._clock)

            self._compare_and_swap_case(connection, resolved_case, expected_version, timestamp)
            self._append_event(connection, event)
            return stored.with_case(resolved_case, now_utc=timestamp)

    @staticmethod
    def _assert_resolution_is_domain_approved(case: ReviewCase, event: ReviewEvent) -> None:
        """Refuse anything the Sprint 08 workflow could not have produced.

        ``ReviewEvent`` already pins the event to its audit payload. What is
        checked here is the other half: that the case and the event describe the
        same decision, by the same reviewer, at the same point in the sequence.
        A mismatch means the two were not produced by one ``resolve_case`` call.
        """
        if case.status is ReviewStatus.PENDING:
            raise PersistedCaseIntegrityError(
                f"Review case {case.review_case_id} is still PENDING; apply_resolution "
                "persists decisions, it does not make them."
            )
        resolution = case.resolution
        if resolution is None:
            raise PersistedCaseIntegrityError(
                f"Review case {case.review_case_id} is {case.status.value} but carries no "
                "ReviewResolution. Only ReviewWorkflow.resolve_case may produce one."
            )
        if not event.is_resolution:
            raise ReviewEventIntegrityError(
                f"apply_resolution requires a resolution event; got {event.event_type.value}."
            )
        if event.review_case_id != case.review_case_id:
            raise ReviewEventIntegrityError(
                f"Event belongs to review case {event.review_case_id}, not {case.review_case_id}."
            )
        if case.status.value != event.event_type.value:
            raise ReviewEventIntegrityError(
                f"Review case {case.review_case_id} is {case.status.value} but the event "
                f"records {event.event_type.value}."
            )
        if DECISION_TO_EVENT_TYPE[resolution.human_decision] != event.event_type:
            raise ReviewEventIntegrityError(
                f"Decision {resolution.human_decision.value} cannot be recorded as event "
                f"{event.event_type.value}."
            )
        if resolution.resolution_sequence != event.resolution_sequence:
            raise ReviewEventIntegrityError(
                f"Resolution sequence {resolution.resolution_sequence} disagrees with the "
                f"event sequence {event.resolution_sequence}."
            )
        if resolution.reviewer_id != event.reviewer_id:
            raise ReviewEventIntegrityError(
                f"Reviewer {resolution.reviewer_id!r} disagrees with the event reviewer "
                f"{event.reviewer_id!r}."
            )

    @staticmethod
    def _require_resolvable_case(
        stored: PersistedCase | None,
        resolved_case: ReviewCase,
        expected_version: int,
    ) -> PersistedCase:
        """Return the stored row, once it is established that it may be updated.

        Identity, state and version are all checked here rather than trusted
        from the caller's in-memory copy, which may have been loaded before
        another reviewer wrote.
        """
        if stored is None:
            raise ReviewCaseNotFoundError(f"Review case not found: {resolved_case.review_case_id}")
        stored_pair = (stored.case.pair.record_a_id, stored.case.pair.record_b_id)
        resolved_pair = (resolved_case.pair.record_a_id, resolved_case.pair.record_b_id)
        if stored_pair != resolved_pair:
            raise PersistedCaseIntegrityError(
                f"Review case {resolved_case.review_case_id} is stored for records "
                f"{stored_pair}, not {resolved_pair}. Refusing to resolve a different pair."
            )
        # Version before status, deliberately. A reviewer who lost a race holds
        # a stale version and must be told that, not handed the more general
        # complaint that the case is already resolved -- only the first tells
        # them to reload and decide again.
        if stored.version != expected_version:
            raise ReviewConflictError(
                f"Review case {resolved_case.review_case_id} is at version "
                f"{stored.version}, not the expected {expected_version}. Reload and "
                "re-authorize before deciding again.",
                review_case_id=resolved_case.review_case_id,
                expected_version=expected_version,
            )
        if stored.case.status is not ReviewStatus.PENDING:
            # A resolved case at the version the caller expected: not a race,
            # but a caller trying to overwrite a decision a human already made.
            raise PersistedCaseIntegrityError(
                f"Review case {resolved_case.review_case_id} is already "
                f"{stored.case.status.value}; a resolved case is terminal."
            )
        return stored

    def _compare_and_swap_case(
        self,
        connection: sqlite3.Connection,
        resolved_case: ReviewCase,
        expected_version: int,
        timestamp: str,
    ) -> None:
        """Update this queue's case only while it stands at ``expected_version``.

        The read above and this write share one IMMEDIATE transaction, so this
        cannot fail in practice -- but it is the guarantee that does not depend
        on the read having happened. rowcount is the entire verdict: anything
        but 1 means the row moved, and raising here rolls the whole transaction
        back, event included.

        The queue is part of the predicate, not an assumption. A review case id
        can name a row in another tenant's queue, and an UPDATE that matched it
        would apply one organization's decision to another's data.
        """
        cursor = connection.execute(
            _CAS_UPDATE_CASE,
            (
                resolved_case.status.value,
                case_payload_json(resolved_case),
                timestamp,
                self._review_queue_id,
                resolved_case.review_case_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise ReviewConflictError(
                f"Review case {resolved_case.review_case_id} was modified concurrently; "
                f"the conditional update at version {expected_version} matched "
                f"{cursor.rowcount} rows. Nothing was written.",
                review_case_id=resolved_case.review_case_id,
                expected_version=expected_version,
            )

    def _append_event(self, connection: sqlite3.Connection, event: ReviewEvent) -> None:
        """Append one history row to this queue. Never updates, never deletes.

        The composite foreign key on ``(review_queue_id, review_case_id)`` is
        the second guarantee here: even if this method were handed an event for
        a case in another queue, the row would be refused rather than written
        under the wrong tenant.
        """
        connection.execute(
            _INSERT_EVENT,
            event_to_row(event, review_queue_id=self._review_queue_id),
        )

    # -- advisory semantic suggestions --------------------------------------

    def record_semantic_suggestion(
        self,
        suggestion: SemanticSuggestion,
        *,
        now_utc: str | None = None,
    ) -> bool:
        """Store one advisory Sprint 09 suggestion. Never changes case state.

        Returns True when the suggestion was newly stored, False when an
        identical one was already present. Suggestion ids are content
        addresses, so a replay is an ordinary occurrence -- the same provider
        answering the same request twice -- and the first stored observation
        stays authoritative.

        "Identical" means the durable Sprint 09 payload, which excludes
        ``explanation``: that field is not retained, so it cannot make two
        recordings differ. An id arriving with a different *retained* field is
        refused (``SemanticSuggestionConflictError``); one of the two is then
        not what it claims to be, a stored suggestion is immutable, and
        persistence has no basis for preferring either.

        Replay detection is queue-local, because a suggestion id is a content
        address rather than a global name. Two queues reviewing structurally
        identical pairs can produce the same id for two genuinely different
        observations, and one tenant's stored row must never answer for
        another's. Nothing about the advisory contract changes: the suggestion
        is still immutable, still carries no human-decision token, still has no
        retained explanation, and still cannot touch ``review_cases``.

        No statement in this method touches ``review_cases``. The case is read
        only to verify that the suggestion really describes it.
        """
        assert_is_sprint_09_suggestion(suggestion)
        payload_json = canonical_suggestion_json(suggestion)

        with self._database.transaction() as connection:
            stored_case = self._select_case(connection, suggestion.review_case_id)
            if stored_case is None:
                raise ReviewCaseNotFoundError(f"Review case not found: {suggestion.review_case_id}")
            self._assert_suggestion_describes_case(suggestion, stored_case)

            existing = self._select_suggestion_row(connection, suggestion.suggestion_id)
            if existing is not None:
                self._assert_stored_suggestion_is_identical(existing, suggestion, payload_json)
                # Idempotent replay: no row, no event, no timestamp moves.
                return False

            timestamp = now_utc if now_utc is not None else utc_timestamp(self._clock)
            connection.execute(
                _INSERT_SUGGESTION,
                suggestion_to_row(
                    suggestion,
                    review_queue_id=self._review_queue_id,
                    schema_version=DATABASE_SCHEMA_VERSION,
                ),
            )
            self._append_event(
                connection,
                ReviewEvent.semantic_suggestion_recorded(
                    suggestion.review_case_id,
                    suggestion_id=suggestion.suggestion_id,
                    occurred_at_utc=timestamp,
                ),
            )
            return True

    def list_semantic_suggestions(self, review_case_id: str) -> tuple[SemanticSuggestion, ...]:
        """Return the advisory suggestions recorded for one case, oldest first.

        Returns Sprint 09 objects, never sqlite rows, carrying every retained
        field. ``explanation`` comes back empty because it was never stored; see
        ``review_persistence.sqlite.semantic_mapper``.

        Order is observation order and nothing more: the most recent suggestion
        is not the operative one, because none of them is operative.
        ``ReviewCase.status`` remains the only statement about what was decided.
        """
        rows = (
            self._database.connect()
            .execute(
                _SELECT_SUGGESTIONS + " AND review_case_id = ?" + _SUGGESTION_ORDER,
                (self._review_queue_id, review_case_id),
            )
            .fetchall()
        )
        return tuple(row_to_semantic_suggestion(row) for row in rows)

    @staticmethod
    def _assert_suggestion_describes_case(
        suggestion: SemanticSuggestion,
        stored: PersistedCase,
    ) -> None:
        """The suggestion must name this case and this ordered record pair.

        Record order is part of the identity, not a detail: ``RecordPair`` is
        ordered, and a suggestion filed against the reversed pair is a
        suggestion about a comparison that was never made.

        Case status is deliberately not checked. Late persistence of an
        immutable advisory suggestion is allowed: persistence does not infer
        when the suggestion was generated, and it never reopens or mutates a
        resolved ReviewCase. What is verified here is identity and nothing
        more.

        Separately, and as a fact about Sprint 09 rather than a guarantee about
        this method's input, ``evaluate_routing`` refuses to generate a new
        suggestion for a terminal case. That is why refusing late writes here
        would buy little while discarding an audit record; it is not evidence
        about any particular suggestion handed to the repository, which could
        have been produced by any caller.
        """
        case = stored.case
        if suggestion.review_case_id != case.review_case_id:
            raise SemanticSuggestionIntegrityError(
                f"Suggestion names review case {suggestion.review_case_id}, not "
                f"{case.review_case_id}."
            )
        stored_pair = (case.pair.record_a_id, case.pair.record_b_id)
        suggested_pair = (suggestion.record_a_id, suggestion.record_b_id)
        if suggested_pair != stored_pair:
            raise SemanticSuggestionIntegrityError(
                f"Suggestion for {case.review_case_id} describes records {suggested_pair}, "
                f"but the stored case is {stored_pair}. Record order is part of the "
                "reviewed pair's identity."
            )

    @staticmethod
    def _assert_stored_suggestion_is_identical(
        row: Mapping[str, Any],
        suggestion: SemanticSuggestion,
        payload_json: str,
    ) -> None:
        """One content address may name only one retained observation.

        Compared as canonical JSON over the durable Sprint 09 payload, which is
        every field this layer keeps -- identity, advisory value, provider,
        model, cost and the measurement fields. A replay differing in any of
        them is refused rather than merged: persistence cannot know which of two
        disagreeing observations is the real one, and overwriting an immutable
        audit record to find out is not an option.

        ``explanation`` is outside that comparison because it is not retained.
        Two observations differing only in their explanation are therefore the
        same persisted observation and the first row stands. Note this is a
        property of what persistence stores, not of the identifier: Sprint 09
        derives the suggestion id from request id, advisory value, attempt count
        and request fingerprint, and the explanation is not among them.
        """
        if str(row["suggestion_payload_json"]) == payload_json:
            return
        raise SemanticSuggestionConflictError(
            f"Semantic suggestion {suggestion.suggestion_id} is already stored with "
            "different content. Sprint 09 suggestion ids are content addresses, so this "
            "is a genuine contradiction; stored suggestions are immutable and are never "
            "overwritten."
        )

    def _select_suggestion_row(
        self,
        connection: sqlite3.Connection,
        suggestion_id: str,
    ) -> Mapping[str, Any] | None:
        return connection.execute(
            _SELECT_SUGGESTIONS + " AND suggestion_id = ?",
            (self._review_queue_id, suggestion_id),
        ).fetchone()

    # -- reads -------------------------------------------------------------

    def get_case(self, review_case_id: str) -> PersistedCase:
        """Return one of this queue's cases, or raise ``ReviewCaseNotFoundError``.

        A case id belonging to another queue is simply absent here, and is
        reported with the same error as an id that exists nowhere. The two are
        indistinguishable by construction rather than by policy, because the
        query cannot see outside its queue.
        """
        persisted = self._select_case(self._database.connect(), review_case_id)
        if persisted is None:
            raise ReviewCaseNotFoundError(f"Review case not found: {review_case_id}")
        return persisted

    def list_cases(self, *, status: ReviewStatus | None = None) -> tuple[PersistedCase, ...]:
        """Return this queue's cases in a deterministic order.

        Ordered by created_at_utc then review_case_id: registration order for
        a human reading the queue, with the deterministic id breaking ties
        between cases registered inside the same second. Another queue's cases
        are never counted, listed, or paged over.
        """
        return self._select_cases(self._database.connect(), status=status)

    def load_workflow_bundle(self) -> WorkflowBundle:
        """Load everything Sprint 08 MATCH authorization needs, as one snapshot.

        Returns every stored case -- resolved ones included, because a prior
        MATCH or NO_MATCH elsewhere in the component decides whether a new MATCH
        is allowed -- together with each case's persistence version, the entity
        records, and the reduced AUTO_MATCH snapshot.

        All three reads -- context, cases, and resolution history -- happen
        inside one DEFERRED read transaction, so they always describe the same
        committed database state. Reading the history separately could observe
        an event whose case update the reader had not yet seen, and the
        reconciliation below would report that as corruption.

        The audit trail and ``next_resolution_sequence`` come from
        ``reconstruct_history``, which fails closed when a stored case and its
        event disagree. Cases stored before any history existed are still
        readable; see ``review_application.history``.

        The bundle is exactly this queue's world, and that is what keeps Sprint
        08 authorization meaningful under tenancy. The boundary check projects a
        connected component across every AUTO_MATCH edge and every recorded
        human decision it is given; a bundle spanning two queues would let one
        organization's NO_MATCH forbid another's MATCH, and a bundle narrower
        than one queue would let an unsafe merge through.

        Raises ``ReviewWorkflowContextMissingError`` when this queue stores no
        context. Returning cases alone would hand the caller a graph missing
        its AUTO_MATCH edges and records, and a check that should fail closed
        would quietly pass.
        """
        with self._database.read_transaction() as connection:
            stored = self._select_context(connection)
            if stored is None:
                raise ReviewWorkflowContextMissingError(
                    f"Review queue {self._review_queue_id} stores no workflow "
                    "authorization context. Register a workflow before loading a bundle; "
                    "a partial bundle would weaken Sprint 08 MATCH authorization."
                )
            persisted_cases = self._select_cases(connection)
            events = self._select_events(connection)

        history = reconstruct_history(persisted_cases, events)
        return WorkflowBundle(
            persisted_cases=persisted_cases,
            entity_records=stored.entity_records(),
            resolution_snapshot=stored.resolution_snapshot,
            next_resolution_sequence=history.next_resolution_sequence,
            audit_entries=history.audit_entries,
            entity_resolution_config_path=stored.entity_resolution_config_path,
        )

    def list_events(self, review_case_id: str) -> tuple[ReviewEvent, ...]:
        """Return one case's append-only history, oldest first.

        Ordered by ``event_id`` because that is the order the rows were
        appended. Timestamps have second resolution, so two events recorded in
        the same second would otherwise have no defined order.

        Returns domain-shaped ``ReviewEvent`` values, never sqlite rows: every
        event-type invariant is re-checked on the way out, so a row edited
        outside this code cannot be read back as valid history.

        Scoped to this queue, so a history read can never splice in an event
        that another queue recorded against a case with the same id.
        """
        return self._select_events(self._database.connect(), review_case_id=review_case_id)

    def workflow_context(self) -> StoredWorkflowContext | None:
        """This queue's authorization context, or None when it stores none."""
        return self._select_context(self._database.connect())

    # -- row access --------------------------------------------------------

    def _select_case(
        self,
        connection: sqlite3.Connection,
        review_case_id: str,
    ) -> PersistedCase | None:
        row = connection.execute(
            _SELECT_CASES + " AND review_case_id = ?",
            (self._review_queue_id, review_case_id),
        ).fetchone()
        return None if row is None else row_to_persisted_case(row)

    def _select_cases(
        self,
        connection: sqlite3.Connection,
        *,
        status: ReviewStatus | None = None,
    ) -> tuple[PersistedCase, ...]:
        # The queue predicate is in _SELECT_CASES and is never optional; status
        # is the only thing a caller can add.
        sql = _SELECT_CASES
        parameters: tuple[str, ...] = (self._review_queue_id,)
        if status is not None:
            sql += " AND status = ?"
            parameters = (*parameters, status.value)
        rows = connection.execute(sql + _CASE_ORDER, parameters).fetchall()
        return tuple(row_to_persisted_case(row) for row in rows)

    def _select_events(
        self,
        connection: sqlite3.Connection,
        *,
        review_case_id: str | None = None,
    ) -> tuple[ReviewEvent, ...]:
        sql = _SELECT_EVENTS
        parameters: tuple[str, ...] = (self._review_queue_id,)
        if review_case_id is not None:
            sql += " AND review_case_id = ?"
            parameters = (*parameters, review_case_id)
        rows = connection.execute(sql + _EVENT_ORDER, parameters).fetchall()
        return tuple(row_to_review_event(row) for row in rows)

    def _select_context(self, connection: sqlite3.Connection) -> StoredWorkflowContext | None:
        row = connection.execute(_SELECT_CONTEXT, (self._review_queue_id,)).fetchone()
        if row is None:
            return None
        records_payload = decode_json_column(row["entity_records_json"], "entity_records_json")
        if not isinstance(records_payload, list):
            raise ReviewPersistenceError("Stored entity_records_json must be a JSON array.")
        return StoredWorkflowContext(
            entity_records_payload=[dict(item) for item in records_payload],
            resolution_snapshot=snapshot_from_payload(
                decode_json_column(row["resolution_snapshot_json"], "resolution_snapshot_json")
            ),
            entity_resolution_config_path=row["entity_resolution_config_path"],
            created_at_utc=str(row["created_at_utc"]),
            updated_at_utc=str(row["updated_at_utc"]),
        )
