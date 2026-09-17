"""The published schema, which is the contract clients actually generate from.

A field can be absent from every response body a test happens to exercise and
still be published in the schema, where a code generator will pick it up and a
reader will believe it exists. ``explanation`` is the field that matters here:
Sprint 09 never stores it, so the API must not advertise it either.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from review_api import create_app


@pytest.fixture
def schema() -> dict:
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def operation_ids(schema: dict) -> set[tuple[str, str]]:
    return {
        (path, method.upper())
        for path, operations in schema["paths"].items()
        for method in operations
    }


# --------------------------------------------------------------------------
# Published operations
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/review-cases",
        "/api/v1/review-cases/{review_case_id}",
        "/api/v1/review-cases/{review_case_id}/events",
        "/api/v1/review-cases/{review_case_id}/semantic-suggestions",
    ],
)
def test_each_read_endpoint_is_published(schema: dict, path: str) -> None:
    # OpenAPI spells methods in lower case.
    assert "get" in schema["paths"][path], f"{path} is not published as a GET"


def test_health_is_published(schema: dict) -> None:
    assert "get" in schema["paths"]["/health"]


def test_the_resolution_operation_is_published_as_a_post(schema: dict) -> None:
    operations = schema["paths"]["/api/v1/review-cases/{review_case_id}/resolve"]

    assert set(operations) == {"post"}


def test_no_semantic_generation_operation_is_published(schema: dict) -> None:
    assert not [path for path, _ in operation_ids(schema) if path.endswith("/semantic-review")]


def test_no_registration_operation_is_published(schema: dict) -> None:
    assert not [path for path, _ in operation_ids(schema) if "register" in path]


# The complete Sprint 11 HTTP surface. Registering a trusted workflow is an
# operator action -- ``manage_human_review.py ... --register-review-queue`` --
# and must never become a request anyone can send: the context it writes is what
# MATCH authorization is evaluated against, and this API has no authentication.
SPRINT_11_OPERATIONS = {
    ("/health", "GET"),
    ("/api/v1/review-cases", "GET"),
    ("/api/v1/review-cases/{review_case_id}", "GET"),
    ("/api/v1/review-cases/{review_case_id}/events", "GET"),
    ("/api/v1/review-cases/{review_case_id}/semantic-suggestions", "GET"),
    ("/api/v1/review-cases/{review_case_id}/resolve", "POST"),
}


def test_the_published_surface_is_exactly_the_sprint_11_operations(schema: dict) -> None:
    """A set comparison, so an added route fails here rather than shipping."""
    assert operation_ids(schema) == SPRINT_11_OPERATIONS


@pytest.mark.parametrize(
    "token",
    ["register", "bootstrap", "import", "workflow", "context", "upload", "admin"],
)
def test_no_trusted_registration_vocabulary_appears_in_a_path(
    schema: dict,
    token: str,
) -> None:
    assert not [path for path, _ in operation_ids(schema) if token in path.lower()]


def test_the_semantic_suggestions_endpoint_is_read_only(schema: dict) -> None:
    """Reading a persisted suggestion is published; producing one is not.

    Stated as a method set rather than a path search, because the read path is
    legitimately spelled ``semantic-suggestions`` -- what must not exist is a
    write beside it that would call the provider from a request.
    """
    operations = schema["paths"]["/api/v1/review-cases/{review_case_id}/semantic-suggestions"]

    assert set(operations) == {"get"}


@pytest.mark.parametrize("token", ["semantic", "suggest", "generate"])
def test_no_published_write_reaches_semantic_generation(schema: dict, token: str) -> None:
    writes = [path for path, method in operation_ids(schema) if method != "GET"]

    assert not [path for path in writes if token in path.lower()]


def test_resolution_is_the_only_published_write(schema: dict) -> None:
    writes = {(path, method) for path, method in operation_ids(schema) if method != "GET"}

    assert writes == {("/api/v1/review-cases/{review_case_id}/resolve", "POST")}


# --------------------------------------------------------------------------
# The published request contract
# --------------------------------------------------------------------------


def test_the_resolve_operation_declares_the_explicit_request_model(schema: dict) -> None:
    operation = schema["paths"]["/api/v1/review-cases/{review_case_id}/resolve"]["post"]
    ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]

    assert ref.endswith("/ResolveReviewCaseRequest")


def test_the_published_request_has_exactly_three_properties(schema: dict) -> None:
    request = schema["components"]["schemas"]["ResolveReviewCaseRequest"]

    assert set(request["properties"]) == {"decision", "expected_version", "reviewer_id"}


def test_the_published_request_forbids_extra_properties(schema: dict) -> None:
    """``extra="forbid"`` must reach the schema, not only the runtime model.

    A generated client built from a permissive schema would happily send fields
    the server rejects, and the mismatch would only surface at runtime.
    """
    request = schema["components"]["schemas"]["ResolveReviewCaseRequest"]

    assert request.get("additionalProperties") is False


def test_expected_version_and_decision_are_required(schema: dict) -> None:
    """``expected_version`` is never defaulted; optimistic concurrency depends on it."""
    request = schema["components"]["schemas"]["ResolveReviewCaseRequest"]

    assert set(request["required"]) == {"decision", "expected_version"}


def test_the_published_decision_enum_is_the_decision_vocabulary(schema: dict) -> None:
    """MATCH / NO_MATCH / DEFER -- not the DEFERRED status spelling."""
    enum = schema["components"]["schemas"]["HumanReviewDecision"]["enum"]

    assert sorted(enum) == ["DEFER", "MATCH", "NO_MATCH"]
    assert "DEFERRED" not in enum


def test_the_published_version_has_a_lower_bound(schema: dict) -> None:
    version = schema["components"]["schemas"]["ResolveReviewCaseRequest"]["properties"][
        "expected_version"
    ]

    assert version["minimum"] == 1


def test_no_authorization_material_is_published_as_a_request_field(schema: dict) -> None:
    """The schema is where a client learns what it may send. It may send three fields."""
    request = json.dumps(schema["components"]["schemas"]["ResolveReviewCaseRequest"])

    for token in (
        "records_by_id",
        "entity_records",
        "resolution_snapshot",
        "auto_match_pairs",
        "auto_match_threshold",
        "review_threshold",
        "entity_resolution_config",
        "workflow_state",
        "resolution_sequence",
        "audit_entry",
        "person_id",
        "machine_score",
        "status",
        "suggestion_id",
    ):
        assert token not in request, f"{token} is published as a resolve request field"


def test_the_resolve_operation_declares_the_explicit_response_model(schema: dict) -> None:
    operation = schema["paths"]["/api/v1/review-cases/{review_case_id}/resolve"]["post"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]

    assert ref.endswith("/ResolveReviewCaseResponse")
    assert set(schema["components"]["schemas"]["ResolveReviewCaseResponse"]["properties"]) == {
        "case",
        "event",
    }


# --------------------------------------------------------------------------
# The non-durable field must not be advertised
# --------------------------------------------------------------------------


def test_the_suggestion_schema_declares_no_explanation(schema: dict) -> None:
    properties = schema["components"]["schemas"]["SemanticSuggestionRead"]["properties"]

    assert "explanation" not in properties
    assert set(properties) == {
        "suggestion_id",
        "suggestion",
        "reason_codes",
        "provider",
        "requested_model",
        "failure_code",
        "live",
        "created_at_utc",
        "advisory",
    }


def test_the_word_explanation_appears_nowhere_in_the_schema(schema: dict) -> None:
    """Including as a description, an example, or a nested definition."""
    assert "explanation" not in json.dumps(schema)


def test_no_published_schema_advertises_provider_telemetry(schema: dict) -> None:
    document = json.dumps(schema)

    for token in (
        "estimated_cost_usd",
        "input_token_count",
        "output_token_count",
        "latency_ms",
        "provider_request_id",
        "request_fingerprint",
        "prompt_template_hash",
        "cost_mode",
        "pricing_version",
    ):
        assert token not in document, f"{token} is advertised in the OpenAPI schema"


def test_no_published_schema_advertises_persistence_material(schema: dict) -> None:
    document = json.dumps(schema)

    for token in (
        "audit_entry_payload",
        "case_payload_json",
        "resolution_snapshot",
        "entity_resolution_config_path",
        "auto_match_pairs",
        "machine_readable_reasons",
    ):
        assert token not in document, f"{token} is advertised in the OpenAPI schema"


def test_the_schema_does_not_advertise_a_database_schema_version(schema: dict) -> None:
    """``schema_version`` is database metadata on stored events and cases."""
    assert "schema_version" not in json.dumps(schema)
