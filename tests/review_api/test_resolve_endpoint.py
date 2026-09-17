"""The resolution endpoint's transport contract, against a capturing service.

What this file establishes: the route forwards exactly three fields and adds
nothing; the request model rejects everything else; each domain refusal reaches
the client as its own status and code with no exception text attached.

What it deliberately does not establish: whether Sprint 08 would actually refuse
a given decision. That is proved against real SQLite in
``test_resolve_sqlite_integration.py``, where the domain does the refusing.
Here the service is a fake that raises on command, so a mapping can be exercised
without contriving the queue state behind it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from human_review.errors import (
    HumanReviewAuthorizationContextError,
    HumanReviewAuthorizationError,
    HumanReviewContradictionError,
    InvalidReviewTransitionError,
    ReviewCaseNotFoundError,
)
from human_review.models import HumanReviewDecision
from review_application import (
    PersistedCaseIntegrityError,
    ReviewAuthorizationConfigError,
    ReviewConflictError,
    ReviewEventIntegrityError,
    ReviewPersistenceError,
)
from review_application.errors import ReviewWorkflowContextMissingError
from tests.review_api.conftest import resolution_result, service_client
from tests.review_api.fake_service import FakeReviewQueueService

CASE_ID = "RC-cc90777b8be6026f"
RESOLVE_URL = f"/api/v1/review-cases/{CASE_ID}/resolve"

VALID_BODY = {"decision": "MATCH", "expected_version": 1, "reviewer_id": "rev-1"}

# Text of the kind the real exceptions carry. Every mapping test raises an
# exception containing all of it, so a handler that forwarded str(exc) would
# fail every leakage assertion below rather than only some.
LEAKY = (
    "Review case RC-SENTINEL-LEAK-8842 in storage/review_queue.db: "
    "SELECT * FROM review_cases; severe conflict on email a@example.com vs d@example.com; "
    "component {rec-a, rec-b, rec-c}; auto_match threshold 0.88; "
    "config configs/entity_resolution.yaml; SENTINEL-DOMAIN-DETAIL-5c19"
)

LEAK_FRAGMENTS = (
    "SENTINEL-DOMAIN-DETAIL-5c19",
    "RC-SENTINEL-LEAK-8842",
    "storage/review_queue.db",
    "SELECT",
    "review_cases",
    "a@example.com",
    "d@example.com",
    "rec-a",
    "component",
    "0.88",
    "configs/entity_resolution.yaml",
    "severe conflict",
    "Traceback",
)


def ok_service() -> FakeReviewQueueService:
    return FakeReviewQueueService(result=resolution_result("r-1", "r-2"))


def raising_client(error: Exception) -> TestClient:
    return service_client(FakeReviewQueueService(error=error))


# --------------------------------------------------------------------------
# Exactly what crosses the boundary
# --------------------------------------------------------------------------


def test_the_route_forwards_exactly_three_fields() -> None:
    """The central claim of this phase, asserted against a recorded call.

    Anything else appearing here would mean the HTTP layer had started
    contributing to an authorization decision it has no business influencing.
    """
    service = ok_service()

    response = service_client(service).post(RESOLVE_URL, json=VALID_BODY)

    assert response.status_code == 200
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call.review_case_id == CASE_ID
    assert call.decision is HumanReviewDecision.MATCH
    assert call.reviewer_id == "rev-1"
    assert call.expected_version == 1
    assert call.extra_kwargs == {}


def test_the_decision_arrives_as_the_domain_enum() -> None:
    """Not a string. The route must not hand raw transport text to the domain."""
    service = ok_service()

    service_client(service).post(RESOLVE_URL, json={"decision": "DEFER", "expected_version": 3})

    assert service.calls[0].decision is HumanReviewDecision.DEFER
    assert isinstance(service.calls[0].decision, HumanReviewDecision)


def test_a_missing_reviewer_id_is_forwarded_as_none() -> None:
    """The domain allows an anonymous decision; the API does not invent one."""
    service = ok_service()

    service_client(service).post(RESOLVE_URL, json={"decision": "MATCH", "expected_version": 1})

    assert service.calls[0].reviewer_id is None


def test_reviewer_id_is_forwarded_verbatim() -> None:
    """No strip, no case fold, no emptiness rule.

    It lands in an append-only audit row, so any normalisation here would
    silently change recorded identity. Sprint 13 owns reviewer identity.
    """
    service = ok_service()

    service_client(service).post(
        RESOLVE_URL,
        json={"decision": "MATCH", "expected_version": 1, "reviewer_id": "  Ada  "},
    )

    assert service.calls[0].reviewer_id == "  Ada  "


def test_the_route_does_not_call_the_service_when_validation_fails() -> None:
    """A rejected request must not reach the authority at all."""
    service = ok_service()

    response = service_client(service).post(RESOLVE_URL, json={"decision": "nope"})

    assert response.status_code == 422
    assert service.calls == []


# --------------------------------------------------------------------------
# Success contract
# --------------------------------------------------------------------------


def test_success_is_200_not_201() -> None:
    """The case already existed; it was transitioned, not created."""
    assert service_client(ok_service()).post(RESOLVE_URL, json=VALID_BODY).status_code == 200


def test_response_field_set_is_pinned() -> None:
    body = service_client(ok_service()).post(RESOLVE_URL, json=VALID_BODY).json()

    assert set(body) == {"case", "event"}


def test_the_response_reports_the_new_state() -> None:
    body = service_client(ok_service()).post(RESOLVE_URL, json=VALID_BODY).json()

    assert body["case"]["status"] == "MATCH"
    assert body["case"]["version"] == 2
    assert body["case"]["resolution"]["human_decision"] == "MATCH"
    assert body["case"]["resolution"]["reviewer_id"] == "rev-1"


def test_the_response_carries_the_appended_event() -> None:
    body = service_client(ok_service()).post(RESOLVE_URL, json=VALID_BODY).json()

    assert body["event"]["event_type"] == "MATCH"
    assert body["event"]["is_resolution"] is True
    assert body["event"]["resolution_sequence"] == 1


def test_the_response_event_id_is_null_here() -> None:
    """Honest about what the service returned.

    The id is assigned by the database during the write, and
    ``apply_resolution`` hands back only the updated case. ``event_id`` is
    already optional on the published event model, and a client that needs
    stable ids reads them from the history endpoint.
    """
    body = service_client(ok_service()).post(RESOLVE_URL, json=VALID_BODY).json()

    assert body["event"]["event_id"] is None


def test_the_response_reuses_the_phase_b_minimisation() -> None:
    """The resolved case is projected by the same mapper the GET uses."""
    body = service_client(ok_service()).post(RESOLVE_URL, json=VALID_BODY).text

    for token in (
        "machine_readable_reasons",
        "audit_entry_payload",
        "workflow_state",
        "resolution_snapshot",
        "entity_resolution_config_path",
        "entity_records",
        "records_by_id",
        "auto_match_pairs",
        "schema_version",
        "record_ids",
        "explanation",
    ):
        assert token not in body, f"{token} leaked into the resolution response"


def test_the_blocking_key_is_still_published_once() -> None:
    service = ok_service()
    case = service._result.persisted_case.case  # type: ignore[union-attr]
    key = case.blocking_reasons[0].blocking_key
    assert key

    body = service_client(service).post(RESOLVE_URL, json=VALID_BODY).text

    assert body.count(key) == 1


# --------------------------------------------------------------------------
# Error mapping
# --------------------------------------------------------------------------

MAPPINGS = [
    (ReviewCaseNotFoundError(LEAKY), 404, "REVIEW_CASE_NOT_FOUND"),
    (InvalidReviewTransitionError(LEAKY), 409, "REVIEW_CASE_NOT_PENDING"),
    (HumanReviewContradictionError(LEAKY), 409, "HUMAN_REVIEW_CONTRADICTION"),
    (HumanReviewAuthorizationError(LEAKY), 422, "MATCH_NOT_AUTHORIZED"),
    (HumanReviewAuthorizationContextError(LEAKY), 503, "AUTHORIZATION_CONTEXT_UNAVAILABLE"),
    (ReviewAuthorizationConfigError(LEAKY), 503, "AUTHORIZATION_CONFIG_UNAVAILABLE"),
    (ReviewWorkflowContextMissingError(LEAKY), 503, "REVIEW_QUEUE_NOT_READY"),
    (ReviewPersistenceError(LEAKY), 503, "REVIEW_STORAGE_UNAVAILABLE"),
    (PersistedCaseIntegrityError(LEAKY), 500, "REVIEW_STORAGE_CORRUPT"),
    (ReviewEventIntegrityError(LEAKY), 500, "REVIEW_STORAGE_CORRUPT"),
]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    MAPPINGS,
    ids=lambda value: type(value).__name__,
)
def test_each_refusal_maps_to_its_own_status_and_code(
    error: Exception,
    status: int,
    code: str,
) -> None:
    response = raising_client(error).post(RESOLVE_URL, json=VALID_BODY)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    ("error", "status", "code"),
    MAPPINGS,
    ids=lambda value: type(value).__name__,
)
@pytest.mark.parametrize("fragment", LEAK_FRAGMENTS)
def test_no_refusal_leaks_its_exception_text(
    error: Exception,
    status: int,
    code: str,
    fragment: str,
) -> None:
    response = raising_client(error).post(RESOLVE_URL, json=VALID_BODY)

    assert fragment not in response.text


def test_every_refusal_uses_the_standard_envelope() -> None:
    for error, _, code in MAPPINGS:
        payload = raising_client(error).post(RESOLVE_URL, json=VALID_BODY).json()
        assert set(payload) == {"error"}
        assert set(payload["error"]) == {"code", "message", "details"}
        assert payload["error"]["code"] == code


def test_an_unsafe_match_is_not_401_or_403() -> None:
    """Sprint 08 authorization is about records, not about who is calling.

    A 403 would tell a reviewer their credentials were insufficient, which is a
    claim this API cannot make -- it has no credentials at all.
    """
    response = raising_client(HumanReviewAuthorizationError(LEAKY)).post(
        RESOLVE_URL, json=VALID_BODY
    )

    assert response.status_code not in (401, 403)
    assert response.status_code == 422


def test_an_evaluated_refusal_and_an_unavailable_verdict_differ() -> None:
    """422 means "judged and refused"; 503 means "no verdict was reached".

    Collapsing them would tell a reviewer their merge is unsafe when in fact
    nothing established that.
    """
    refused = raising_client(HumanReviewAuthorizationError(LEAKY)).post(
        RESOLVE_URL, json=VALID_BODY
    )
    unavailable = raising_client(HumanReviewAuthorizationContextError(LEAKY)).post(
        RESOLVE_URL, json=VALID_BODY
    )

    assert refused.status_code == 422
    assert unavailable.status_code == 503
    assert refused.json()["error"]["message"] != unavailable.json()["error"]["message"]


# --------------------------------------------------------------------------
# Stale version
# --------------------------------------------------------------------------


def conflict_client() -> TestClient:
    return raising_client(ReviewConflictError(LEAKY, review_case_id=CASE_ID, expected_version=1))


def test_a_stale_version_is_409_with_its_own_code() -> None:
    response = conflict_client().post(RESOLVE_URL, json=VALID_BODY)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVIEW_CASE_VERSION_CONFLICT"


def test_the_conflict_message_tells_the_client_to_reload() -> None:
    message = conflict_client().post(RESOLVE_URL, json=VALID_BODY).json()["error"]["message"]

    assert message == (
        "The review case changed after it was loaded. Reload it before deciding again."
    )


def test_the_conflict_details_echo_only_the_clients_own_value() -> None:
    """Safe by construction, and it identifies which in-flight request lost."""
    details = conflict_client().post(RESOLVE_URL, json=VALID_BODY).json()["error"]["details"]

    assert details == {"expected_version": 1}


def test_the_conflict_details_do_not_reveal_the_stored_version() -> None:
    """Deliberate: obtaining it means an extra storage read in an error path.

    The answer could also be stale before the response is written, since another
    racer can land in between. A client that needs it re-fetches the case, which
    it must do anyway to decide again.
    """
    details = conflict_client().post(RESOLVE_URL, json=VALID_BODY).json()["error"]["details"]

    assert set(details) == {"expected_version"}
    for token in ("current_version", "stored_version", "version_in_database"):
        assert token not in conflict_client().post(RESOLVE_URL, json=VALID_BODY).text


def test_the_conflict_response_hides_the_cas_mechanism() -> None:
    """How the conflict was detected is not part of the contract.

    (No bare "CAS" token here: it is a substring of the error code itself.)
    """
    body = conflict_client().post(RESOLVE_URL, json=VALID_BODY).text

    for token in (
        "UPDATE",
        "WHERE",
        "compare-and-swap",
        "compare_and_swap",
        "BEGIN IMMEDIATE",
        "sqlite",
        "transaction",
    ):
        assert token not in body
