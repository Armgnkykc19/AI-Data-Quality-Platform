"""The health contract, pinned field by field.

The assertions here are mostly about what is *absent*. ``GET /health`` is the
one endpoint an unauthenticated caller is expected to reach, so every field it
gains is a fact published to whoever can reach the port. Asserting equality
against the whole body -- rather than checking that "status" is present -- is
what makes an added field fail a test instead of silently shipping.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Terms that describe the deployment rather than its liveness. None of them may
# appear anywhere in the health response, under any key.
FORBIDDEN_IN_HEALTH = (
    "schema",
    "version",
    "database",
    "db",
    "path",
    "storage",
    "queue",
    "sqlite",
    "commit",
    "config",
)


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_body_has_exactly_one_field(client: TestClient) -> None:
    """Equality above already implies this; stating it names the intent."""
    assert set(client.get("/health").json()) == {"status"}


@pytest.mark.parametrize("term", FORBIDDEN_IN_HEALTH)
def test_health_describes_nothing_about_the_deployment(client: TestClient, term: str) -> None:
    assert term not in client.get("/health").text.lower()


def test_health_is_json(client: TestClient) -> None:
    assert client.get("/health").headers["content-type"].startswith("application/json")


def test_health_rejects_other_methods(client: TestClient) -> None:
    """A liveness probe is a read. Anything else is a client mistake, not a 404."""
    response = client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_no_readiness_endpoint_exists_yet(client: TestClient) -> None:
    """Phase A ships liveness only.

    Readiness would have to describe whether the queue is usable, which means
    reading storage state. That belongs with the endpoints whose readiness it
    would describe, not ahead of them.
    """
    assert client.get("/ready").status_code == 404
