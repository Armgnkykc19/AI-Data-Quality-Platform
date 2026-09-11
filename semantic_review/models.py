from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from entity_resolution.models import MatchDecisionType
from human_review.models import ReviewStatus


class SemanticSuggestionType(StrEnum):
    SUGGEST_MATCH = "SUGGEST_MATCH"
    SUGGEST_NO_MATCH = "SUGGEST_NO_MATCH"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


class CostMode(StrEnum):
    SIMULATED = "SIMULATED"
    LIVE = "LIVE"
    UNKNOWN = "UNKNOWN"


FORBIDDEN_HUMAN_DECISIONS = frozenset({"MATCH", "NO_MATCH", "DEFERRED", "DEFER"})


class SemanticFailureCode(StrEnum):
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_STRUCTURED_OUTPUT = "INVALID_STRUCTURED_OUTPUT"
    RESPONSE_ID_MISMATCH = "RESPONSE_ID_MISMATCH"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    UNSAFE_OR_INELIGIBLE_CASE = "UNSAFE_OR_INELIGIBLE_CASE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class SemanticSkipReason(StrEnum):
    LLM_DISABLED = "LLM_DISABLED"
    NOT_REVIEW_DECISION = "NOT_REVIEW_DECISION"
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    CASE_RESOLVED = "CASE_RESOLVED"
    CASE_TERMINAL = "CASE_TERMINAL"
    AUTHORIZATION_BLOCKED = "AUTHORIZATION_BLOCKED"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    UNSAFE_OR_INELIGIBLE_CASE = "UNSAFE_OR_INELIGIBLE_CASE"


FieldPresence = Literal["missing", "empty", "present"]


@dataclass(frozen=True)
class BoundedFieldValue:
    field_name: str
    presence: FieldPresence
    value: str | None
    truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "presence": self.presence,
            "value": self.value,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class SemanticEvidenceItem:
    kind: str
    field_name: str
    code: str
    strength: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "field_name": self.field_name,
            "code": self.code,
            "strength": self.strength,
            "description": self.description,
        }


@dataclass(frozen=True)
class SemanticRecordView:
    record_id: str
    fields: tuple[BoundedFieldValue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "fields": [item.to_dict() for item in self.fields],
        }


@dataclass(frozen=True)
class SemanticReviewRequest:
    request_id: str
    review_case_id: str
    record_a_id: str
    record_b_id: str
    record_a: SemanticRecordView
    record_b: SemanticRecordView
    machine_decision: MatchDecisionType
    review_status: ReviewStatus
    machine_score: float
    machine_reason: str
    supporting_evidence: tuple[SemanticEvidenceItem, ...]
    conflicting_evidence: tuple[SemanticEvidenceItem, ...]
    missing_evidence_notes: tuple[str, ...]
    prompt_version: str
    prompt_template_hash: str
    request_schema_version: str
    response_schema_version: str
    request_fingerprint: str
    created_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "review_case_id": self.review_case_id,
            "record_a_id": self.record_a_id,
            "record_b_id": self.record_b_id,
            "record_a": self.record_a.to_dict(),
            "record_b": self.record_b.to_dict(),
            "machine_decision": self.machine_decision.value,
            "review_status": self.review_status.value,
            "machine_score": round(self.machine_score, 6),
            "machine_reason": self.machine_reason,
            "supporting_evidence": [item.to_dict() for item in self.supporting_evidence],
            "conflicting_evidence": [item.to_dict() for item in self.conflicting_evidence],
            "missing_evidence_notes": list(self.missing_evidence_notes),
            "prompt_version": self.prompt_version,
            "prompt_template_hash": self.prompt_template_hash,
            "request_schema_version": self.request_schema_version,
            "response_schema_version": self.response_schema_version,
            "request_fingerprint": self.request_fingerprint,
            "created_at_utc": self.created_at_utc,
        }

    def fingerprint_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("created_at_utc", None)
        payload.pop("request_id", None)
        payload.pop("request_fingerprint", None)
        return payload


@dataclass(frozen=True)
class SemanticSuggestion:
    suggestion_id: str
    request_id: str
    review_case_id: str
    record_a_id: str
    record_b_id: str
    suggestion: SemanticSuggestionType
    reason_codes: tuple[str, ...]
    explanation: str
    provider: str
    requested_model: str
    returned_model: str | None
    model_snapshot: str | None
    provider_request_id: str | None
    prompt_version: str
    prompt_template_hash: str
    request_schema_version: str
    response_schema_version: str
    request_fingerprint: str
    attempt_count: int
    latency_ms: int
    input_token_count: int
    cached_input_token_count: int
    output_token_count: int
    reasoning_token_count: int
    estimated_cost_usd: float | None
    actual_provider_cost_usd: float | None
    hypothetical_model_cost_usd: float | None
    cost_mode: CostMode
    usage_available: bool
    pricing_version: str
    failure_code: SemanticFailureCode | None
    created_at_utc: str
    live: bool
    result_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "request_id": self.request_id,
            "review_case_id": self.review_case_id,
            "record_a_id": self.record_a_id,
            "record_b_id": self.record_b_id,
            "suggestion": self.suggestion.value,
            "reason_codes": list(self.reason_codes),
            "provider": self.provider,
            "requested_model": self.requested_model,
            "returned_model": self.returned_model,
            "model_snapshot": self.model_snapshot,
            "provider_request_id": self.provider_request_id,
            "prompt_version": self.prompt_version,
            "prompt_template_hash": self.prompt_template_hash,
            "request_schema_version": self.request_schema_version,
            "response_schema_version": self.response_schema_version,
            "request_fingerprint": self.request_fingerprint,
            "attempt_count": self.attempt_count,
            "latency_ms": self.latency_ms,
            "input_token_count": self.input_token_count,
            "cached_input_token_count": self.cached_input_token_count,
            "output_token_count": self.output_token_count,
            "reasoning_token_count": self.reasoning_token_count,
            "estimated_cost_usd": self.estimated_cost_usd,
            "actual_provider_cost_usd": self.actual_provider_cost_usd,
            "hypothetical_model_cost_usd": self.hypothetical_model_cost_usd,
            "cost_mode": self.cost_mode.value,
            "usage_available": self.usage_available,
            "pricing_version": self.pricing_version,
            "failure_code": None if self.failure_code is None else self.failure_code.value,
            "created_at_utc": self.created_at_utc,
            "live": self.live,
            "result_source": self.result_source,
        }


@dataclass(frozen=True)
class SemanticRoutingDecision:
    eligible: bool
    reason: SemanticSkipReason | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": None if self.reason is None else self.reason.value,
            "detail": self.detail,
        }
