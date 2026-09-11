from __future__ import annotations

import json
from typing import Any

from semantic_review.errors import (
    SemanticReviewIdentityMismatchError,
    SemanticReviewInvalidOutputError,
)
from semantic_review.models import (
    FORBIDDEN_HUMAN_DECISIONS,
    SemanticSuggestionType,
)

REQUEST_SCHEMA_VERSION = "1.0"
RESPONSE_SCHEMA_VERSION = "1.0"
PROVIDER_OUTPUT_SCHEMA_NAME = "semantic_review_suggestion"

ALLOWED_MODEL_SUGGESTIONS = (
    SemanticSuggestionType.SUGGEST_MATCH.value,
    SemanticSuggestionType.SUGGEST_NO_MATCH.value,
    SemanticSuggestionType.INSUFFICIENT_EVIDENCE.value,
)

MAX_REASON_CODES = 8
MAX_EXPLANATION_CHARS = 400
MAX_REASON_CODE_CHARS = 64
MAX_ID_CHARS = 128


def provider_json_schema() -> dict[str, Any]:
    """JSON Schema sent to the provider. PROVIDER_FAILURE is never a model output."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "review_case_id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_CHARS},
            "record_a_id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_CHARS},
            "record_b_id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_CHARS},
            "suggestion": {"type": "string", "enum": list(ALLOWED_MODEL_SUGGESTIONS)},
            "reason_codes": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": MAX_REASON_CODE_CHARS},
                "minItems": 1,
                "maxItems": MAX_REASON_CODES,
            },
            "explanation": {"type": "string", "minLength": 1, "maxLength": MAX_EXPLANATION_CHARS},
        },
        "required": [
            "review_case_id",
            "record_a_id",
            "record_b_id",
            "suggestion",
            "reason_codes",
            "explanation",
        ],
    }


def responses_text_format() -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": PROVIDER_OUTPUT_SCHEMA_NAME,
            "strict": True,
            "schema": provider_json_schema(),
        }
    }


def parse_provider_payload(
    raw: str | dict[str, Any],
    *,
    expected_review_case_id: str,
    expected_record_a_id: str,
    expected_record_b_id: str,
) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SemanticReviewInvalidOutputError(
                f"Provider returned malformed JSON: {exc.msg}"
            ) from exc
    else:
        payload = raw

    if not isinstance(payload, dict):
        raise SemanticReviewInvalidOutputError("Provider output must be a JSON object.")
    allowed = {
        "review_case_id",
        "record_a_id",
        "record_b_id",
        "suggestion",
        "reason_codes",
        "explanation",
    }
    extra = set(payload) - allowed
    if extra:
        raise SemanticReviewInvalidOutputError(
            f"Provider output contains forbidden fields: {sorted(extra)}"
        )
    for required in (
        "review_case_id",
        "record_a_id",
        "record_b_id",
        "suggestion",
        "reason_codes",
        "explanation",
    ):
        if required not in payload:
            raise SemanticReviewInvalidOutputError(f"Provider output missing '{required}'.")

    review_case_id = payload["review_case_id"]
    record_a_id = payload["record_a_id"]
    record_b_id = payload["record_b_id"]
    for name, value in (
        ("review_case_id", review_case_id),
        ("record_a_id", record_a_id),
        ("record_b_id", record_b_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise SemanticReviewInvalidOutputError(f"{name} must be a non-empty string.")

    if review_case_id != expected_review_case_id:
        raise SemanticReviewIdentityMismatchError(
            f"Provider review_case_id '{review_case_id}' does not match "
            f"request '{expected_review_case_id}'."
        )
    if record_a_id != expected_record_a_id or record_b_id != expected_record_b_id:
        raise SemanticReviewIdentityMismatchError(
            "Provider record IDs do not match the requested pair order "
            f"({expected_record_a_id}, {expected_record_b_id})."
        )

    suggestion = payload["suggestion"]
    if not isinstance(suggestion, str):
        raise SemanticReviewInvalidOutputError("suggestion must be a string.")
    if suggestion in FORBIDDEN_HUMAN_DECISIONS:
        raise SemanticReviewInvalidOutputError(
            f"Provider attempted to emit a human decision '{suggestion}'."
        )
    if suggestion not in ALLOWED_MODEL_SUGGESTIONS:
        raise SemanticReviewInvalidOutputError(f"Unknown suggestion enum '{suggestion}'.")

    reason_codes = payload["reason_codes"]
    if not isinstance(reason_codes, list) or not reason_codes:
        raise SemanticReviewInvalidOutputError("reason_codes must be a non-empty list.")
    if len(reason_codes) > MAX_REASON_CODES:
        raise SemanticReviewInvalidOutputError("reason_codes exceeds the allowed maximum.")
    cleaned_codes: list[str] = []
    for item in reason_codes:
        if not isinstance(item, str) or not item.strip():
            raise SemanticReviewInvalidOutputError("reason_codes must contain non-empty strings.")
        if len(item) > MAX_REASON_CODE_CHARS:
            raise SemanticReviewInvalidOutputError("reason_code exceeds the allowed maximum.")
        cleaned_codes.append(item.strip())

    explanation = payload["explanation"]
    if not isinstance(explanation, str) or not explanation.strip():
        raise SemanticReviewInvalidOutputError("explanation must be a non-empty string.")
    if len(explanation) > MAX_EXPLANATION_CHARS:
        raise SemanticReviewInvalidOutputError("explanation exceeds the allowed maximum.")

    return {
        "review_case_id": review_case_id,
        "record_a_id": record_a_id,
        "record_b_id": record_b_id,
        "suggestion": suggestion,
        "reason_codes": tuple(cleaned_codes),
        "explanation": explanation.strip(),
    }
