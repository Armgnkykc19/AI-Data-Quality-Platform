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


def bootstrap_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> int:
    """Run the official operator command. No ``--review-db``: the default is the point."""
    csv_path = tmp_path / "customers.csv"
    csv_path.write_text(OPERATIONAL_CSV, encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_human_review.py",
            "generate",
            str(csv_path),
            "--report-dir",
            str(tmp_path / "report"),
            "--register-review-queue",
        ],
    )
    return manage_human_review.main()


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


def test_an_unbootstrapped_queue_fails_closed_rather_than_deciding(
    configured_queue: Path,
) -> None:
    """A database with a valid schema but no registered workflow context.

    This is the residual state the runner's file-existence check cannot detect,
    so it is pinned here instead. Reads answer an honest empty page, and any
    attempt to decide is refused with 503 rather than evaluated against an
    authorization graph that was never stored.
    """
    with serve() as client:
        # Starting the app initializes the schema, which is what makes this the
        # "valid schema, never bootstrapped" case rather than a missing file.
        assert client.get(BASE_URL).json()["total"] == 0

        refused = client.post(
            f"{BASE_URL}/RC-does-not-matter/resolve",
            json={"decision": "NO_MATCH", "expected_version": 1},
        )

    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "REVIEW_QUEUE_NOT_READY"


def test_the_production_database_is_never_created() -> None:
    """Guards the fixture wiring for this module."""
    assert not (PROJECT_ROOT / "storage" / "review_queue.db").exists()
