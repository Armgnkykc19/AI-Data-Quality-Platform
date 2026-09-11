from __future__ import annotations

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import EntityRecord, MatchDecisionType, ResolutionResult
from human_review.authorization import assert_human_match_authorization_boundary
from human_review.errors import HumanReviewAuthorizationError, HumanReviewContradictionError
from human_review.models import HumanReviewOutcome, ReviewCase, ReviewStatus
from semantic_review.config import SemanticReviewConfig
from semantic_review.errors import SemanticReviewInputTooLargeError
from semantic_review.ids import estimate_token_count
from semantic_review.models import SemanticRoutingDecision, SemanticSkipReason
from semantic_review.prompt import SYSTEM_INSTRUCTIONS, build_user_message
from semantic_review.request_builder import build_semantic_review_request


def evaluate_routing(
    case: ReviewCase | None,
    *,
    config: SemanticReviewConfig,
    outcome: HumanReviewOutcome | None = None,
    resolution: ResolutionResult | None = None,
    records_by_id: dict[str, EntityRecord] | None = None,
    entity_resolution_config: EntityResolutionConfig | None = None,
    require_enabled: bool = True,
) -> SemanticRoutingDecision:
    if require_enabled and not config.enabled:
        return SemanticRoutingDecision(
            eligible=False,
            reason=SemanticSkipReason.LLM_DISABLED,
            detail="LLM assistance is disabled; no provider call is made.",
        )
    if case is None:
        return SemanticRoutingDecision(
            eligible=False,
            reason=SemanticSkipReason.CASE_NOT_FOUND,
            detail="Review case does not exist.",
        )
    if case.machine_decision != MatchDecisionType.REVIEW:
        return SemanticRoutingDecision(
            eligible=False,
            reason=SemanticSkipReason.NOT_REVIEW_DECISION,
            detail=f"Machine decision is {case.machine_decision.value}; LLM is REVIEW-only.",
        )
    if case.status == ReviewStatus.DEFERRED:
        return SemanticRoutingDecision(
            eligible=False,
            reason=SemanticSkipReason.CASE_TERMINAL,
            detail="Deferred cases are terminal for LLM assistance until a human reopens them.",
        )
    if case.resolution is not None or case.status in {
        ReviewStatus.MATCH,
        ReviewStatus.NO_MATCH,
    }:
        return SemanticRoutingDecision(
            eligible=False,
            reason=SemanticSkipReason.CASE_RESOLVED,
            detail=f"Review case is already {case.status.value}.",
        )
    if case.status != ReviewStatus.PENDING:
        return SemanticRoutingDecision(
            eligible=False,
            reason=SemanticSkipReason.CASE_TERMINAL,
            detail=f"Review case status {case.status.value} is not eligible.",
        )

    has_evidence = bool(
        case.supporting_evidence
        or case.conflicting_evidence
        or case.missing_evidence_notes
        or case.machine_reason
    )
    if not has_evidence:
        return SemanticRoutingDecision(
            eligible=False,
            reason=SemanticSkipReason.MISSING_EVIDENCE,
            detail="Required deterministic evidence is missing.",
        )

    if (
        outcome is not None
        and resolution is not None
        and records_by_id is not None
        and entity_resolution_config is not None
    ):
        try:
            assert_human_match_authorization_boundary(
                pair=case.pair,
                outcome=outcome,
                resolution=resolution,
                records_by_id=records_by_id,
                config=entity_resolution_config,
            )
        except (HumanReviewAuthorizationError, HumanReviewContradictionError) as exc:
            return SemanticRoutingDecision(
                eligible=False,
                reason=SemanticSkipReason.AUTHORIZATION_BLOCKED,
                detail=str(exc),
            )

    if records_by_id is not None:
        try:
            request = build_semantic_review_request(case, records_by_id, config=config)
        except SemanticReviewInputTooLargeError as exc:
            return SemanticRoutingDecision(
                eligible=False,
                reason=SemanticSkipReason.INPUT_TOO_LARGE,
                detail=str(exc),
            )
        except (KeyError, ValueError) as exc:
            return SemanticRoutingDecision(
                eligible=False,
                reason=SemanticSkipReason.UNSAFE_OR_INELIGIBLE_CASE,
                detail=str(exc),
            )
        estimated = estimate_token_count(SYSTEM_INSTRUCTIONS + build_user_message(request))
        if estimated > config.max_input_tokens:
            return SemanticRoutingDecision(
                eligible=False,
                reason=SemanticSkipReason.INPUT_TOO_LARGE,
                detail="Estimated prompt tokens exceed the configured input cap.",
            )

    return SemanticRoutingDecision(eligible=True, reason=None, detail="Eligible REVIEW case.")
