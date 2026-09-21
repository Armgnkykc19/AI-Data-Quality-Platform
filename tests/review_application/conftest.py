from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from entity_resolution.config import EntityResolutionConfig, load_entity_resolution_config
from entity_resolution.models import ResolutionResult
from human_review.cases import generate_review_cases
from human_review.models import ReviewCase, ReviewWorkflowState
from human_review.reporting import resolution_snapshot
from review_application.queues import ReviewQueue
from review_application.service import ReviewQueueService
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite.database import ReviewDatabase, open_review_database
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.human_review.conftest import make_review_resolution
from tests.review_persistence.conftest import (
    FrozenClock,
    provision_queue,
    provision_second_queue,
)

FROZEN_NOW = "2026-09-11T12:00:00Z"

# The real config the queue is generated with. Persisting this path is what
# lets the service load the same thresholds later instead of guessing.
ENTITY_RESOLUTION_CONFIG_PATH = "configs/entity_resolution.yaml"


@pytest.fixture
def resolution_config() -> EntityResolutionConfig:
    return load_entity_resolution_config()


@pytest.fixture
def resolution() -> ResolutionResult:
    return make_review_resolution("a-1", "a-2")


@pytest.fixture
def review_state(
    resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> ReviewWorkflowState:
    """Real Sprint 08 workflow state, generated through production code paths."""
    return generate_review_cases(resolution, config=resolution_config)


@pytest.fixture
def review_case(review_state: ReviewWorkflowState) -> ReviewCase:
    assert review_state.cases, "Fixture resolution must produce at least one REVIEW case."
    return review_state.cases[0]


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def persistence_config(tmp_path: Path) -> ReviewPersistenceConfig:
    """Always tmp_path. No test may touch storage/review_queue.db."""
    return ReviewPersistenceConfig(
        database_path=tmp_path / "review_queue.db",
        busy_timeout_ms=2000,
        journal_mode="WAL",
    )


@pytest.fixture
def database(
    persistence_config: ReviewPersistenceConfig,
    clock: FrozenClock,
) -> Iterator[ReviewDatabase]:
    db = open_review_database(persistence_config, clock=clock)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def review_queue(database: ReviewDatabase) -> ReviewQueue:
    return provision_queue(database)


@pytest.fixture
def second_review_queue(database: ReviewDatabase) -> ReviewQueue:
    return provision_second_queue(database)


@pytest.fixture
def repository(
    database: ReviewDatabase,
    clock: FrozenClock,
    review_queue: ReviewQueue,
) -> SqliteReviewCaseRepository:
    return SqliteReviewCaseRepository(
        database, review_queue_id=review_queue.review_queue_id, clock=clock
    )


@pytest.fixture
def second_repository(
    database: ReviewDatabase,
    clock: FrozenClock,
    second_review_queue: ReviewQueue,
) -> SqliteReviewCaseRepository:
    return SqliteReviewCaseRepository(
        database, review_queue_id=second_review_queue.review_queue_id, clock=clock
    )


@pytest.fixture
def service(repository: SqliteReviewCaseRepository, clock: FrozenClock) -> ReviewQueueService:
    return ReviewQueueService(repository, clock=clock)


@pytest.fixture
def second_service(
    second_repository: SqliteReviewCaseRepository,
    clock: FrozenClock,
) -> ReviewQueueService:
    """A service over the other tenant's queue, sharing one database and clock."""
    return ReviewQueueService(second_repository, clock=clock)


def register(
    repository: SqliteReviewCaseRepository,
    state: ReviewWorkflowState,
    resolution: ResolutionResult,
    *,
    entity_resolution_config_path: str | None = ENTITY_RESOLUTION_CONFIG_PATH,
) -> None:
    """Persist a workflow with the authorization context it was generated from."""
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=entity_resolution_config_path,
    )


@pytest.fixture
def registered_case(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    review_case: ReviewCase,
) -> str:
    """A single stored PENDING case whose MATCH is safe. Returns its id."""
    register(repository, review_state, resolution)
    return review_case.review_case_id
