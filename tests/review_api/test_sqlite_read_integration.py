"""The read endpoints over a real SQLite queue in a temporary file.

The fake-repository tests prove the projections are right. This file proves the
projections are right about what the real Sprint 10 stack actually stores --
that a case registered through ``register_workflow``, resolved through
``ReviewWorkflow``, and given an advisory suggestion comes back over HTTP
looking the way the contract says it should.

The queue is seeded by calling the repository directly. That is fixture setup
and nothing more: it does **not** close the production registration gap, which
remains a Phase D deliverable. No production code path populates a queue yet.

Every database lives under ``tmp_path``. ``storage/review_queue.db`` is never
touched, and a test that did touch it would be writing to a real reviewer queue.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from entity_resolution.config import load_entity_resolution_config
from human_review.models import HumanReviewDecision
from human_review.reporting import resolution_snapshot
from review_api import create_app
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite import (
    ReviewDatabase,
    SqliteReviewCaseRepository,
    open_review_database,
)
from tests.human_review.conftest import match_authorization_kwargs
from tests.review_api.conftest import NOW, build_review_state, records_by_id
from tests.review_persistence.semantic_fixtures import make_suggestion

CASES_URL = "/api/v1/review-cases"


@pytest.fixture
def persistence_config(tmp_path: Path) -> ReviewPersistenceConfig:
    """Always tmp_path. No test may open the production queue."""
    return ReviewPersistenceConfig(
        database_path=tmp_path / "review_queue.db",
        busy_timeout_ms=2000,
        journal_mode="WAL",
    )


@asynccontextmanager
async def open_queue(app: FastAPI, config: ReviewPersistenceConfig) -> AsyncIterator[None]:
    """Mirror ``production_lifespan``, against a temporary database.

    The connection is opened *inside* the lifespan, which matters more than it
    looks. A ``sqlite3`` connection is legal only on the thread that created it,
    and ``TestClient`` runs the application on its own thread -- so a database
    opened in the test's main thread and handed to ``create_app`` would raise
    ``ProgrammingError`` on the first query. Opening here puts the connection on
    the same thread that will serve the requests, exactly as uvicorn does when
    it runs the real lifespan on its event loop.

    That constraint is Sprint 11's accepted temporary one, unchanged from Phase
    A: no ``check_same_thread``, no lock, no executor. Sprint 14 owns the
    redesign.
    """
    database = open_review_database(config)
    try:
        app.state.repository = SqliteReviewCaseRepository(database)
        yield
    finally:
        app.state.repository = None
        database.close()


@pytest.fixture
def seeded(persistence_config: ReviewPersistenceConfig) -> dict:
    """A real queue on disk: one registered case with an advisory suggestion.

    Seeded by calling the repository directly, on its own connection, which is
    closed before the API opens its own. This is fixture setup only -- it does
    not close the production registration gap, which remains Phase D.
    """
    resolution, state = build_review_state("s-1", "s-2")
    case = state.cases[0]
    suggestion = make_suggestion(case, records_by_id(resolution))

    database = open_review_database(persistence_config)
    try:
        repository = SqliteReviewCaseRepository(database)
        repository.register_workflow(
            state,
            entity_records=resolution.records,
            resolution_snapshot=resolution_snapshot(resolution),
            entity_resolution_config_path="configs/entity_resolution.yaml",
            now_utc=NOW,
        )
        repository.record_semantic_suggestion(suggestion, now_utc=NOW)
    finally:
        database.close()

    return {
        "config": persistence_config,
        "case_id": case.review_case_id,
        "state": state,
        "resolution": resolution,
        "er_config": load_entity_resolution_config(),
        "suggestion": suggestion,
    }


@pytest.fixture
def client(seeded: dict) -> Iterator[TestClient]:
    def lifespan(app: FastAPI):
        return open_queue(app, seeded["config"])

    with TestClient(create_app(lifespan=lifespan)) as test_client:
        yield test_client


@contextmanager
def open_repository(seeded: dict) -> Iterator[SqliteReviewCaseRepository]:
    """A short-lived repository on this thread, for seeding and verification."""
    database: ReviewDatabase = open_review_database(seeded["config"])
    try:
        yield SqliteReviewCaseRepository(database)
    finally:
        database.close()


def resolve_through_the_domain(seeded: dict, decision: HumanReviewDecision) -> None:
    """Resolve using the real Sprint 08 workflow and the real Sprint 10 repository.

    The API is not involved: Phase B has no write endpoint. This exists only so
    the read endpoints can be tested against state the domain actually produced,
    rather than a status a test assigned by hand.
    """
    from human_review.workflow import ReviewWorkflow
    from review_application import ReviewEvent

    with open_repository(seeded) as repository:
        bundle = repository.load_workflow_bundle()
        workflow = ReviewWorkflow(bundle.to_workflow_state())
        kwargs = (
            match_authorization_kwargs(seeded["resolution"], seeded["er_config"])
            if decision is HumanReviewDecision.MATCH
            else {}
        )
        updated = workflow.resolve_case(
            seeded["case_id"], decision=decision, reviewer_id="reviewer-7", **kwargs
        )
        case = updated.case_by_id(seeded["case_id"])
        assert case is not None
        repository.apply_resolution(
            case,
            expected_version=1,
            event=ReviewEvent.from_audit_entry(
                updated.audit_trail[-1], occurred_at_utc="2026-09-14T09:00:00Z"
            ),
            now_utc="2026-09-14T09:00:00Z",
        )


# --------------------------------------------------------------------------
# Reads against real stored state
# --------------------------------------------------------------------------


def test_the_queue_lists_the_registered_case(client: TestClient, seeded: dict) -> None:
    body = client.get(CASES_URL).json()

    assert body["total"] == 1
    assert body["items"][0]["review_case_id"] == seeded["case_id"]
    assert body["items"][0]["status"] == "PENDING"
    assert body["items"][0]["version"] == 1


def test_the_status_filter_reaches_sqlite(client: TestClient) -> None:
    assert client.get(CASES_URL, params={"status": "PENDING"}).json()["total"] == 1
    assert client.get(CASES_URL, params={"status": "MATCH"}).json()["total"] == 0


def test_detail_projects_a_real_stored_case(client: TestClient, seeded: dict) -> None:
    body = client.get(f"{CASES_URL}/{seeded['case_id']}").json()

    assert body["status"] == "PENDING"
    assert body["resolution"] is None
    assert body["version"] == 1
    assert body["created_at_utc"] == NOW
    assert body["blocking_reasons"]
    assert body["supporting_evidence"]


def test_a_real_resolution_is_visible_through_the_api(client: TestClient, seeded: dict) -> None:
    resolve_through_the_domain(seeded, HumanReviewDecision.MATCH)

    body = client.get(f"{CASES_URL}/{seeded['case_id']}").json()

    assert body["status"] == "MATCH"
    assert body["version"] == 2
    assert body["resolution"]["human_decision"] == "MATCH"
    assert body["resolution"]["reviewer_id"] == "reviewer-7"
    assert body["resolution"]["resolution_sequence"] == 1


def test_real_history_is_visible_and_ordered(client: TestClient, seeded: dict) -> None:
    resolve_through_the_domain(seeded, HumanReviewDecision.NO_MATCH)

    events = client.get(f"{CASES_URL}/{seeded['case_id']}/events").json()

    types = [event["event_type"] for event in events]
    assert "SEMANTIC_SUGGESTION_RECORDED" in types
    assert types[-1] == "NO_MATCH"
    assert [event["event_id"] for event in events] == sorted(event["event_id"] for event in events)


def test_the_semantic_event_is_not_a_resolution(client: TestClient, seeded: dict) -> None:
    events = client.get(f"{CASES_URL}/{seeded['case_id']}/events").json()
    semantic = [e for e in events if e["event_type"] == "SEMANTIC_SUGGESTION_RECORDED"]

    assert semantic
    for event in semantic:
        assert event["is_resolution"] is False
        assert event["resolution_sequence"] is None
        assert event["reviewer_id"] is None


def test_a_stored_suggestion_reads_back_advisory(client: TestClient, seeded: dict) -> None:
    body = client.get(f"{CASES_URL}/{seeded['case_id']}/semantic-suggestions").json()

    assert len(body) == 1
    assert body[0]["suggestion_id"] == seeded["suggestion"].suggestion_id
    assert body[0]["advisory"] is True
    assert body[0]["live"] is False


def test_a_stored_suggestion_carries_no_explanation(client: TestClient, seeded: dict) -> None:
    """Sprint 09 never persisted it, so there is nothing to publish or rebuild."""
    response = client.get(f"{CASES_URL}/{seeded['case_id']}/semantic-suggestions")

    assert "explanation" not in response.text
    assert "explanation" not in response.json()[0]


def test_a_suggestion_does_not_change_the_case(client: TestClient, seeded: dict) -> None:
    """Advisory means advisory: the queue still shows an undecided case."""
    detail = client.get(f"{CASES_URL}/{seeded['case_id']}").json()

    assert detail["status"] == "PENDING"
    assert detail["resolution"] is None
    assert detail["version"] == 1


def test_an_unknown_case_is_a_404_against_real_storage(client: TestClient) -> None:
    for suffix in ("", "/events", "/semantic-suggestions"):
        response = client.get(f"{CASES_URL}/RC-0000000000000000{suffix}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "REVIEW_CASE_NOT_FOUND"


# --------------------------------------------------------------------------
# GETs do not write
# --------------------------------------------------------------------------


def stored_state(seeded: dict) -> tuple:
    """Read the durable state back from the file, on a fresh connection.

    A fresh connection rather than a cached object, so this observes what is
    actually committed rather than what some in-memory repository believes.
    """
    case_id = seeded["case_id"]
    with open_repository(seeded) as repository:
        case = repository.get_case(case_id)
        return (
            case.status,
            case.version,
            case.created_at_utc,
            case.updated_at_utc,
            len(repository.list_events(case_id)),
            len(repository.list_semantic_suggestions(case_id)),
        )


def test_reading_everything_changes_nothing_in_the_database(
    client: TestClient,
    seeded: dict,
) -> None:
    case_id = seeded["case_id"]
    before = stored_state(seeded)

    for url in (
        CASES_URL,
        f"{CASES_URL}?status=PENDING",
        f"{CASES_URL}/{case_id}",
        f"{CASES_URL}/{case_id}/events",
        f"{CASES_URL}/{case_id}/semantic-suggestions",
    ):
        assert client.get(url).status_code == 200

    assert stored_state(seeded) == before


def test_reads_do_not_change_a_resolved_case_either(client: TestClient, seeded: dict) -> None:
    resolve_through_the_domain(seeded, HumanReviewDecision.MATCH)
    case_id = seeded["case_id"]
    before = stored_state(seeded)

    for _ in range(3):
        client.get(f"{CASES_URL}/{case_id}")
        client.get(f"{CASES_URL}/{case_id}/events")
        client.get(f"{CASES_URL}/{case_id}/semantic-suggestions")

    assert stored_state(seeded) == before


def test_the_production_database_is_never_created(tmp_path: Path) -> None:
    """Guards the fixture wiring itself.

    Every database in this file is built from an explicit tmp_path config, so a
    regression that fell back to ``load_review_persistence_config()`` would start
    writing to the real queue. The default path must stay absent.
    """
    from review_persistence.config import PROJECT_ROOT

    assert not (PROJECT_ROOT / "storage" / "review_queue.db").exists()
