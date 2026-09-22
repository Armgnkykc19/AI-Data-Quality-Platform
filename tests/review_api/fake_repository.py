"""A repository that answers reads and detonates on writes.

The read methods are lookups over pre-seeded tuples -- no filtering logic, no
ordering logic, no domain behaviour. Reimplementing any of that here would mean
the route tests were passing against a second, simpler system than the real one.

Every authority method raises ``AssertionError``. That is the point of the
class: a GET route that ever resolved, registered, or recorded anything would
fail loudly and immediately, in the test that touched it, instead of quietly
mutating a queue. The same applies to ``load_workflow_bundle`` -- a read route
that needed the authorization graph would be doing something a read route has
no business doing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from entity_resolution.models import EntityRecord
from human_review.errors import ReviewCaseNotFoundError
from human_review.models import ReviewCase, ReviewStatus, ReviewWorkflowState
from review_application import PersistedCase, ReviewEvent
from semantic_review.models import SemanticSuggestion


class FakeReviewCaseRepository:
    """Structurally a ``ReviewCaseRepository``; behaviourally a read fixture."""

    def __init__(
        self,
        *,
        cases: Sequence[PersistedCase] = (),
        events: Mapping[str, Sequence[ReviewEvent]] | None = None,
        suggestions: Mapping[str, Sequence[SemanticSuggestion]] | None = None,
        read_error: Exception | None = None,
    ) -> None:
        self._cases = tuple(cases)
        self._events = {key: tuple(value) for key, value in (events or {}).items()}
        self._suggestions = {key: tuple(value) for key, value in (suggestions or {}).items()}
        # When set, every read raises it. Used to prove that a storage or
        # integrity failure reaches the client as a static envelope.
        self._read_error = read_error
        # What the route actually asked for, so a test can assert the status
        # filter crossed the boundary as the enum and not as a string.
        self.list_cases_calls: list[ReviewStatus | None] = []

    # -- reads --------------------------------------------------------------

    def _maybe_fail(self) -> None:
        if self._read_error is not None:
            raise self._read_error

    def get_case(self, review_case_id: str) -> PersistedCase:
        self._maybe_fail()
        for persisted in self._cases:
            if persisted.review_case_id == review_case_id:
                return persisted
        raise ReviewCaseNotFoundError(f"Review case not found: {review_case_id}")

    def list_cases(
        self,
        *,
        status: ReviewStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[PersistedCase, ...]:
        self._maybe_fail()
        self.list_cases_calls.append(status)
        matching = (
            self._cases
            if status is None
            else tuple(persisted for persisted in self._cases if persisted.status == status)
        )
        if limit is None:
            return matching
        return matching[offset : offset + limit]

    def count_cases(self, *, status: ReviewStatus | None = None) -> int:
        self._maybe_fail()
        if status is None:
            return len(self._cases)
        return sum(1 for persisted in self._cases if persisted.status == status)

    def list_events(self, review_case_id: str) -> tuple[ReviewEvent, ...]:
        self._maybe_fail()
        # Returns () for an unknown case, exactly like the SQLite repository.
        # The routes must not rely on this raising.
        return self._events.get(review_case_id, ())

    def list_semantic_suggestions(self, review_case_id: str) -> tuple[SemanticSuggestion, ...]:
        self._maybe_fail()
        return self._suggestions.get(review_case_id, ())

    # -- authority: never reachable from a read endpoint ---------------------

    def register_workflow(
        self,
        state: ReviewWorkflowState,
        *,
        entity_records: Sequence[EntityRecord],
        resolution_snapshot: Mapping[str, Any],
        entity_resolution_config_path: str | None,
        now_utc: str,
    ) -> tuple[PersistedCase, ...]:
        raise AssertionError("A read endpoint registered a workflow.")

    def load_workflow_bundle(self) -> Any:
        raise AssertionError("A read endpoint loaded the authorization bundle.")

    def apply_resolution(
        self,
        resolved_case: ReviewCase,
        *,
        expected_version: int,
        event: ReviewEvent,
        now_utc: str,
    ) -> PersistedCase:
        raise AssertionError("A read endpoint applied a resolution.")

    def record_semantic_suggestion(
        self,
        suggestion: SemanticSuggestion,
        *,
        now_utc: str,
    ) -> bool:
        raise AssertionError("A read endpoint recorded a semantic suggestion.")
