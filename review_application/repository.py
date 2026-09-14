"""Persistence contract for the review queue.

Structural ``typing.Protocol``, matching the ``SemanticReviewProvider`` style
already used in Sprint 09. The contract is persistence-only: it stores
decisions that Sprint 08 domain logic has already produced and authorized, and
it exposes no primitive capable of creating one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
        """
        ...

    def get_case(self, review_case_id: str) -> PersistedCase:
        """Return one stored case with its persistence metadata.

        Raises ``ReviewCaseNotFoundError`` when the case is absent.
        """
        ...

    def list_cases(self, *, status: ReviewStatus | None = None) -> tuple[PersistedCase, ...]:
        """Return stored cases, optionally filtered by review status."""
        ...

    def load_workflow_bundle(self) -> WorkflowBundle:
        """Load the complete material Sprint 08 authorization requires.

        Must read a consistent snapshot of every case, every entity record, and
        the reduced AUTO_MATCH resolution snapshot. Never narrow this to a
        single case: ``assert_human_match_authorization_boundary`` projects
        component membership transitively, and a partial load would weaken the
        check without failing.
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
