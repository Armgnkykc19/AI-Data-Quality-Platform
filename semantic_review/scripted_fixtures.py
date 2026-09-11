"""Deterministic offline fixtures for formula and safety tests. Not model quality."""

from __future__ import annotations

from dataclasses import dataclass

from entity_resolution.models import (
    BlockingReasonType,
    CandidateReason,
    EntityRecord,
    EvidenceType,
    MatchCandidate,
    MatchDecision,
    MatchDecisionType,
    PairComparison,
    PairEvidence,
    RecordPair,
    ResolutionResult,
    ResolutionSummary,
    ReviewItem,
)
from semantic_review.models import SemanticSkipReason, SemanticSuggestionType
from semantic_review.providers.fake_provider import (
    FixedSuggestionProvider,
    invalid_schema_provider,
    timeout_provider,
)


def _make_record(record_id: str, email: str) -> EntityRecord:
    return EntityRecord(
        record_id=record_id,
        source_name="source_a",
        field_values={
            "first_name": "Ali",
            "last_name": "Yilmaz",
            "email": email,
            "phone": "+905321111111",
            "company": "Acme",
            "city": "Istanbul",
            "district": "Kadikoy",
            "address": "Test Sokak",
        },
    )


def make_scripted_resolution(
    left_id: str,
    right_id: str,
    *,
    shared_email: bool,
) -> ResolutionResult:
    pair = RecordPair.ordered(left_id, right_id)
    evidence = (
        PairEvidence(
            evidence_type=EvidenceType.FIRST_NAME_SIMILARITY,
            field_name="first_name",
            value=0.91,
            weight=0.10,
            contribution=0.09,
            strength="similarity",
            description="Similar first names.",
        ),
        PairEvidence(
            evidence_type=EvidenceType.LAST_NAME_EXACT,
            field_name="last_name",
            value=1.0,
            weight=0.12,
            contribution=0.12,
            strength="exact",
            description="Exact last name.",
        ),
    )
    comparison = PairComparison(
        pair=pair,
        candidate_reasons=(
            CandidateReason(
                reason_type=BlockingReasonType.NAME_SURNAME_BLOCK,
                blocking_key="ali|yilmaz",
                description="Shared blocking key.",
            ),
        ),
        evidence=evidence,
        conflicts=(),
        score=0.86,
    )
    decision = MatchDecision(
        pair=pair,
        comparison=comparison,
        decision=MatchDecisionType.REVIEW,
        reason="Only weak or fuzzy evidence present; unsafe for AUTO_MATCH.",
    )
    review_item = ReviewItem(
        pair=pair,
        score=0.86,
        decision=MatchDecisionType.REVIEW,
        evidence=evidence,
        conflicts=(),
        candidate_reasons=comparison.candidate_reasons,
        reason=decision.reason,
    )
    left_email = "shared@example.com" if shared_email else "a@example.com"
    right_email = "shared@example.com" if shared_email else "b@example.com"
    return ResolutionResult(
        source_label="scripted",
        records=(
            _make_record(left_id, left_email),
            _make_record(right_id, right_email),
        ),
        candidates=(MatchCandidate(pair=pair, reasons=comparison.candidate_reasons),),
        decisions=(decision,),
        review_queue=(review_item,),
        clusters=(),
        summary=ResolutionSummary(
            record_count=2,
            possible_pair_count=1,
            candidate_pair_count=1,
            candidate_reduction_ratio=0.0,
            auto_match_count=0,
            review_count=1,
            no_match_count=0,
            cluster_count=0,
            conflict_guarded_clusters=0,
        ),
    )


@dataclass(frozen=True)
class ScriptedScenario:
    name: str
    resolution: ResolutionResult
    provider_factory: str
    expected_suggestion: SemanticSuggestionType | None
    same_person: bool | None
    expected_skip_reason: SemanticSkipReason | None


def build_scripted_scenarios() -> tuple[ScriptedScenario, ...]:
    return (
        ScriptedScenario(
            name="correct_suggest_match",
            resolution=make_scripted_resolution("m1a", "m1b", shared_email=True),
            provider_factory="match",
            expected_suggestion=SemanticSuggestionType.SUGGEST_MATCH,
            same_person=True,
            expected_skip_reason=None,
        ),
        ScriptedScenario(
            name="incorrect_suggest_match",
            resolution=make_scripted_resolution("m2a", "m2b", shared_email=True),
            provider_factory="match",
            expected_suggestion=SemanticSuggestionType.SUGGEST_MATCH,
            same_person=False,
            expected_skip_reason=None,
        ),
        ScriptedScenario(
            name="correct_suggest_no_match",
            resolution=make_scripted_resolution("n1a", "n1b", shared_email=True),
            provider_factory="no_match",
            expected_suggestion=SemanticSuggestionType.SUGGEST_NO_MATCH,
            same_person=False,
            expected_skip_reason=None,
        ),
        ScriptedScenario(
            name="incorrect_suggest_no_match",
            resolution=make_scripted_resolution("n2a", "n2b", shared_email=True),
            provider_factory="no_match",
            expected_suggestion=SemanticSuggestionType.SUGGEST_NO_MATCH,
            same_person=True,
            expected_skip_reason=None,
        ),
        ScriptedScenario(
            name="insufficient_evidence",
            resolution=make_scripted_resolution("i1a", "i1b", shared_email=True),
            provider_factory="insufficient",
            expected_suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
            same_person=True,
            expected_skip_reason=None,
        ),
        ScriptedScenario(
            name="provider_failure",
            resolution=make_scripted_resolution("f1a", "f1b", shared_email=True),
            provider_factory="timeout",
            expected_suggestion=SemanticSuggestionType.PROVIDER_FAILURE,
            same_person=True,
            expected_skip_reason=None,
        ),
        ScriptedScenario(
            name="invalid_structured_output",
            resolution=make_scripted_resolution("v1a", "v1b", shared_email=True),
            provider_factory="invalid",
            expected_suggestion=SemanticSuggestionType.PROVIDER_FAILURE,
            same_person=True,
            expected_skip_reason=None,
        ),
        ScriptedScenario(
            name="authorization_blocked",
            resolution=make_scripted_resolution("b1a", "b1b", shared_email=False),
            provider_factory="match",
            expected_suggestion=None,
            same_person=False,
            expected_skip_reason=SemanticSkipReason.AUTHORIZATION_BLOCKED,
        ),
    )


def provider_for_factory(name: str, pricing, model: str) -> FixedSuggestionProvider:
    if name == "match":
        return FixedSuggestionProvider(
            suggestion=SemanticSuggestionType.SUGGEST_MATCH,
            reason_codes=("EMAIL_EXACT",),
            explanation="Scripted match suggestion.",
            pricing=pricing,
            requested_model=model,
        )
    if name == "no_match":
        return FixedSuggestionProvider(
            suggestion=SemanticSuggestionType.SUGGEST_NO_MATCH,
            reason_codes=("NAME_ONLY",),
            explanation="Scripted no-match suggestion.",
            pricing=pricing,
            requested_model=model,
        )
    if name == "insufficient":
        return FixedSuggestionProvider(
            suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
            reason_codes=("WEAK_EVIDENCE",),
            explanation="Scripted abstention.",
            pricing=pricing,
            requested_model=model,
        )
    if name == "timeout":
        return timeout_provider(pricing, model)
    if name == "invalid":
        return invalid_schema_provider(pricing, model)
    raise ValueError(f"Unknown scripted provider factory '{name}'.")
