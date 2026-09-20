"""The whole of Sprint 11 in one chain: bootstrap a queue, then serve it.

Every other test in this package seeds its queue by calling
``register_workflow`` directly. That is the right shape for testing what the API
does with a queue, and the wrong shape for testing whether an operator can
actually get one. This file closes that gap: the queue is created by running the
real ``manage_human_review generate --register-review-queue`` command over a real
CSV, and it is then served by ``create_production_app`` through the real
``production_lifespan``. Nothing between the two is stubbed.

The integration that most needed proving is the database path. The bootstrap
command and the API runtime each read ``configs/review_persistence.yaml``
through ``load_review_persistence_config``, and each anchors a relative path to
the project root -- but they do it from different modules, and an operator who
bootstrapped one database and then served a different one would see a queue that
was permanently, silently empty. Both seams are redirected to one ``tmp_path``
config here, and the command is run *without* ``--review-db`` so that it is the
default resolution being exercised rather than an override.

No network, no provider, no golden or holdout data, and
``storage/review_queue.db`` is never created.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import review_api.dependencies as api_dependencies
from human_review.models import ReviewStatus
from review_api import create_production_app
from review_persistence.config import PROJECT_ROOT, ReviewPersistenceConfig
from scripts import manage_human_review

BASE_URL = "/api/v1/review-cases"

# The tenant and the queue an operator creates before registering anything.
# The chain below runs the real commands, so this is also the proof that
# neither comes into existence as a side effect of registering a workflow.
ORGANIZATION_SLUG = "operational-smoke"
REVIEW_QUEUE_NAME = "production-review"

# Two rows sharing an exact email while conflicting on company, city, district
# and address: strong identity evidence, a score below AUTO_MATCH, and therefore
# exactly one REVIEW case from the real entity-resolution engine.
OPERATIONAL_CSV = (
    "first_name,last_name,email,phone,company,city,district,address\n"
    "Ali,Yilmaz,ali@example.com,,Acme,Ankara,Cankaya,Street 1\n"
    "Ali,Yilmaz,ali@example.com,,Beta,Izmir,Konak,Other 9\n"
)


@pytest.fixture
def configured_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point both configuration seams at one temporary database.

    Patched in two places on purpose. ``scripts.manage_human_review`` and
    ``review_api.dependencies`` each imported the loader into their own
    namespace, so redirecting one and not the other is precisely the split-brain
    this fixture exists to rule out -- if the two modules ever stopped reading
    the same configuration, the chain below would break here rather than in
    production.
    """
    database = tmp_path / "storage" / "review_queue.db"

    def _config() -> ReviewPersistenceConfig:
        return ReviewPersistenceConfig(
            database_path=database,
            busy_timeout_ms=2000,
            journal_mode="WAL",
        )

    monkeypatch.setattr(manage_human_review, "load_review_persistence_config", _config)
    monkeypatch.setattr(api_dependencies, "load_review_persistence_config", _config)
    return database


def run_command(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Invoke the operator CLI the way an operator types it."""
    monkeypatch.setattr(sys, "argv", ["manage_human_review.py", *argv])
    return manage_human_review.main()


def create_organization(monkeypatch: pytest.MonkeyPatch) -> int:
    return run_command(
        monkeypatch,
        "create-organization",
        "--slug",
        ORGANIZATION_SLUG,
        "--name",
        "Operational Smoke",
    )


def create_review_queue(monkeypatch: pytest.MonkeyPatch, name: str = REVIEW_QUEUE_NAME) -> int:
    return run_command(
        monkeypatch,
        "create-review-queue",
        "--organization",
        ORGANIZATION_SLUG,
        "--name",
        name,
    )


def bootstrap_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> int:
    """The official operator chain, all three steps, in order.

    Create the tenant, create the queue, then register a workflow into that
    existing queue. No ``--review-db`` on any of them: the default configured
    path is the thing being exercised.

    Each step is separate because each is a separate operator decision.
    Registration provisions nothing: it refuses to invent an organization, and
    it refuses to invent a queue -- so both names on the last command must
    already resolve to something an operator deliberately made.
    """
    csv_path = tmp_path / "customers.csv"
    csv_path.write_text(OPERATIONAL_CSV, encoding="utf-8")
    for provision in (create_organization, create_review_queue):
        created = provision(monkeypatch)
        if created != 0:
            return created
    return run_command(
        monkeypatch,
        "generate",
        str(csv_path),
        "--report-dir",
        str(tmp_path / "report"),
        "--register-review-queue",
        "--organization",
        ORGANIZATION_SLUG,
        "--review-queue",
        REVIEW_QUEUE_NAME,
    )


def serve() -> TestClient:
    """The real production application, with the lifespan that owns the queue."""
    return TestClient(create_production_app())


def test_the_operator_chain_works_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured_queue: Path,
) -> None:
    """Bootstrap, serve, read, decide, restart, and read the decision back."""
    assert bootstrap_queue(monkeypatch, tmp_path) == 0
    # The command created the configured database, not some other one.
    assert configured_queue.exists()

    with serve() as client:
        assert client.get("/health").json() == {"status": "ok"}

        listing = client.get(BASE_URL)
        assert listing.status_code == 200
        page = listing.json()
        assert page["total"] == 1
        assert page["count"] == 1
        summary = page["items"][0]
        assert summary["status"] == ReviewStatus.PENDING.value

        case_id = summary["review_case_id"]
        detail = client.get(f"{BASE_URL}/{case_id}")
        assert detail.status_code == 200
        version = detail.json()["version"]
        assert version == 1

        resolved = client.post(
            f"{BASE_URL}/{case_id}/resolve",
            json={
                "decision": "NO_MATCH",
                "expected_version": version,
                "reviewer_id": "operator-smoke",
            },
        )
        assert resolved.status_code == 200, resolved.text
        body = resolved.json()
        assert body["case"]["status"] == "NO_MATCH"
        assert body["case"]["version"] == version + 1
        assert body["event"]["is_resolution"] is True

    # A second application over the same file: a fresh lifespan, a fresh
    # connection, and nothing carried over in memory.
    with serve() as client:
        detail = client.get(f"{BASE_URL}/{case_id}").json()
        assert detail["status"] == "NO_MATCH"
        assert detail["version"] == version + 1
        assert detail["resolution"]["human_decision"] == "NO_MATCH"
        assert detail["resolution"]["reviewer_id"] == "operator-smoke"

        events = client.get(f"{BASE_URL}/{case_id}/events").json()
        assert [event["is_resolution"] for event in events] == [True]
        assert events[0]["event_id"] is not None


def test_the_bootstrap_and_runtime_defaults_are_one_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured_queue: Path,
) -> None:
    """Compare resolved paths, not strings, through both production seams.

    ``configured_database_path`` is what the runner checks before starting, and
    the CLI's loader is what the bootstrap command opens. They must name the
    same file, or an operator bootstraps one queue and serves another.
    """
    bootstrap_target = manage_human_review.load_review_persistence_config().database_path
    runtime_target = api_dependencies.configured_database_path()

    assert bootstrap_target.resolve() == runtime_target.resolve() == configured_queue.resolve()

    assert bootstrap_queue(monkeypatch, tmp_path) == 0

    # The runner's readiness check sees the file the command just wrote.
    assert api_dependencies.configured_database_path().exists()


def test_a_queue_with_no_registered_workflow_fails_closed_rather_than_deciding(
    monkeypatch: pytest.MonkeyPatch,
    configured_queue: Path,
) -> None:
    """An existing, empty review queue: readable, and unable to decide anything.

    This is the residual state no startup check can detect -- the queue is
    real and owned by a real organization, it simply has no workflow
    authorization context yet. Reads answer an honest empty page, and any
    attempt to decide is refused with 503 rather than evaluated against an
    authorization graph that was never stored.
    """
    assert create_organization(monkeypatch) == 0
    assert (
        run_command(
            monkeypatch,
            "create-review-queue",
            "--organization",
            ORGANIZATION_SLUG,
            "--name",
            "empty",
        )
        == 0
    )

    with serve() as client:
        assert client.get(BASE_URL).json()["total"] == 0

        refused = client.post(
            f"{BASE_URL}/RC-does-not-matter/resolve",
            json={"decision": "NO_MATCH", "expected_version": 1},
        )

    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "REVIEW_QUEUE_NOT_READY"


def test_serving_a_database_with_no_review_queue_refuses_to_start(
    configured_queue: Path,
) -> None:
    """No queue means no tenant, and this build will not invent one.

    The transitional binding in ``review_api.dependencies`` reads the queue an
    operator already created. When there is none it raises during startup, so
    the process never begins answering requests about a queue it cannot name
    -- rather than creating a default organization to keep itself running.
    """
    with pytest.raises(RuntimeError, match="No review queue is registered"):
        with serve():
            pass  # pragma: no cover - startup raises before the body runs


def test_serving_a_database_with_two_review_queues_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
    configured_queue: Path,
) -> None:
    """Ambiguity is refused, not resolved by picking one.

    These routes have no authenticated caller, so nothing in a request could
    say which tenant it means. Serving one of the two arbitrarily would put
    one organization's review evidence behind an unauthenticated endpoint.
    """
    assert create_organization(monkeypatch) == 0
    for name in ("first", "second"):
        assert (
            run_command(
                monkeypatch,
                "create-review-queue",
                "--organization",
                ORGANIZATION_SLUG,
                "--name",
                name,
            )
            == 0
        )

    with pytest.raises(RuntimeError, match="2 review queues"):
        with serve():
            pass  # pragma: no cover - startup raises before the body runs


def test_the_production_database_is_never_created() -> None:
    """Guards the fixture wiring for this module."""
    assert not (PROJECT_ROOT / "storage" / "review_queue.db").exists()
