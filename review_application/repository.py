"""Persistence contract for the review queue.

Structural ``typing.Protocol``, matching the ``SemanticReviewProvider`` style
already used in Sprint 09. The contract is persistence-only: it stores
decisions that Sprint 08 domain logic has already produced and authorized, and
it exposes no primitive capable of creating one.

**Every method addresses exactly one review queue, and none of them names it.**
An implementation is bound to a queue when it is constructed and cannot reach
outside it; that is why no signature below carries a ``review_queue_id``, and
why adding one would be a mistake rather than an improvement. A per-call scope
parameter can be omitted, defaulted, or supplied from the wrong place while
still type-checking. A constructor binding cannot: there is no instance without
one.

The practical consequence is that tenancy is invisible here. ``review_case_id``
is unique within a queue and not beyond it -- ``stable_review_case_id`` derives
it from customer record identifiers, so two tenants producing the same value is
routine -- but a caller holding one of these never has to know, because it can
only ever see its own queue's rows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from entity_resolution.models import EntityRecord
from human_review.models import ReviewCase, ReviewStatus, ReviewWorkflowState
from review_application.models import PersistedCase, ReviewEvent, WorkflowBundle
from semantic_review.models import SemanticSuggestion


class ReviewCaseRepository(Protocol):
    """Storage operations for review cases, history, and semantic suggestions.

    Deliberately absent, and never to be added:

    * ``force_match`` / ``force_no_match`` / ``resolve_without_authorization``
    * ``authorize_match`` — authorization belongs to
      ``human_review.authorization``
    * ``set_status`` / ``update_status`` — a status may only change as part of
      :meth:`apply_resolution`, which requires a domain-produced case and audit
      event
    * a generic ``append_event`` — it would let a caller write a MATCH history
      row without ever applying a resolution
    """

    def unit_of_work(self) -> AbstractContextManager[object]:
        """Hold one serialized write scope across several calls below.

        The reason this exists is narrow and is the whole of Sprint 14 Phase A's
        concurrency fix. Sprint 08 MATCH authorization is a property of the
        queue, not of the reviewed pair: it projects a connected component
        across every AUTO_MATCH edge and every recorded human decision. So a
        resolution is three steps -- load the bundle, ask the domain, write the
        result -- and the authorization it performed is only valid if the queue
        did not move between the first step and the third.

        Target-case ``expected_version`` does not establish that. It proves the
        *one* row being written has not changed, which is lost-update
        protection and nothing more. Two writers resolving two *different*
        cases in one component each pass their own version check, and the
        combined state can be one the domain would have refused if it had been
        asked once, in order.

        An implementation must therefore make the enclosed calls observe and
        write one serialized state: whatever :meth:`load_workflow_bundle`
        returns inside this scope must still be true when
        :meth:`apply_resolution` commits inside the same scope, and a
        concurrent writer must not be able to interleave. Committing the whole
        scope atomically is what satisfies that; a nested implementation must
        join the outer scope rather than open a second one.

        Reentrant, so a repository method that takes its own transaction stays
        correct whether or not it was called from inside one.

        The yielded value is deliberately opaque. Callers use this to bound a
        scope, never to obtain a connection, a cursor, or anything else that
        would let the application layer write SQL.
        """
        ...

    def register_workflow(
        self,
        state: ReviewWorkflowState,
        *,
        entity_records: Sequence[EntityRecord],
        resolution_snapshot: Mapping[str, Any],
        entity_resolution_config_path: str | None,
        now_utc: str,
    ) -> tuple[PersistedCase, ...]:
        """Idempotently store generated cases together with their ER context.

        Cases and their authorization context are registered as one unit
        because ``generate_review_cases`` always produces them together, and a
        case whose records are absent would make MATCH authorization fail
        closed later.

        Re-registering an already stored deterministic case is a no-op. A
        stored case is never reset to PENDING and its version is never
        rewound; that would silently discard a human decision.

        Both the idempotence and the conflict are properties of this queue
        alone. An identical context in another queue is unrelated, and a
        changed context conflicts only where one is already stored.
        """
        ...

    def get_case(self, review_case_id: str) -> PersistedCase:
        """Return one stored case with its persistence metadata.

        Raises ``ReviewCaseNotFoundError`` when the case is absent.
        """
        ...

    def list_cases(
        self,
        *,
        status: ReviewStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[PersistedCase, ...]:
        """Return stored cases, optionally filtered by review status.

        ``limit``/``offset`` page the already-filtered, already-ordered set.
        ``limit is None`` returns every matching case. Ordering is the
        repository's, never the caller's.
        """
        ...

    def count_cases(self, *, status: ReviewStatus | None = None) -> int:
        """Count stored cases, using the same filter ``list_cases`` would."""
        ...

    def load_workflow_bundle(self) -> WorkflowBundle:
        """Load the complete material Sprint 08 authorization requires.

        Must read a consistent snapshot of every case, every entity record, and
        the reduced AUTO_MATCH resolution snapshot *in this queue*. Never
        narrow this to a single case: ``assert_human_match_authorization_boundary``
        projects component membership transitively, and a partial load would
        weaken the check without failing.

        Never widen it past the queue either. The queue is one authorization
        graph; a bundle spanning two would let one tenant's recorded NO_MATCH
        forbid a merge in another's data.
        """
        ...

    def apply_resolution(
        self,
        resolved_case: ReviewCase,
        *,
        expected_version: int,
        event: ReviewEvent,
        now_utc: str,
    ) -> PersistedCase:
        """Persist a decision that ``ReviewWorkflow.resolve_case`` already made.

        ``resolved_case`` is the domain object the workflow returned, and
        ``event`` must come from :meth:`ReviewEvent.from_audit_entry`, so no
        status string is ever accepted from a caller. The case update and the
        history append commit together or not at all.

        Raises ``ReviewConflictError`` when the stored version no longer equals
        ``expected_version``; nothing is written in that case.
        """
        ...

    def list_events(self, review_case_id: str) -> tuple[ReviewEvent, ...]:
        """Return the append-only history for one case in append order."""
        ...

    def record_semantic_suggestion(
        self,
        suggestion: SemanticSuggestion,
        *,
        now_utc: str,
    ) -> bool:
        """Store an advisory Sprint 09 suggestion. Never changes case state.

        Keyed by the content-addressed Sprint 09 ``suggestion_id``, so storing
        the same suggestion twice is an idempotent no-op and the first
        observation stays authoritative for ``created_at_utc``. Returns True
        when the suggestion was newly stored.
        """
        ...

    def list_semantic_suggestions(self, review_case_id: str) -> tuple[SemanticSuggestion, ...]:
        """Return stored advisory suggestions for one case."""
        ...
