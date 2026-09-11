from __future__ import annotations

import json

import pytest

from semantic_review.ids import canonical_json_digest, stable_request_id
from semantic_review.models import FORBIDDEN_HUMAN_DECISIONS, SemanticSuggestionType
from semantic_review.request_builder import build_semantic_review_request
from semantic_review.schema import REQUEST_SCHEMA_VERSION, parse_provider_payload
from tests.semantic_review.conftest import frozen_clock


def _valid_payload(**overrides):
    payload = {
        "review_case_id": "RC-1",
        "record_a_id": "a-1",
        "record_b_id": "a-2",
        "suggestion": "SUGGEST_MATCH",
        "reason_codes": ["EMAIL_EXACT"],
        "explanation": "ok",
    }
    payload.update(overrides)
    return payload


def test_suggestion_enum_excludes_human_decisions() -> None:
    values = {item.value for item in SemanticSuggestionType}
    assert values.isdisjoint(FORBIDDEN_HUMAN_DECISIONS)
    assert "MATCH" not in values
    assert "NO_MATCH" not in values
    assert "DEFERRED" not in values
    assert "DEFER" not in values


def test_request_serialization_and_fingerprint(review_bundle, semantic_config) -> None:
    _resolution, state, records_by_id, _config = review_bundle
    case = state.cases[0]
    first = build_semantic_review_request(
        case, records_by_id, config=semantic_config, clock=frozen_clock
    )
    second = build_semantic_review_request(
        case, records_by_id, config=semantic_config, clock=frozen_clock
    )
    assert first.request_fingerprint == second.request_fingerprint
    assert first.request_id == stable_request_id(first.request_fingerprint)
    assert first.request_schema_version == REQUEST_SCHEMA_VERSION
    payload = first.to_dict()
    json.dumps(payload)
    assert "person_id" not in payload
    assert "auto_match_threshold" not in payload
    assert "review_threshold" not in payload
    assert canonical_json_digest(first.fingerprint_payload()) == first.request_fingerprint


def test_parse_rejects_malformed_missing_extra_and_human_tokens() -> None:
    with pytest.raises(Exception, match="malformed JSON"):
        parse_provider_payload(
            "{",
            expected_review_case_id="RC-1",
            expected_record_a_id="a-1",
            expected_record_b_id="a-2",
        )
    with pytest.raises(Exception, match="missing"):
        parse_provider_payload(
            {"review_case_id": "RC-1", "suggestion": "SUGGEST_MATCH"},
            expected_review_case_id="RC-1",
            expected_record_a_id="a-1",
            expected_record_b_id="a-2",
        )
    with pytest.raises(Exception, match="forbidden fields"):
        parse_provider_payload(
            _valid_payload(person_id="secret"),
            expected_review_case_id="RC-1",
            expected_record_a_id="a-1",
            expected_record_b_id="a-2",
        )
    with pytest.raises(Exception, match="Unknown suggestion"):
        parse_provider_payload(
            _valid_payload(suggestion="MAYBE"),
            expected_review_case_id="RC-1",
            expected_record_a_id="a-1",
            expected_record_b_id="a-2",
        )
    for banned in ("MATCH", "NO_MATCH", "DEFER", "DEFERRED"):
        with pytest.raises(Exception, match="human decision"):
            parse_provider_payload(
                _valid_payload(suggestion=banned),
                expected_review_case_id="RC-1",
                expected_record_a_id="a-1",
                expected_record_b_id="a-2",
            )
