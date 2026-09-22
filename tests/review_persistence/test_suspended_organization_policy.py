"""What a SUSPENDED organization means on the operator side.

``OrganizationStatus`` is defined as "whether this organization's queues may be
worked on". Sprint 13 enforced that over HTTP: ``review_api.tenancy`` refuses
the whole tenant scope and answers a generic 404, because telling a caller that
a tenant exists but is suspended would publish a customer's commercial state.

Nothing enforced it anywhere else. Creating a queue, granting a membership and
registering a workflow are all ordinary business operations, and all three
succeeded in a suspended organization -- so "suspended" meant only "unreachable
over HTTP" while an operator command could keep growing the tenant.

Sprint 14 Phase A closes that, and the enforcement point is deliberate. It is
in the repositories rather than in the CLI, because the repositories are the
boundary every supported caller already passes through: the operator commands,
the bootstrap helpers, and the tests all reach for them. A check in
``manage_human_review.py`` would have left all three of those open.

Two things this policy is **not**. It is not a read restriction: suspension
makes a tenant inert, it does not hide or delete what is already stored. And it
is not the HTTP leakage policy -- an operator running a local administrative
command against a database they already hold is told plainly what is wrong,
because there is no existence to leak and an unexplained refusal would only
invite a workaround.
"""

from __future__ import annotations

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import ResolutionResult
from human_review.cases import generate_review_cases
from human_review.models import ReviewWorkflowState
from human_review.reporting import resolution_snapshot
from identity.errors import IdentityNotFoundError, OrganizationNotActiveError
from identity.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    User,
)
from review_application.queues import ReviewQueue
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository

SUSPENDED_ID = "ORG-suspended-fixture"
SUSPENDED_SLUG = "suspended-fixture"
AT = "2026-09-12T07:00:00Z"


@pytest.fixture
def tenants(database: ReviewDatabase) -> SqliteTenantRepository:
    return SqliteTenantRepository(database)


@pytest.fixture
def suspended_organization(tenants: SqliteTenantRepository) -> Organization:
    return tenants.create_organization(
        Organization.create(
            slug=SUSPENDED_SLUG,
            display_name=SUSPENDED_SLUG,
            created_at_utc=AT,
            organization_id=SUSPENDED_ID,
            status=OrganizationStatus.SUSPENDED,
        )
    )


@pytest.fixture
def a_user(tenants: SqliteTenantRepository) -> User:
    return tenants.create_user(
        User.create(
            email="suspended-policy@example.test",
            display_name="policy user",
            created_at_utc=AT,
            user_id="USR-suspended-policy",
        )
    )


# --------------------------------------------------------------------------
# Refused in a suspended organization
# --------------------------------------------------------------------------


def test_queue_creation_is_refused(
    tenants: SqliteTenantRepository,
    suspended_organization: Organization,
) -> None:
    with pytest.raises(OrganizationNotActiveError, match="SUSPENDED"):
        tenants.create_review_queue(
            ReviewQueue.create(
                organization_id=suspended_organization.organization_id,
                name="new-queue",
                created_at_utc=AT,
            )
        )

    assert tenants.list_review_queues(suspended_organization.organization_id) == ()


def test_membership_creation_is_refused(
    tenants: SqliteTenantRepository,
    suspended_organization: Organization,
    a_user: User,
) -> None:
    with pytest.raises(OrganizationNotActiveError, match="SUSPENDED"):
        tenants.create_membership(
            OrganizationMembership.create(
                organization_id=suspended_organization.organization_id,
                user_id=a_user.user_id,
                role=MembershipRole.REVIEWER,
                created_at_utc=AT,
            )
        )

    assert (
        tenants.get_membership(
            organization_id=suspended_organization.organization_id,
            user_id=a_user.user_id,
        )
        is None
    )


def test_workflow_registration_into_a_suspended_tenants_queue_is_refused(
    database: ReviewDatabase,
    tenants: SqliteTenantRepository,
    resolution: ResolutionResult,
    review_state: ReviewWorkflowState,
) -> None:
    """The queue was created while the tenant was ACTIVE, then suspended.

    This is the ordering that matters: the queue is perfectly valid and the
    workflow is perfectly valid, and registering it is still refused, because
    registration is business work rather than administration.
    """
    organization = tenants.create_organization(
        Organization.create(
            slug="was-active",
            display_name="was active",
            created_at_utc=AT,
            organization_id="ORG-was-active",
        )
    )
    queue = tenants.create_review_queue(
        ReviewQueue.create(
            organization_id=organization.organization_id,
            name="q",
            created_at_utc=AT,
            review_queue_id="RQ-was-active",
        )
    )
    database.connect().execute(
        "UPDATE organizations SET status = ? WHERE organization_id = ?",
        (OrganizationStatus.SUSPENDED.value, organization.organization_id),
    )

    repository = SqliteReviewCaseRepository(database, review_queue_id=queue.review_queue_id)

    with pytest.raises(OrganizationNotActiveError, match="SUSPENDED"):
        repository.register_workflow(
            review_state,
            entity_records=resolution.records,
            resolution_snapshot=resolution_snapshot(resolution),
            entity_resolution_config_path="configs/entity_resolution.yaml",
        )

    assert repository.list_cases() == ()
    assert repository.workflow_context() is None


def test_an_unknown_organization_is_still_reported_as_unknown(
    tenants: SqliteTenantRepository,
    a_user: User,
) -> None:
    """ "No such organization" and "that one is suspended" stay distinguishable.

    They are different operator problems with different fixes, and this is a
    local administrative context rather than the HTTP surface, so collapsing
    them would only make a refusal harder to act on.
    """
    with pytest.raises(IdentityNotFoundError, match="not stored"):
        tenants.create_membership(
            OrganizationMembership.create(
                organization_id="ORG-does-not-exist",
                user_id=a_user.user_id,
                role=MembershipRole.VIEWER,
                created_at_utc=AT,
            )
        )


# --------------------------------------------------------------------------
# Unaffected: ACTIVE work, and data already stored
# --------------------------------------------------------------------------


def test_the_same_operations_succeed_in_an_active_organization(
    tenants: SqliteTenantRepository,
    review_queue: ReviewQueue,
    a_user: User,
    repository: SqliteReviewCaseRepository,
    resolution: ResolutionResult,
    review_state: ReviewWorkflowState,
) -> None:
    """The policy must refuse suspension, not ordinary work."""
    membership = tenants.create_membership(
        OrganizationMembership.create(
            organization_id=review_queue.organization_id,
            user_id=a_user.user_id,
            role=MembershipRole.REVIEWER,
            created_at_utc=AT,
        )
    )
    assert membership.role is MembershipRole.REVIEWER

    second = tenants.create_review_queue(
        ReviewQueue.create(
            organization_id=review_queue.organization_id,
            name="another-queue",
            created_at_utc=AT,
        )
    )
    assert second.organization_id == review_queue.organization_id

    stored = repository.register_workflow(
        review_state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    assert len(stored) == len(review_state.cases)


def test_suspension_does_not_remove_or_hide_existing_queue_data(
    database: ReviewDatabase,
    repository: SqliteReviewCaseRepository,
    review_queue: ReviewQueue,
    resolution: ResolutionResult,
    review_state: ReviewWorkflowState,
    resolution_config: EntityResolutionConfig,
) -> None:
    """Suspension makes a tenant inert. It is not a delete and not a read block.

    The stored cases, the workflow context and the bundle all remain readable,
    which is what makes suspension reversible and what keeps an operator able to
    verify or export a suspended tenant's data.
    """
    repository.register_workflow(
        review_state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    before = repository.list_cases()
    assert before

    database.connect().execute(
        "UPDATE organizations SET status = ? WHERE organization_id = ?",
        (OrganizationStatus.SUSPENDED.value, review_queue.organization_id),
    )

    assert repository.list_cases() == before
    assert repository.workflow_context() is not None
    assert repository.load_workflow_bundle().cases() == review_state.cases


def test_registering_the_same_workflow_again_is_refused_rather_than_partially_applied(
    database: ReviewDatabase,
    repository: SqliteReviewCaseRepository,
    review_queue: ReviewQueue,
    resolution: ResolutionResult,
    review_state: ReviewWorkflowState,
) -> None:
    """Suspending mid-life must not corrupt what a re-registration would touch.

    The first registration happened while ACTIVE. After suspension the
    idempotent re-run is refused, and the stored context and cases are exactly
    what they were -- the refusal happens before anything is written.
    """
    repository.register_workflow(
        review_state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    context_before = repository.workflow_context()
    cases_before = repository.list_cases()

    database.connect().execute(
        "UPDATE organizations SET status = ? WHERE organization_id = ?",
        (OrganizationStatus.SUSPENDED.value, review_queue.organization_id),
    )

    with pytest.raises(OrganizationNotActiveError):
        repository.register_workflow(
            review_state,
            entity_records=resolution.records,
            resolution_snapshot=resolution_snapshot(resolution),
            entity_resolution_config_path="configs/entity_resolution.yaml",
        )

    assert repository.list_cases() == cases_before
    after = repository.workflow_context()
    assert after is not None and context_before is not None
    assert after.updated_at_utc == context_before.updated_at_utc


def test_generated_cases_are_unaffected_by_status(
    resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> None:
    """Case generation is deterministic and knows nothing about tenants.

    Pinned so the policy stays a persistence and authorization concern. If
    suspension ever changed what the pipeline produced, the same input would
    stop producing the same review cases.
    """
    assert generate_review_cases(resolution, config=resolution_config).cases == (
        generate_review_cases(resolution, config=resolution_config).cases
    )
