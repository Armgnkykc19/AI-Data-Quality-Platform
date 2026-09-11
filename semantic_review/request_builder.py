from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from entity_resolution.models import EntityRecord, MatchDecisionType
from human_review.models import ReviewCase, ReviewStatus
from semantic_review.config import SemanticReviewConfig
from semantic_review.errors import SemanticReviewInputTooLargeError
from semantic_review.ids import canonical_json_digest, estimate_token_count, stable_request_id
from semantic_review.models import (
    BoundedFieldValue,
    SemanticEvidenceItem,
    SemanticRecordView,
    SemanticReviewRequest,
)
from semantic_review.prompt import SYSTEM_INSTRUCTIONS, build_user_message, prompt_template_hash

ALLOWED_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "company",
    "city",
    "district",
    "address",
)

FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "person_id",
        "expected_person_id",
        "oracle",
        "oracle_label",
        "ground_truth",
        "expected_decision",
        "expected_normalized_value",
        "hard_positive",
        "hard_negative",
        "corruption",
        "final_holdout",
        "holdout",
        "generator",
        "generator_metadata",
        "dataset_split",
        "source_path",
        "source_file_path",
        "auto_match_threshold",
        "review_threshold",
    }
)

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


def _bounded_field(field_name: str, raw: str | None, *, max_chars: int) -> BoundedFieldValue:
    if raw is None:
        return BoundedFieldValue(
            field_name=field_name, presence="missing", value=None, truncated=False
        )
    if raw == "":
        return BoundedFieldValue(field_name=field_name, presence="empty", value="", truncated=False)
    truncated = len(raw) > max_chars
    value = raw[:max_chars]
    return BoundedFieldValue(
        field_name=field_name,
        presence="present",
        value=value,
        truncated=truncated,
    )


def _record_view(record: EntityRecord, *, max_chars: int) -> SemanticRecordView:
    fields = tuple(
        _bounded_field(field_name, record.get(field_name), max_chars=max_chars)
        for field_name in ALLOWED_FIELDS
    )
    return SemanticRecordView(record_id=record.record_id, fields=fields)


def assert_no_ground_truth_leakage(payload: dict) -> None:
    stack: list[object] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                lowered = str(key).lower()
                if lowered in FORBIDDEN_PAYLOAD_KEYS or any(
                    token in lowered for token in FORBIDDEN_PAYLOAD_KEYS
                ):
                    raise ValueError(
                        f"Ground-truth or generator metadata leaked into LLM request: {key}"
                    )
                stack.append(value)
        elif isinstance(current, list | tuple):
            stack.extend(current)


def build_semantic_review_request(
    case: ReviewCase,
    records_by_id: dict[str, EntityRecord],
    *,
    config: SemanticReviewConfig,
    clock: Clock = _now,
) -> SemanticReviewRequest:
    if case.machine_decision != MatchDecisionType.REVIEW:
        raise ValueError("Only REVIEW cases may be converted into LLM requests.")
    if case.status != ReviewStatus.PENDING:
        raise ValueError("Only unresolved PENDING review cases may be converted into LLM requests.")
    if case.pair.record_a_id not in records_by_id or case.pair.record_b_id not in records_by_id:
        raise KeyError("Required reviewed records are missing from records_by_id.")

    record_a = records_by_id[case.pair.record_a_id]
    record_b = records_by_id[case.pair.record_b_id]
    supporting = tuple(
        SemanticEvidenceItem(
            kind="supporting",
            field_name=item.field_name,
            code=item.evidence_type,
            strength=item.strength,
            description=item.description[: config.max_field_chars],
        )
        for item in case.supporting_evidence
    )
    conflicting = tuple(
        SemanticEvidenceItem(
            kind="conflicting",
            field_name=item.field_name,
            code=item.conflict_type,
            strength=item.severity,
            description=item.description[: config.max_field_chars],
        )
        for item in case.conflicting_evidence
    )
    missing_notes = tuple(note[: config.max_field_chars] for note in case.missing_evidence_notes)
    request = SemanticReviewRequest(
        request_id="pending",
        review_case_id=case.review_case_id,
        record_a_id=case.pair.record_a_id,
        record_b_id=case.pair.record_b_id,
        record_a=_record_view(record_a, max_chars=config.max_field_chars),
        record_b=_record_view(record_b, max_chars=config.max_field_chars),
        machine_decision=case.machine_decision,
        review_status=case.status,
        machine_score=case.machine_score,
        machine_reason=case.machine_reason[: config.max_field_chars],
        supporting_evidence=supporting,
        conflicting_evidence=conflicting,
        missing_evidence_notes=missing_notes,
        prompt_version=config.prompt_version,
        prompt_template_hash=prompt_template_hash(),
        request_schema_version=config.request_schema_version,
        response_schema_version=config.response_schema_version,
        request_fingerprint="pending",
        created_at_utc=clock().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    fingerprint = canonical_json_digest(request.fingerprint_payload())
    request_id = stable_request_id(fingerprint)
    request = SemanticReviewRequest(
        **{
            **request.__dict__,
            "request_id": request_id,
            "request_fingerprint": fingerprint,
        }
    )
    payload = request.to_dict()
    assert_no_ground_truth_leakage(payload)
    estimated = estimate_token_count(SYSTEM_INSTRUCTIONS + build_user_message(request))
    if estimated > config.max_input_tokens:
        raise SemanticReviewInputTooLargeError(
            f"Estimated input tokens {estimated} exceed max_input_tokens {config.max_input_tokens}."
        )
    return request
