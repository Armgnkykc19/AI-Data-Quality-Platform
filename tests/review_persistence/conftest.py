from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from entity_resolution.config import EntityResolutionConfig, load_entity_resolution_config
from entity_resolution.models import ResolutionResult
from human_review.cases import generate_review_cases
from human_review.models import HumanReviewDecision, ReviewCase, ReviewWorkflowState
from human_review.reporting import resolution_snapshot
from human_review.workflow import ReviewWorkflow
from identity.models import Organization
from review_application.queues import ReviewQueue
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite.database import ReviewDatabase, open_review_database
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository
from tests.human_review.conftest import (
    make_bridge_resolution,
    make_record,
    make_review_resolution,
    match_authorization_kwargs,
)

# Readable, pinned identifiers. ``assert_opaque_id`` deliberately does not
# require the generated 32-hex form, so a fixture can name a tenant something a
# failing assertion is legible about while still exercising the production
# validation path.
ORGANIZATION_ID = "ORG-fixture-primary"
ORGANIZATION_SLUG = "fixture-primary"
REVIEW_QUEUE_ID = "RQ-fixture-primary"

SECOND_ORGANIZATION_ID = "ORG-fixture-second"
SECOND_ORGANIZATION_SLUG = "fixture-second"
SECOND_REVIEW_QUEUE_ID = "RQ-fixture-second"

PROVISIONED_AT = "2026-09-12T07:00:00Z"


class FrozenClock:
    """Deterministic clock. Advances only when a test asks it to."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now += timedelta(seconds=seconds)


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


def provision_queue(
    database: ReviewDatabase,
    *,
    organization_id: str = ORGANIZATION_ID,
    slug: str = ORGANIZATION_SLUG,
    review_queue_id: str = REVIEW_QUEUE_ID,
    queue_name: str = "default",
) -> ReviewQueue:
    """Create an organization and one queue inside it, through production code.

    Nothing in the tests inserts tenant rows by hand. Review data is owned by a
    queue and a queue by an organization, so a fixture that bypassed
    ``SqliteTenantRepository`` would be testing a graph the application cannot
    actually produce.
    """
    tenants = SqliteTenantRepository(database)
    tenants.create_organization(
        Organization.create(
            slug=slug,
            display_name=slug,
            created_at_utc=PROVISIONED_AT,
            organization_id=organization_id,
        )
    )
    return tenants.create_review_queue(
        ReviewQueue.create(
            organization_id=organization_id,
            name=queue_name,
            created_at_utc=PROVISIONED_AT,
            review_queue_id=review_queue_id,
        )
    )


def bound_repository(
    database: ReviewDatabase,
    clock: FrozenClock | None = None,
) -> SqliteReviewCaseRepository:
    """A repository on the fixture queue, provisioning it if the file is new.

    The restart tests open their own databases, so they cannot use the
    ``repository`` fixture. This gives them the same binding: the tenant rows
    are created on first use and found again after a reopen, which is also how
    a real installation behaves across a restart.
    """
    tenants = SqliteTenantRepository(database)
    queue = tenants.get_review_queue(REVIEW_QUEUE_ID) or provision_queue(database)
    if clock is None:
        return SqliteReviewCaseRepository(database, review_queue_id=queue.review_queue_id)
    return SqliteReviewCaseRepository(database, review_queue_id=queue.review_queue_id, clock=clock)


def provision_second_queue(database: ReviewDatabase) -> ReviewQueue:
    """A second tenant with its own queue, for the isolation tests."""
    return provision_queue(
        database,
        organization_id=SECOND_ORGANIZATION_ID,
        slug=SECOND_ORGANIZATION_SLUG,
        review_queue_id=SECOND_REVIEW_QUEUE_ID,
    )


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
    """A repository bound to the other tenant's queue, over the same database."""
    return SqliteReviewCaseRepository(
        database, review_queue_id=second_review_queue.review_queue_id, clock=clock
    )


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
    """Real Sprint 08 state, produced by production case generation."""
    return generate_review_cases(resolution, config=resolution_config)


@pytest.fixture
def review_case(review_state: ReviewWorkflowState) -> ReviewCase:
    assert review_state.cases, "Fixture resolution must produce at least one REVIEW case."
    return review_state.cases[0]


@pytest.fixture
def resolved_match_case(
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> ReviewCase:
    """A MATCH case produced by ReviewWorkflow.resolve_case, not hand-built.

    Persistence must never be tested against a status it invented itself.
    """
    workflow = ReviewWorkflow(review_state)
    case_id = review_state.cases[0].review_case_id
    resolved_state = workflow.resolve_case(
        case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        **match_authorization_kwargs(resolution, resolution_config),
    )
    resolved = resolved_state.case_by_id(case_id)
    assert resolved is not None
    return resolved


@pytest.fixture
def snapshot(resolution: ResolutionResult) -> dict[str, object]:
    """The reduced AUTO_MATCH snapshot, via the public Sprint 08 helper."""
    return resolution_snapshot(resolution)


@pytest.fixture
def resolved_match_state(
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> ReviewWorkflowState:
    """Workflow state after a real MATCH resolution, for persistence fixtures."""
    workflow = ReviewWorkflow(review_state)
    return workflow.resolve_case(
        review_state.cases[0].review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        **match_authorization_kwargs(resolution, resolution_config),
    )


def conflicting_bridge_resolution() -> ResolutionResult:
    """Four records, two AUTO_MATCH pairs, one REVIEW bridge, conflicting emails.

    A human MATCH on the bridge is unsafe only because the AUTO_MATCH edges pull
    rec-a and rec-d into the same component, where their emails conflict. Any
    persistence that loses an entity record or an AUTO_MATCH edge would make the
    same check pass, which is what the transitive-safety tests detect.
    """
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-c", first_name="Ali", last_name="Yilmaz", email="c@example.com"),
        make_record("rec-d", first_name="Ali", last_name="Yilmaz", email="d@example.com"),
    )
    return make_bridge_resolution(
        left_ids=("rec-a", "rec-b"),
        right_ids=("rec-c", "rec-d"),
        bridge_ids=("rec-b", "rec-c"),
        records=records,
    )


@pytest.fixture
def bridge_resolution() -> ResolutionResult:
    return conflicting_bridge_resolution()
