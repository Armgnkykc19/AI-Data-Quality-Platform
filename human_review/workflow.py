from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from human_review.authorization import assert_human_match_authorization_boundary
from human_review.errors import (
    HumanReviewAuthorizationContextError,
    InvalidReviewTransitionError,
    ReviewCaseNotFoundError,
)
from human_review.models import (
    HumanReviewDecision,
    HumanReviewOutcome,
    ReviewAuditEntry,
    ReviewCase,
    ReviewResolution,
    ReviewStatus,
    ReviewWorkflowState,
)

if TYPE_CHECKING:
    from entity_resolution.config import EntityResolutionConfig
    from entity_resolution.models import EntityRecord, ResolutionResult


def _downstream_action(decision: HumanReviewDecision) -> str:
    if decision == HumanReviewDecision.MATCH:
        return "eligible_for_human_confirmed_canonical_merge"
    if decision == HumanReviewDecision.NO_MATCH:
        return "explicit_no_match_constraint_recorded"
    return "remain_excluded_from_unsafe_canonical_merge"


def _status_for_decision(decision: HumanReviewDecision) -> ReviewStatus:
    if decision == HumanReviewDecision.MATCH:
        return ReviewStatus.MATCH
    if decision == HumanReviewDecision.NO_MATCH:
        return ReviewStatus.NO_MATCH
    return ReviewStatus.DEFERRED


class ReviewWorkflow:
    def __init__(self, state: ReviewWorkflowState) -> None:
        self._state = state

    @property
    def state(self) -> ReviewWorkflowState:
        return self._state

    def list_cases(self) -> tuple[ReviewCase, ...]:
        return self._state.cases

    def get_case(self, review_case_id: str) -> ReviewCase:
        case = self._state.case_by_id(review_case_id)
        if case is None:
            raise ReviewCaseNotFoundError(f"Review case not found: {review_case_id}")
        return case

    def resolve_case(
        self,
        review_case_id: str,
        *,
        decision: HumanReviewDecision,
        reviewer_id: str | None = None,
        resolution: ResolutionResult | None = None,
        records_by_id: dict[str, EntityRecord] | None = None,
        entity_resolution_config: EntityResolutionConfig | None = None,
    ) -> ReviewWorkflowState:
        case = self.get_case(review_case_id)
        if case.status != ReviewStatus.PENDING:
            raise InvalidReviewTransitionError(
                f"Review case {review_case_id} is already {case.status.value}; "
                "only PENDING cases may be resolved."
            )

        if decision == HumanReviewDecision.MATCH:
            missing = []
            if resolution is None:
                missing.append("resolution")
            if not records_by_id:
                missing.append("records_by_id")
            if entity_resolution_config is None:
                missing.append("entity_resolution_config")
            if missing:
                raise HumanReviewAuthorizationContextError(
                    "Human MATCH requires full entity-resolution authorization context "
                    f"({', '.join(missing)} missing). Fail closed; MATCH not applied."
                )
            if (
                case.pair.record_a_id not in records_by_id
                or case.pair.record_b_id not in records_by_id
            ):
                raise HumanReviewAuthorizationContextError(
                    "Human MATCH requires both reviewed records in records_by_id. "
                    "Fail closed; MATCH not applied."
                )
            assert_human_match_authorization_boundary(
                pair=case.pair,
                outcome=self.to_outcome(),
                resolution=resolution,
                records_by_id=records_by_id,
                config=entity_resolution_config,
            )
        resolution = ReviewResolution(
            review_case_id=case.review_case_id,
            human_decision=decision,
            reviewer_id=reviewer_id,
            resolution_sequence=self._state.next_resolution_sequence,
            machine_decision=case.machine_decision,
            machine_reason=case.machine_reason,
            downstream_action=_downstream_action(decision),
        )
        updated_case = replace(
            case,
            status=_status_for_decision(decision),
            resolution=resolution,
        )
        audit_entry = ReviewAuditEntry(
            review_case_id=case.review_case_id,
            record_a_id=case.pair.record_a_id,
            record_b_id=case.pair.record_b_id,
            machine_decision=case.machine_decision.value,
            machine_reason=case.machine_reason,
            human_decision=decision.value,
            reviewer_id=reviewer_id,
            resolution_sequence=resolution.resolution_sequence,
            downstream_action=resolution.downstream_action,
        )

        updated_cases = tuple(
            updated_case if item.review_case_id == review_case_id else item
            for item in self._state.cases
        )
        self._state = ReviewWorkflowState(
            cases=updated_cases,
            audit_trail=self._state.audit_trail + (audit_entry,),
            next_resolution_sequence=self._state.next_resolution_sequence + 1,
        )
        return self._state

    def to_outcome(self) -> HumanReviewOutcome:
        return HumanReviewOutcome(workflow_state=self._state)
