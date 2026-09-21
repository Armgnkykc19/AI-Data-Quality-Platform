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

# The one scoped prefix every review operation lives under. Written out rather
# than imported from the router so this file states the published contract
# independently of the code that produces it.
TENANT_CASES = (
    "/api/v1/organizations/{organization_id}/review-queues/{review_queue_id}/review-cases"
)


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
        TENANT_CASES,
        f"{TENANT_CASES}/{{review_case_id}}",
        f"{TENANT_CASES}/{{review_case_id}}/events",
        f"{TENANT_CASES}/{{review_case_id}}/semantic-suggestions",
    ],
)
def test_each_read_endpoint_is_published(schema: dict, path: str) -> None:
    # OpenAPI spells methods in lower case.
    assert "get" in schema["paths"][path], f"{path} is not published as a GET"


def test_health_is_published(schema: dict) -> None:
    assert "get" in schema["paths"]["/health"]


def test_the_resolution_operation_is_published_as_a_post(schema: dict) -> None:
    operations = schema["paths"][f"{TENANT_CASES}/{{review_case_id}}/resolve"]

    assert set(operations) == {"post"}


def test_no_semantic_generation_operation_is_published(schema: dict) -> None:
    assert not [path for path, _ in operation_ids(schema) if path.endswith("/semantic-review")]


def test_no_registration_operation_is_published(schema: dict) -> None:
    assert not [path for path, _ in operation_ids(schema) if "register" in path]


# The complete published HTTP surface.
#
# Registering a trusted workflow is an operator action --
# ``manage_human_review.py ... --register-review-queue`` -- and must never
# become a request anyone can send: the context it writes is what MATCH
# authorization is evaluated against.
#
# Creating a user is an operator action for the same kind of reason. There is no
# public registration and no password-reset or password-change endpoint; an
# account exists because an operator made one.
#
# Granting a membership is an operator action too, and for the sharpest reason
# of the three: a membership is what makes a tenant's review evidence reachable
# at all. An HTTP endpoint that created one would be an endpoint that grants
# access to customer data, reachable with a stolen cookie.
PUBLISHED_OPERATIONS = {
    ("/health", "GET"),
    ("/api/v1/auth/login", "POST"),
    ("/api/v1/auth/logout", "POST"),
    ("/api/v1/auth/session", "GET"),
    ("/api/v1/auth/session/continue", "POST"),
    (TENANT_CASES, "GET"),
    (f"{TENANT_CASES}/{{review_case_id}}", "GET"),
    (f"{TENANT_CASES}/{{review_case_id}}/events", "GET"),
    (f"{TENANT_CASES}/{{review_case_id}}/semantic-suggestions", "GET"),
    (f"{TENANT_CASES}/{{review_case_id}}/resolve", "POST"),
}


def test_the_published_surface_is_exactly_the_expected_operations(schema: dict) -> None:
    """A set comparison, so an added route fails here rather than shipping."""
    assert operation_ids(schema) == PUBLISHED_OPERATIONS


@pytest.mark.parametrize(
    "path",
    ["/api/v1/auth/register", "/api/v1/auth/password", "/api/v1/auth/password/reset"],
)
def test_no_self_service_credential_operation_is_published(schema: dict, path: str) -> None:
    """Registration, password change and reset are all absent, deliberately.

    Each is a credential-bearing flow with its own abuse surface -- enumeration
    through a signup form, an emailed reset token, a change endpoint reachable
    with a stolen cookie -- and none of them is needed by an internal tool whose
    accounts an operator creates.
    """
    assert path not in schema["paths"]


@pytest.mark.parametrize(
    "token",
    ["register", "bootstrap", "import", "workflow", "context", "upload", "admin"],
)
def test_no_trusted_registration_vocabulary_appears_in_a_path(
    schema: dict,
    token: str,
) -> None:
    assert not [path for path, _ in operation_ids(schema) if token in path.lower()]


@pytest.mark.parametrize("token", ["member", "membership", "role", "grant", "invite"])
def test_no_membership_management_operation_is_published(schema: dict, token: str) -> None:
    """Granting or changing access is an operator action, never a request.

    Phase E added ``manage_human_review.py add-membership`` and deliberately
    nothing beside it. A membership endpoint would let whoever holds a session
    widen their own reach, which is the one escalation this design has no
    defence against other than not existing.
    """
    assert not [path for path, _ in operation_ids(schema) if token in path.lower()]


def test_no_unscoped_review_path_survives(schema: dict) -> None:
    """The Sprint 11 surface is gone, not merely authenticated in place.

    An unscoped route that required a session would still have to pick a queue
    from somewhere, and every candidate -- the sole queue, the session, a
    default -- is a queue the caller did not name. So the paths themselves must
    be absent.
    """
    offenders = [path for path, _ in operation_ids(schema) if path.startswith("/api/v1/review")]

    assert offenders == []


def test_every_review_operation_names_both_tenant_segments(schema: dict) -> None:
    """Stated positively, so a future route cannot be added without a scope."""
    review_paths = [path for path, _ in operation_ids(schema) if "review-cases" in path]

    assert review_paths
    for path in review_paths:
        assert "{organization_id}" in path
        assert "{review_queue_id}" in path


def test_the_semantic_suggestions_endpoint_is_read_only(schema: dict) -> None:
    """Reading a persisted suggestion is published; producing one is not.

    Stated as a method set rather than a path search, because the read path is
    legitimately spelled ``semantic-suggestions`` -- what must not exist is a
    write beside it that would call the provider from a request.
    """
    operations = schema["paths"][f"{TENANT_CASES}/{{review_case_id}}/semantic-suggestions"]

    assert set(operations) == {"get"}


@pytest.mark.parametrize("token", ["semantic", "suggest", "generate"])
def test_no_published_write_reaches_semantic_generation(schema: dict, token: str) -> None:
    writes = [path for path, method in operation_ids(schema) if method != "GET"]

    assert not [path for path in writes if token in path.lower()]


def test_resolution_is_the_only_published_review_write(schema: dict) -> None:
    """Authentication writes change a session; they cannot reach review data."""
    writes = {
        (path, method)
        for path, method in operation_ids(schema)
        if method != "GET" and "/review-cases" in path
    }

    assert writes == {(f"{TENANT_CASES}/{{review_case_id}}/resolve", "POST")}


# --------------------------------------------------------------------------
# The published request contract
# --------------------------------------------------------------------------


def test_the_resolve_operation_declares_the_explicit_request_model(schema: dict) -> None:
    operation = schema["paths"][f"{TENANT_CASES}/{{review_case_id}}/resolve"]["post"]
    ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]

    assert ref.endswith("/ResolveReviewCaseRequest")


def test_the_published_request_has_exactly_two_properties(schema: dict) -> None:
    """A decision and a version, and that is the whole of what a client may send."""
    request = schema["components"]["schemas"]["ResolveReviewCaseRequest"]

    assert set(request["properties"]) == {"decision", "expected_version"}


def test_the_published_request_advertises_no_reviewer_identity(schema: dict) -> None:
    """The schema is where a generated client learns what to send.

    Leaving ``reviewer_id`` published while the server rejected it would give
    every code generator a field that produces a 422 -- and, worse, would
    advertise client-chosen reviewer identity as part of the contract when the
    server now derives it from the session.
    """
    request = json.dumps(schema["components"]["schemas"]["ResolveReviewCaseRequest"])

    for token in ("reviewer_id", "user_id", "organization_id", "review_queue_id"):
        assert token not in request, f"{token} is published as a resolve request field"


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
    operation = schema["paths"][f"{TENANT_CASES}/{{review_case_id}}/resolve"]["post"]
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


# --------------------------------------------------------------------------
# The published authentication contract
# --------------------------------------------------------------------------


def response_schema_names(schema: dict) -> set[str]:
    """Every component schema reachable from a published *response*.

    Walked from the responses rather than listed, so a model that becomes a
    response later is covered without anyone remembering to add it here.
    """
    components = schema.get("components", {}).get("schemas", {})
    seen: set[str] = set()

    def visit(node: object) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[-1]
                if name not in seen:
                    seen.add(name)
                    visit(components.get(name, {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    for operations in schema["paths"].values():
        for operation in operations.values():
            visit(operation.get("responses", {}))
    return seen


def test_no_response_schema_publishes_a_secret(schema: dict) -> None:
    """A password may be an input. It may never be an output.

    Walked recursively because a secret does not have to be a top-level field
    to be published -- one nested model is enough.
    """
    components = schema["components"]["schemas"]

    for name in response_schema_names(schema):
        properties = set(components.get(name, {}).get("properties", {}))
        offenders = {
            field
            for field in properties
            if any(
                token in field.lower()
                for token in ("password", "token", "secret", "credential", "hash")
            )
        }
        assert not offenders, f"{name} publishes {offenders}"


def test_password_appears_only_in_the_login_request(schema: dict) -> None:
    components = schema["components"]["schemas"]

    carrying = {
        name
        for name, model in components.items()
        if any("password" in field.lower() for field in model.get("properties", {}))
    }

    assert carrying == {"LoginRequest"}


def test_the_session_response_publishes_no_tenant_or_role(schema: dict) -> None:
    """A session establishes identity; authorization is read fresh each request.

    Publishing a role would invite a client to cache it, and a cached role is a
    role that stays in force after an operator removes it.
    """
    components = schema["components"]["schemas"]
    published = {
        field
        for name in response_schema_names(schema)
        for field in components.get(name, {}).get("properties", {})
    }

    for forbidden in ("organization", "queue", "role", "membership", "session_id", "email"):
        assert not [field for field in published if forbidden in field.lower()], forbidden


def test_the_login_request_forbids_extra_properties(schema: dict) -> None:
    """So a caller cannot smuggle a field into the one request that
    establishes identity."""
    assert schema["components"]["schemas"]["LoginRequest"]["additionalProperties"] is False


def test_the_session_policy_travels_with_the_session(schema: dict) -> None:
    """A frontend reads the thresholds rather than hard-coding 55 and 60.

    A browser that computed its own could drift from the expiry the server
    applies and warn about a session that had already ended.
    """
    properties = schema["components"]["schemas"]["SessionRead"]["properties"]

    for field in ("idle_timeout_seconds", "warning_after_seconds", "absolute_timeout_seconds"):
        assert field in properties
