"""What a client is allowed to send, and everything it is not.

The resolution endpoint drives the Sprint 08 authority. Every input that
authority reads is loaded from storage by the service, so the only safe request
model is one that accepts three fields and rejects the rest outright.

The forbidden-field tests below are the enforcement of that. Each submits a
field that would, if honoured, let a client contribute to an authorization
decision -- an AUTO_MATCH graph, a threshold, a records map, a resolution
sequence, a status. ``extra="forbid"`` turns each into a 422, and the service is
never called.

Rejecting rather than ignoring matters: a silently dropped ``auto_match_threshold``
would make a request that tried to lower the safety bar look accepted.
"""

from __future__ import annotations

import pytest

from tests.review_api.conftest import resolution_result, service_client
from tests.review_api.fake_service import FakeReviewQueueService

CASE_ID = "RC-cc90777b8be6026f"
RESOLVE_URL = f"/api/v1/review-cases/{CASE_ID}/resolve"

SENTINEL = "SENTINEL-FORBIDDEN-INPUT-3a77"

# Authorization material, persistence metadata, and server-owned state. Nothing
# here may be settable by a reviewer.
FORBIDDEN_FIELDS = [
    "authorization_context",
    "records_by_id",
    "records",
    "entity_records",
    "resolution",
    "resolution_snapshot",
    "auto_match_pairs",
    "workflow_state",
    "entity_resolution_config",
    "entity_resolution_config_path",
    "auto_match_threshold",
    "review_threshold",
    "candidate_threshold",
    "person_id",
    "canonical_entity_id",
    "resolution_sequence",
    "audit_entry",
    "audit_entry_payload",
    "event_id",
    "event_type",
    "status",
    "machine_decision",
    "machine_score",
    "machine_reason",
    "version",
    "created_at_utc",
    "updated_at_utc",
    "schema_version",
    "suggestion_id",
    "semantic_suggestion",
    "downstream_action",
    "ground_truth",
    "split",
]


def fresh_service() -> FakeReviewQueueService:
    return FakeReviewQueueService(result=resolution_result("v-1", "v-2"))


def post(body: dict, service: FakeReviewQueueService | None = None):
    service = service or fresh_service()
    return service_client(service).post(RESOLVE_URL, json=body), service


# --------------------------------------------------------------------------
# Forbidden fields
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_a_forbidden_field_is_rejected(field: str) -> None:
    response, service = post(
        {"decision": "MATCH", "expected_version": 1, field: SENTINEL},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert service.calls == [], f"{field} reached the authority"


@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_a_forbidden_field_value_is_not_echoed(field: str) -> None:
    response, _ = post({"decision": "MATCH", "expected_version": 1, field: SENTINEL})

    assert SENTINEL not in response.text


def test_the_rejected_field_is_named_so_a_client_can_fix_it() -> None:
    """The key is actionable feedback; the value is the caller's data."""
    response, _ = post(
        {"decision": "MATCH", "expected_version": 1, "records_by_id": SENTINEL},
    )

    assert "records_by_id" in response.text
    assert SENTINEL not in response.text


def test_several_forbidden_fields_at_once_are_all_rejected() -> None:
    response, service = post(
        {
            "decision": "MATCH",
            "expected_version": 1,
            "auto_match_threshold": 0.1,
            "records_by_id": {"rec-a": {}},
            "resolution_snapshot": {"auto_match_pairs": [["rec-a", "rec-b"]]},
        },
    )

    assert response.status_code == 422
    assert service.calls == []


# --------------------------------------------------------------------------
# decision
# --------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["MATCH", "NO_MATCH", "DEFER"])
def test_each_valid_decision_is_accepted(decision: str) -> None:
    response, service = post({"decision": decision, "expected_version": 1})

    assert response.status_code == 200
    assert service.calls[0].decision.value == decision


def test_deferred_is_not_a_decision() -> None:
    """DEFERRED is the resulting *status*, never the submitted decision.

    Translating it would mean this layer deciding what a reviewer meant, and the
    two vocabularies belong to the domain, which keeps them distinct on purpose.
    """
    response, service = post({"decision": "DEFERRED", "expected_version": 1})

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "decision",
    ["match", "no_match", "defer", "Match", "DEFERRED", "UNKNOWN", "", " MATCH", "MATCH "],
)
def test_an_invalid_decision_is_rejected(decision: str) -> None:
    response, service = post({"decision": decision, "expected_version": 1})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert service.calls == []


def test_a_missing_decision_is_rejected() -> None:
    response, service = post({"expected_version": 1})

    assert response.status_code == 422
    assert service.calls == []


def test_a_null_decision_is_rejected() -> None:
    response, _ = post({"decision": None, "expected_version": 1})

    assert response.status_code == 422


def test_an_invalid_decision_value_is_not_echoed() -> None:
    response, _ = post({"decision": SENTINEL, "expected_version": 1})

    assert response.status_code == 422
    assert SENTINEL not in response.text


# --------------------------------------------------------------------------
# expected_version
# --------------------------------------------------------------------------


@pytest.mark.parametrize("version", [1, 2, 7, 9999])
def test_a_positive_version_is_accepted(version: int) -> None:
    response, service = post({"decision": "MATCH", "expected_version": version})

    assert response.status_code == 200
    assert service.calls[0].expected_version == version


def test_expected_version_is_mandatory() -> None:
    """Never defaulted to the stored value.

    Optimistic concurrency is the contract: a reviewer decides against the
    version they were shown. Substituting "whatever is current" would apply a
    decision to a queue state the reviewer never saw, which is precisely the
    failure the version exists to prevent.
    """
    response, service = post({"decision": "MATCH"})

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("version", [0, -1, -99])
def test_a_non_positive_version_is_rejected(version: int) -> None:
    response, service = post({"decision": "MATCH", "expected_version": version})

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("version", ["3", "1", "abc", 3.5, 2.0, True, None, [1], {"v": 1}])
def test_a_non_integer_version_is_rejected(version: object) -> None:
    """Strict integers, chosen deliberately.

    Pydantic's default lax mode would coerce the JSON string "3" into 3 and the
    float 2.0 into 2. A concurrency token whose type the client and server
    disagree about is one a client can get subtly wrong and still have honoured,
    so the field is declared strict and the coercion never happens.
    """
    response, service = post({"decision": "MATCH", "expected_version": version})

    assert response.status_code == 422, f"{version!r} was coerced instead of rejected"
    assert service.calls == []


def test_a_bool_is_not_a_version() -> None:
    """``True`` is an int in Python; strict mode still refuses it."""
    response, _ = post({"decision": "MATCH", "expected_version": True})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# reviewer_id
# --------------------------------------------------------------------------


def test_reviewer_id_may_be_omitted() -> None:
    response, service = post({"decision": "MATCH", "expected_version": 1})

    assert response.status_code == 200
    assert service.calls[0].reviewer_id is None


def test_reviewer_id_may_be_null() -> None:
    response, service = post(
        {"decision": "MATCH", "expected_version": 1, "reviewer_id": None},
    )

    assert response.status_code == 200
    assert service.calls[0].reviewer_id is None


def test_an_empty_reviewer_id_is_passed_through() -> None:
    """The domain already accepts it; rejecting it here would invent a rule.

    Reviewer identity is unverified in Sprint 11 and Sprint 13 owns it. Adding
    an emptiness rule now would change what the audit trail records for a reason
    this sprint cannot justify.
    """
    response, service = post({"decision": "MATCH", "expected_version": 1, "reviewer_id": ""})

    assert response.status_code == 200
    assert service.calls[0].reviewer_id == ""


def test_an_overlong_reviewer_id_is_rejected() -> None:
    """Transport hygiene: it ends up in a durable append-only row."""
    response, service = post(
        {"decision": "MATCH", "expected_version": 1, "reviewer_id": "x" * 257},
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("value", [123, 4.5, True, ["a"], {"id": "a"}])
def test_a_non_string_reviewer_id_is_rejected(value: object) -> None:
    """Strict again: coercing 123 into "123" would record an identity nobody sent."""
    response, service = post(
        {"decision": "MATCH", "expected_version": 1, "reviewer_id": value},
    )

    assert response.status_code == 422
    assert service.calls == []


# --------------------------------------------------------------------------
# Body and path shape
# --------------------------------------------------------------------------


def test_a_missing_body_is_rejected() -> None:
    service = fresh_service()
    response = service_client(service).post(RESOLVE_URL)

    assert response.status_code == 422
    assert service.calls == []


def test_a_non_object_body_is_rejected() -> None:
    service = fresh_service()
    response = service_client(service).post(RESOLVE_URL, json=["MATCH", 1])

    assert response.status_code == 422
    assert service.calls == []


def test_an_empty_path_id_does_not_reach_the_service() -> None:
    """No RC-* pattern is restated here; only bounds. Existence is storage's answer."""
    service = fresh_service()
    response = service_client(service).post(
        "/api/v1/review-cases//resolve", json={"decision": "MATCH", "expected_version": 1}
    )

    assert response.status_code in (404, 422)
    assert service.calls == []


def test_an_overlong_path_id_is_rejected() -> None:
    service = fresh_service()
    response = service_client(service).post(
        f"/api/v1/review-cases/{'x' * 200}/resolve",
        json={"decision": "MATCH", "expected_version": 1},
    )

    assert response.status_code == 422
    assert service.calls == []


def test_an_unknown_shaped_id_still_reaches_the_service() -> None:
    """The API does not pre-judge identifier syntax; the queue decides existence."""
    service = fresh_service()
    response = service_client(service).post(
        "/api/v1/review-cases/not-an-rc-id/resolve",
        json={"decision": "MATCH", "expected_version": 1},
    )

    assert response.status_code == 200
    assert service.calls[0].review_case_id == "not-an-rc-id"


def test_resolve_rejects_other_methods() -> None:
    service = fresh_service()
    client = service_client(service)

    assert client.get(RESOLVE_URL).status_code == 405
    put = client.put(RESOLVE_URL, json={"decision": "MATCH", "expected_version": 1})
    assert put.status_code == 405
    assert client.delete(RESOLVE_URL).status_code == 405
    assert service.calls == []
