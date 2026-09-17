"""The error envelope, and the leak it exists to prevent.

Two things are under test. The envelope is structurally stable -- same three
keys, same nesting, ``details`` always present -- so a client can parse one
shape. And no response carries exception text, a filesystem path, SQL, a
traceback, or the caller's own submitted value.

The second is the one worth writing carefully, because it fails quietly. A
handler that forwards ``str(exc)`` looks correct in every test that only checks
status codes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.review_api.conftest import (
    CUSTOM_VALIDATOR_PREFIX,
    SENSITIVE_VALIDATOR_SENTINEL,
    SENTINEL_INPUT,
)

# Fragments that would mean an internal detail escaped. The probe route raises
# an exception containing every one of them, so these assertions are not
# vacuous: a handler that forwarded the message would fail each of them.
LEAK_MARKERS = (
    "storage/review_queue.db",
    "review_queue.db",
    "SELECT",
    "FROM review_cases",
    "database is locked",
    "Traceback",
    'File "',
    "sqlite3",
    "PRAGMA",
    "BEGIN IMMEDIATE",
    SENTINEL_INPUT,
)


def assert_is_envelope(payload: object, *, code: str) -> dict:
    """Every error response has this shape, and only this shape."""
    assert isinstance(payload, dict)
    assert set(payload) == {"error"}

    error = payload["error"]
    assert isinstance(error, dict)
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    return error


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_unknown_route_uses_the_envelope(client: TestClient) -> None:
    response = client.get("/no-such-route")

    assert response.status_code == 404
    error = assert_is_envelope(response.json(), code="NOT_FOUND")
    assert error["details"] is None


def test_method_not_allowed_uses_the_envelope(client: TestClient) -> None:
    response = client.delete("/health")

    assert response.status_code == 405
    assert_is_envelope(response.json(), code="METHOD_NOT_ALLOWED")


def test_method_not_allowed_keeps_the_allow_header(client: TestClient) -> None:
    """Part of the HTTP contract, and it says nothing about the deployment."""
    assert "GET" in client.delete("/health").headers.get("allow", "")


def test_the_details_key_is_always_present(client: TestClient) -> None:
    """One policy, not two.

    A client should never have to distinguish "details is null" from "details is
    missing", so the key is serialized even when there is nothing to put in it.
    """
    assert "details" in client.get("/no-such-route").json()["error"]


def test_validation_failure_uses_the_envelope(probe_client: TestClient) -> None:
    response = probe_client.post("/probe/validate", json={"expected_version": "not-an-int"})

    assert response.status_code == 422
    error = assert_is_envelope(response.json(), code="INVALID_REQUEST")
    assert error["details"] is not None


def test_unexpected_error_returns_a_static_500(probe_client: TestClient) -> None:
    response = probe_client.get("/probe/boom")

    assert response.status_code == 500
    error = assert_is_envelope(response.json(), code="INTERNAL_ERROR")
    assert error["message"] == "An internal error occurred."
    assert error["details"] is None


# --------------------------------------------------------------------------
# Validation feedback is useful without echoing the request
# --------------------------------------------------------------------------


def test_validation_details_name_the_field_and_the_constraint(probe_client: TestClient) -> None:
    """Structural information only: which field, and which rule it broke."""
    response = probe_client.post("/probe/validate", json={"expected_version": "not-an-int"})

    fields = response.json()["error"]["details"]["fields"]
    assert len(fields) == 1
    assert set(fields[0]) == {"location", "type"}
    assert fields[0]["location"] == ["body", "expected_version"]
    assert fields[0]["type"] == "int_parsing"


def test_validation_details_carry_no_prose(probe_client: TestClient) -> None:
    """No message key, under any spelling.

    Pydantic's own wording is safe; the point is that the wording is not this
    API's to vouch for. Dropping it here is what makes the next test possible.
    """
    fields = probe_client.post("/probe/validate", json={"expected_version": "not-an-int"}).json()[
        "error"
    ]["details"]["fields"]

    for field in fields:
        assert "message" not in field
        assert "msg" not in field


def test_validation_details_do_not_echo_the_submitted_value(probe_client: TestClient) -> None:
    """The whole point of sanitizing Pydantic's output.

    Pydantic reports the offending value under ``input``. Forwarding it turns
    every validation error into a reflection of whatever was posted, and the
    field a client got wrong is often the one carrying personal data.
    """
    response = probe_client.post("/probe/validate", json={"expected_version": SENTINEL_INPUT})

    assert response.status_code == 422
    assert SENTINEL_INPUT not in response.text


def test_validation_details_drop_pydantic_metadata_keys(probe_client: TestClient) -> None:
    response = probe_client.post("/probe/validate", json={"expected_version": SENTINEL_INPUT})

    body = response.text
    assert '"input"' not in body
    assert '"ctx"' not in body
    assert '"url"' not in body
    assert "errors.pydantic.dev" not in body


def test_an_unexpected_field_is_named_but_its_value_is_not(probe_client: TestClient) -> None:
    """Strict request models reject extra keys; the key is feedback, the value is not."""
    response = probe_client.post(
        "/probe/validate",
        json={"expected_version": 1, "resolution": SENTINEL_INPUT},
    )

    assert response.status_code == 422
    assert "resolution" in response.text
    assert SENTINEL_INPUT not in response.text


# --------------------------------------------------------------------------
# A custom validator cannot smuggle text into the response
# --------------------------------------------------------------------------
#
# The tests above use built-in Pydantic failures, whose wording is safe. That
# safety is a property of the framework's messages, not of the sanitizer, so on
# its own it proves nothing about a validator this project writes later. These
# tests exercise the case that actually decides it: author-written exception
# text, interpolating the value that failed, lifted verbatim into Pydantic's
# "msg". A sanitizer keeping "msg" passes every test above and fails these.


def test_a_custom_validator_failure_is_still_a_clean_422(probe_client: TestClient) -> None:
    response = probe_client.post(
        "/probe/custom-validate",
        json={"reviewer_id": SENSITIVE_VALIDATOR_SENTINEL},
    )

    assert response.status_code == 422
    assert_is_envelope(response.json(), code="INVALID_REQUEST")


def test_a_custom_validator_failure_still_identifies_the_field(probe_client: TestClient) -> None:
    """Sanitizing must not cost the client the one thing it can act on."""
    response = probe_client.post(
        "/probe/custom-validate",
        json={"reviewer_id": SENSITIVE_VALIDATOR_SENTINEL},
    )

    fields = response.json()["error"]["details"]["fields"]
    assert len(fields) == 1
    assert fields[0]["location"] == ["body", "reviewer_id"]
    assert fields[0]["type"] == "value_error"
    assert set(fields[0]) == {"location", "type"}


def test_a_custom_validator_cannot_leak_the_submitted_value(probe_client: TestClient) -> None:
    response = probe_client.post(
        "/probe/custom-validate",
        json={"reviewer_id": SENSITIVE_VALIDATOR_SENTINEL},
    )

    assert SENSITIVE_VALIDATOR_SENTINEL not in response.text


def test_a_custom_validator_cannot_leak_its_own_message(probe_client: TestClient) -> None:
    response = probe_client.post(
        "/probe/custom-validate",
        json={"reviewer_id": SENSITIVE_VALIDATOR_SENTINEL},
    )

    body = response.text
    assert CUSTOM_VALIDATOR_PREFIX not in body
    assert "not in the roster" not in body
    assert "Value error" not in body


def test_no_message_key_survives_a_custom_validator_failure(probe_client: TestClient) -> None:
    fields = probe_client.post(
        "/probe/custom-validate",
        json={"reviewer_id": SENSITIVE_VALIDATOR_SENTINEL},
    ).json()["error"]["details"]["fields"]

    for field in fields:
        assert "message" not in field
        assert "msg" not in field


# --------------------------------------------------------------------------
# Nothing internal escapes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("marker", LEAK_MARKERS)
def test_the_500_body_carries_no_internal_detail(probe_client: TestClient, marker: str) -> None:
    response = probe_client.get("/probe/boom")

    assert response.status_code == 500
    assert marker not in response.text


@pytest.mark.parametrize("marker", LEAK_MARKERS)
def test_the_validation_body_carries_no_internal_detail(
    probe_client: TestClient,
    marker: str,
) -> None:
    response = probe_client.post("/probe/validate", json={"expected_version": SENTINEL_INPUT})

    assert marker not in response.text


def test_the_500_response_is_json_not_an_html_traceback(probe_client: TestClient) -> None:
    """What ``debug=False`` buys.

    With debug on, Starlette renders an unhandled exception as an HTML page
    containing the traceback and source lines, bypassing the envelope entirely.
    """
    response = probe_client.get("/probe/boom")

    assert response.headers["content-type"].startswith("application/json")
    assert "<html" not in response.text.lower()
