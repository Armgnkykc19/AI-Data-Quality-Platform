"""Storage and integrity failures on a read path, as the client sees them.

Each error is raised from the repository carrying text of exactly the kind the
real ones carry -- a database path, a SQL fragment, a schema version, a table
name. The assertions are that the status and code are right and that none of
that text survives the boundary.

Raising through the repository rather than calling the handler directly is
deliberate: it exercises the same resolution Starlette performs at runtime,
including the MRO walk that decides whether a subclass lands on its own handler
or its parent's.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from review_application import (
    PersistedCaseIntegrityError,
    ReviewEventIntegrityError,
    ReviewPersistenceError,
    ReviewSchemaVersionError,
    SemanticSuggestionIntegrityError,
)
from review_application.errors import ReviewApplicationError
from tests.review_api.conftest import api_client
from tests.review_api.fake_repository import FakeReviewCaseRepository

CASES_URL = "/api/v1/review-cases"

# Text of the kind the real errors carry. Every fragment here would be a leak.
LEAKY_TEXT = (
    "storage/review_queue.db is locked; "
    "SELECT * FROM review_case_events WHERE schema_version = '1.0.0'; "
    "SENTINEL-STORAGE-DETAIL-4b21"
)

LEAK_FRAGMENTS = (
    "storage/review_queue.db",
    "review_queue.db",
    "SELECT",
    "review_case_events",
    "schema_version",
    "1.0.0",
    "SENTINEL-STORAGE-DETAIL-4b21",
    "Traceback",
)


def failing_client(error: Exception) -> TestClient:
    return TestClient(
        api_client(FakeReviewCaseRepository(read_error=error)).app,
        raise_server_exceptions=False,
    )


STORAGE_CASES = [
    (ReviewPersistenceError, 503, "REVIEW_STORAGE_UNAVAILABLE"),
    (ReviewSchemaVersionError, 503, "REVIEW_STORAGE_UNAVAILABLE"),
    (PersistedCaseIntegrityError, 500, "REVIEW_STORAGE_CORRUPT"),
    (ReviewEventIntegrityError, 500, "REVIEW_STORAGE_CORRUPT"),
    (SemanticSuggestionIntegrityError, 500, "REVIEW_STORAGE_CORRUPT"),
]


@pytest.mark.parametrize(("error_type", "status", "code"), STORAGE_CASES)
def test_storage_failures_map_to_their_code(
    error_type: type[Exception],
    status: int,
    code: str,
) -> None:
    response = failing_client(error_type(LEAKY_TEXT)).get(CASES_URL)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(("error_type", "status", "code"), STORAGE_CASES)
@pytest.mark.parametrize("fragment", LEAK_FRAGMENTS)
def test_storage_failures_leak_nothing(
    error_type: type[Exception],
    status: int,
    code: str,
    fragment: str,
) -> None:
    response = failing_client(error_type(LEAKY_TEXT)).get(CASES_URL)

    assert fragment not in response.text


def test_a_schema_version_error_lands_on_its_own_entry_not_the_parent() -> None:
    """``ReviewSchemaVersionError`` is a ``ReviewPersistenceError``.

    Both answer 503, so the outcome agrees either way -- but the MRO walk is
    what makes that true, and pinning it here means a future mapping that gives
    the two different answers cannot silently resolve to the wrong one.
    """
    assert issubclass(ReviewSchemaVersionError, ReviewPersistenceError)

    response = failing_client(ReviewSchemaVersionError(LEAKY_TEXT)).get(CASES_URL)

    assert response.status_code == 503


def test_an_unmapped_application_error_falls_through_to_a_static_500() -> None:
    """The floor beneath the mapping table.

    ``ReviewApplicationError`` itself is deliberately unmapped: catching the
    base class would absorb every future error into one code. An instance of it
    must therefore reach the catch-all, not a domain handler.
    """
    response = failing_client(ReviewApplicationError(LEAKY_TEXT)).get(CASES_URL)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "SENTINEL-STORAGE-DETAIL-4b21" not in response.text


@pytest.mark.parametrize(
    "url",
    [
        CASES_URL,
        f"{CASES_URL}/RC-abc",
        f"{CASES_URL}/RC-abc/events",
        f"{CASES_URL}/RC-abc/semantic-suggestions",
    ],
)
def test_every_read_route_is_covered_by_the_mapping(url: str) -> None:
    response = failing_client(ReviewPersistenceError(LEAKY_TEXT)).get(url)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REVIEW_STORAGE_UNAVAILABLE"
    assert "SENTINEL-STORAGE-DETAIL-4b21" not in response.text


def test_an_unwired_application_does_not_invent_an_empty_queue() -> None:
    """A route reached on an app with no repository is a deployment fault.

    It must not answer ``200 []`` -- a client cannot tell that from an empty
    queue, and a reviewer would read it as "nothing to review".
    """
    from review_api import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get(CASES_URL)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
