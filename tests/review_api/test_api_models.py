"""Model-level contracts, tested without an application.

These are the assertions that keep the published shape from drifting: a field
set checked against ``model_fields`` fails when someone adds a field, which an
endpoint test asserting "status == ok" would not.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from review_api.app import create_app
from review_api.errors import (
    DOMAIN_ERROR_MAPPINGS,
    PUBLIC_MESSAGES,
    ApiError,
    ErrorCode,
    ErrorResponse,
)
from review_api.models import (
    ApiRequestModel,
    ApiResponseModel,
    HealthResponse,
    ResolveReviewCaseRequest,
    ResolveReviewCaseResponse,
    ReviewCaseDetail,
    ReviewEventRead,
)

# --------------------------------------------------------------------------
# HealthResponse
# --------------------------------------------------------------------------


def test_health_response_has_exactly_one_field() -> None:
    assert set(HealthResponse.model_fields) == {"status"}


def test_health_response_serializes_to_the_published_body() -> None:
    assert HealthResponse(status="ok").model_dump() == {"status": "ok"}


@pytest.mark.parametrize("value", ["degraded", "OK", "", "error"])
def test_health_response_accepts_only_ok(value: str) -> None:
    """A literal, not a free string: there is no other body this endpoint returns."""
    with pytest.raises(ValidationError):
        HealthResponse(status=value)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The response base
# --------------------------------------------------------------------------


def test_response_models_forbid_extra_fields() -> None:
    """A route returning more than its contract fails here rather than leaking.

    FastAPI validates a route's return value against its response model, so this
    is the guard that catches a payload quietly widened with persistence
    metadata.
    """
    with pytest.raises(ValidationError):
        HealthResponse(status="ok", schema_version="1.0.0")  # type: ignore[call-arg]


def test_every_api_response_model_shares_the_strict_base() -> None:
    for model in (HealthResponse, ApiError, ErrorResponse):
        assert issubclass(model, ApiResponseModel)
        assert model.model_config["extra"] == "forbid"


def test_the_request_base_forbids_extra_fields() -> None:
    """The Phase A deferral ended when an endpoint started accepting input.

    On a request model this is a security control rather than tidiness: every
    authorization input the Sprint 08 authority reads is loaded from storage, so
    an unrecognised key is rejected rather than ignored. Silently dropping an
    ``auto_match_threshold`` would make a request that tried to lower the safety
    bar look accepted.
    """
    assert ApiRequestModel.model_config["extra"] == "forbid"
    assert issubclass(ResolveReviewCaseRequest, ApiRequestModel)
    assert ResolveReviewCaseRequest.model_config["extra"] == "forbid"


def test_the_resolve_request_accepts_exactly_three_fields() -> None:
    assert set(ResolveReviewCaseRequest.model_fields) == {
        "decision",
        "expected_version",
        "reviewer_id",
    }


def test_the_resolve_response_reuses_the_phase_b_projections() -> None:
    """One shape per concept, not a parallel one for the write path."""
    assert set(ResolveReviewCaseResponse.model_fields) == {"case", "event"}
    assert ResolveReviewCaseResponse.model_fields["case"].annotation is ReviewCaseDetail
    assert ResolveReviewCaseResponse.model_fields["event"].annotation is ReviewEventRead


def test_the_request_and_response_bases_are_separate() -> None:
    """So a request-only rule never lands on a response by accident."""
    assert not issubclass(ApiResponseModel, ApiRequestModel)
    assert not issubclass(ApiRequestModel, ApiResponseModel)


# --------------------------------------------------------------------------
# The error envelope
# --------------------------------------------------------------------------


def test_error_envelope_fields() -> None:
    assert set(ErrorResponse.model_fields) == {"error"}
    assert set(ApiError.model_fields) == {"code", "message", "details"}


def test_details_defaults_to_none_and_is_always_serialized() -> None:
    payload = ErrorResponse(
        error=ApiError(code=ErrorCode.INTERNAL_ERROR, message="An internal error occurred.")
    ).model_dump()

    assert payload == {
        "error": {
            "code": ErrorCode.INTERNAL_ERROR,
            "message": "An internal error occurred.",
            "details": None,
        }
    }


def test_every_error_code_has_a_static_public_message() -> None:
    """No code may fall back to exception text for want of a message."""
    assert set(PUBLIC_MESSAGES) == set(ErrorCode)
    for message in PUBLIC_MESSAGES.values():
        assert message and not message.endswith(":")


@pytest.mark.parametrize("code", list(ErrorCode))
def test_public_messages_name_no_internal_detail(code: ErrorCode) -> None:
    lowered = PUBLIC_MESSAGES[code].lower()
    for marker in ("sqlite", "sql", "storage", "path", "traceback", ".db", "review_case"):
        assert marker not in lowered


def test_error_codes_are_api_owned_tokens() -> None:
    """Stable strings a client can branch on, not exception class names.

    Pinned as a list so a new code is a deliberate contract change rather than
    something that appears because an exception class was added somewhere.
    """
    assert [code.value for code in ErrorCode] == [
        "INVALID_REQUEST",
        # Sprint 13 Phase D. One token for every way authentication can fail,
        # because the caller must not be able to tell them apart.
        "UNAUTHENTICATED",
        "FORBIDDEN",
        "NOT_FOUND",
        "METHOD_NOT_ALLOWED",
        "INTERNAL_ERROR",
        "REVIEW_CASE_NOT_FOUND",
        "REVIEW_STORAGE_UNAVAILABLE",
        "REVIEW_STORAGE_CORRUPT",
        "REVIEW_CASE_VERSION_CONFLICT",
        "REVIEW_CASE_NOT_PENDING",
        "HUMAN_REVIEW_CONTRADICTION",
        "MATCH_NOT_AUTHORIZED",
        "AUTHORIZATION_CONTEXT_UNAVAILABLE",
        "AUTHORIZATION_CONFIG_UNAVAILABLE",
        "REVIEW_QUEUE_NOT_READY",
    ]


def test_no_later_phase_error_code_exists_yet() -> None:
    """A code with no route that can produce it is a branch no test can reach.

    ``UNAUTHENTICATED`` and ``FORBIDDEN`` left this list in Sprint 13 Phase D,
    when the authentication endpoints that raise them arrived. The rest are
    still premature: registration and semantic generation have no HTTP surface,
    and tenant authorization codes belong to the phase that enforces it.
    """
    declared = {code.value for code in ErrorCode}

    for premature in (
        "QUEUE_ALREADY_REGISTERED",
        "DUPLICATE_CASE_REGISTRATION",
        "SEMANTIC_PROVIDER_UNAVAILABLE",
        "SEMANTIC_BUDGET_EXCEEDED",
        "ORGANIZATION_NOT_FOUND",
        "INSUFFICIENT_ROLE",
    ):
        assert premature not in declared


def test_each_exception_is_mapped_once_to_a_declared_code() -> None:
    """Duplicate entries would make the winner depend on registration order."""
    mapped_types = [entry[0] for entry in DOMAIN_ERROR_MAPPINGS]

    assert len(mapped_types) == len(set(mapped_types))
    for exception_type, status, code in DOMAIN_ERROR_MAPPINGS:
        assert isinstance(exception_type, type) and issubclass(exception_type, Exception)
        assert code in ErrorCode
        assert status in (404, 409, 422, 500, 503)


def test_the_application_error_base_is_never_mapped() -> None:
    """Catching it would absorb every future error into whichever code sat there."""
    from review_application.errors import ReviewApplicationError

    assert ReviewApplicationError not in [entry[0] for entry in DOMAIN_ERROR_MAPPINGS]


# --------------------------------------------------------------------------
# The factory
# --------------------------------------------------------------------------


def test_create_app_returns_a_fastapi_application() -> None:
    assert isinstance(create_app(), FastAPI)


def test_created_apps_are_isolated_instances() -> None:
    """No module-level application, so a test can never pollute another's state."""
    first, second = create_app(), create_app()

    assert first is not second
    first.state.repository = object()
    assert second.state.repository is None


def test_debug_is_off() -> None:
    """With debug on, an unhandled exception renders as an HTML traceback."""
    assert create_app().debug is False


def test_no_cors_middleware_is_installed() -> None:
    """No authentication means no origin this API could safely trust."""
    installed = [middleware.cls.__name__ for middleware in create_app().user_middleware]

    assert "CORSMiddleware" not in installed


def test_injected_repository_and_service_reach_app_state() -> None:
    """The Phase B/C seam: a fake satisfying the contract wires in like the real one."""
    repository, service = object(), object()

    app = create_app(repository=repository, service=service)  # type: ignore[arg-type]

    assert app.state.repository is repository
    assert app.state.service is service


def test_an_unwired_app_reports_no_storage() -> None:
    app = create_app()

    assert app.state.repository is None
    assert app.state.service is None


def published_paths() -> dict[str, dict]:
    """The published operation map, read from the generated OpenAPI document.

    Deliberately not ``app.routes``. That list is Starlette's internal
    composition of the application, and its shape is not a contract: from
    Starlette 1.0 an included router appears there as a router object carrying
    no ``path`` at all, so a walk over it silently stops finding the endpoints
    it was written to check.

    Silently is the dangerous part. Two of the assertions below state that
    something is *absent*, and against an empty list they pass for entirely the
    wrong reason. Reading the OpenAPI document instead keeps them honest: it is
    what FastAPI publishes, what a client generates from, and a public API of
    the framework rather than an implementation detail of its router.

    ``app.openapi()`` is used rather than a request to ``/openapi.json`` because
    it needs no client and runs no lifespan -- building the schema must not be
    able to open the configured review database.
    """
    return create_app().openapi()["paths"]


def business_routes() -> list[tuple[str, set[str]]]:
    """Every published path under /api, with the methods it answers."""
    return sorted(
        (path, {method.upper() for method in operations})
        for path, operations in published_paths().items()
        if path.startswith("/api")
    )


def test_the_published_surface_is_pinned_whole() -> None:
    """Pinned whole, so a new endpoint cannot ship without editing this list.

    Sprint 13 Phase D added four authentication operations and changed nothing
    about the review ones. The review routes are deliberately still
    unauthenticated: adding identity to them without tenant authorization would
    produce a surface where a signed-in user can read every tenant's queue,
    which is worse than an honestly unauthenticated one.
    """
    assert business_routes() == [
        ("/api/v1/auth/login", {"POST"}),
        ("/api/v1/auth/logout", {"POST"}),
        ("/api/v1/auth/session", {"GET"}),
        ("/api/v1/auth/session/continue", {"POST"}),
        ("/api/v1/review-cases", {"GET"}),
        ("/api/v1/review-cases/{review_case_id}", {"GET"}),
        ("/api/v1/review-cases/{review_case_id}/events", {"GET"}),
        ("/api/v1/review-cases/{review_case_id}/resolve", {"POST"}),
        ("/api/v1/review-cases/{review_case_id}/semantic-suggestions", {"GET"}),
    ]


def test_health_is_still_published() -> None:
    assert "/health" in published_paths()


def test_resolution_is_the_only_review_write_endpoint() -> None:
    """One write against review data, and it goes through the Sprint 08 authority.

    The authentication writes are excluded by path rather than forgotten: they
    change a session, never a review case, and none of them can reach the
    review queue at all. Narrowing the assertion to ``/review-cases`` keeps it
    saying what it always said -- that a second way to alter a human decision
    cannot appear unnoticed.
    """
    writes = [
        (path, methods)
        for path, methods in business_routes()
        if methods != {"GET"} and "/review-cases" in path
    ]

    assert writes == [("/api/v1/review-cases/{review_case_id}/resolve", {"POST"})]


def test_no_registration_endpoint_is_published() -> None:
    """Queue registration is a Phase D concern, and not a reviewer action.

    ``register_workflow`` takes an entire workflow state plus the full record
    set and the AUTO_MATCH snapshot. Exposing it over an unauthenticated surface
    would let a caller install the very authorization context that the
    resolution endpoint is judged against.
    """
    paths = {path for path, _ in business_routes()}

    assert not [path for path in paths if "register" in path or path.endswith("/workflows")]


def test_no_live_semantic_generation_endpoint_exists() -> None:
    """Deferred out of the Sprint 11 HTTP surface entirely, not hidden or disabled.

    Generation is budget-controlled and disabled by default; exposing it here
    would put a provider call and a spend decision in an unauthenticated request
    path. Reading stored suggestions is the whole semantic surface.
    """
    paths = {path for path, _ in business_routes()}

    assert "/api/v1/review-cases/{review_case_id}/semantic-review" not in paths
    assert not [path for path in paths if path.endswith("/semantic-review")]
