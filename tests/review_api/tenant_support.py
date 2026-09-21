"""Tenant wiring for the API tests, in the two shapes the suite actually needs.

There are deliberately two, because they prove different things and neither
substitutes for the other.

**Fakes, here.** The projection, validation and error-mapping tests care about
what a route does with a queue, not about how a queue was authorized. They get
``FakeTenantDirectory`` and ``FakeQueueBinder``, and they override the principal
dependency instead of logging in -- so a test about pagination does not fail
because of a cookie. What is *not* faked in those tests is the policy itself:
they run the real ``TenantAuthorizationService`` over the fake directory, so a
route that forgot its scope dependency fails there too.

**Production-shaped wiring, in ``auth_fixtures``.** The authorization matrix,
the leakage policy, membership freshness, the one-touch guarantee, Origin
protection and reviewer identity are all properties of the real composition:
real SQLite, real Argon2id, real sessions, real memberships, real queue binding.
``test_tenant_authorization`` drives that fixture and never touches this
module's fakes, because a fake directory would make the membership read a
test's own invention.

Every identifier below is readable rather than generated. ``assert_opaque_id``
requires a type prefix and no whitespace, not the 32-hex production form, so a
failing assertion can say ``ORG-api-fixture`` instead of a random digest.
"""

from __future__ import annotations

from identity.ids import SESSION_ID_PREFIX
from identity.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
)
from identity.sessions import Session, hash_session_token
from review_api.security import AuthenticatedPrincipal
from review_application import ReviewCaseRepository, ReviewQueue, ReviewQueueService

ORGANIZATION_ID = "ORG-api-fixture"
ORGANIZATION_SLUG = "api-fixture"
REVIEW_QUEUE_ID = "RQ-api-fixture"
USER_ID = "USR-api-fixture"
DISPLAY_NAME = "Fixture Reviewer"

# A loopback origin, because ``AuthHttpConfig`` refuses an insecure cookie
# alongside anything else -- the same constraint the committed local config
# lives under.
TEST_ORIGIN = "http://127.0.0.1:5173"

FIXTURE_TIMESTAMP = "2026-09-14T08:00:00Z"


def tenant_path(
    *,
    organization_id: str = ORGANIZATION_ID,
    review_queue_id: str = REVIEW_QUEUE_ID,
) -> str:
    """The scoped review-cases URL prefix for one organization and one queue.

    Built rather than formatted at each call site so the shape of the tenant
    path exists in exactly one place in the tests, as it does in exactly one
    place in ``review_api.routes.review_cases``.
    """
    return f"/api/v1/organizations/{organization_id}/review-queues/{review_queue_id}/review-cases"


def make_organization(organization_id: str = ORGANIZATION_ID) -> Organization:
    return Organization(
        organization_id=organization_id,
        slug=organization_id.removeprefix("ORG-"),
        display_name=organization_id,
        status=OrganizationStatus.ACTIVE,
        created_at_utc=FIXTURE_TIMESTAMP,
    )


def make_queue(
    review_queue_id: str = REVIEW_QUEUE_ID,
    *,
    organization_id: str = ORGANIZATION_ID,
) -> ReviewQueue:
    return ReviewQueue(
        review_queue_id=review_queue_id,
        organization_id=organization_id,
        name=review_queue_id.removeprefix("RQ-"),
        created_at_utc=FIXTURE_TIMESTAMP,
    )


def make_principal(user_id: str = USER_ID) -> AuthenticatedPrincipal:
    """A principal with a real ``Session``, not a stand-in object.

    ``Session`` validates its own fields, so building a genuine one keeps these
    fixtures honest about the shape the production dependency actually returns.
    The token digest is of a throwaway string that is never a credential for
    anything: nothing here reaches a session store.
    """
    session = Session(
        session_id=f"{SESSION_ID_PREFIX}api-fixture",
        user_id=user_id,
        token_hash=hash_session_token("fixture-token"),
        created_at_utc=FIXTURE_TIMESTAMP,
        last_activity_at_utc=FIXTURE_TIMESTAMP,
        idle_expires_at_utc="2026-09-14T09:00:00Z",
        absolute_expires_at_utc="2026-09-14T16:00:00Z",
    )
    return AuthenticatedPrincipal(
        user_id=user_id,
        display_name=DISPLAY_NAME,
        session_id=session.session_id,
        session=session,
    )


class FakeTenantDirectory:
    """One organization, one queue, and at most one membership.

    Structurally a ``TenantDirectory``. It stores nothing else, so a test that
    wanted to reach a second tenant through it simply cannot -- the cross-tenant
    behaviour is proven against real storage, where it means something.
    """

    def __init__(
        self,
        *,
        role: MembershipRole | None = MembershipRole.REVIEWER,
        user_id: str = USER_ID,
        organization_id: str = ORGANIZATION_ID,
        review_queue_id: str = REVIEW_QUEUE_ID,
    ) -> None:
        self._organization = make_organization(organization_id)
        self._queue = make_queue(review_queue_id, organization_id=organization_id)
        self._membership = (
            None
            if role is None
            else OrganizationMembership(
                membership_id="MEM-api-fixture",
                organization_id=organization_id,
                user_id=user_id,
                role=role,
                created_at_utc=FIXTURE_TIMESTAMP,
            )
        )
        self._user_id = user_id

    def get_organization(self, organization_id: str) -> Organization | None:
        if organization_id != self._organization.organization_id:
            return None
        return self._organization

    def get_review_queue(self, review_queue_id: str) -> ReviewQueue | None:
        if review_queue_id != self._queue.review_queue_id:
            return None
        return self._queue

    def get_membership(
        self,
        *,
        organization_id: str,
        user_id: str,
    ) -> OrganizationMembership | None:
        if self._membership is None:
            return None
        if organization_id != self._membership.organization_id or user_id != self._user_id:
            return None
        return self._membership


class FakeQueueBinder:
    """Hands out one pre-built repository or service, and checks what was asked for.

    The assertion is the point. A route that bound the wrong queue -- or that
    reached a binder without a proven scope and passed something defaulted --
    fails here rather than quietly serving the fixture's data anyway. The
    recorded ids let a test state that the queue the URL named is the queue the
    repository was built for.
    """

    def __init__(
        self,
        *,
        repository: ReviewCaseRepository | None = None,
        service: ReviewQueueService | None = None,
        review_queue_id: str = REVIEW_QUEUE_ID,
    ) -> None:
        self._repository = repository
        self._service = service
        self._review_queue_id = review_queue_id
        self.repository_requests: list[str] = []
        self.service_requests: list[str] = []

    def repository_for(self, review_queue_id: str) -> ReviewCaseRepository:
        self.repository_requests.append(review_queue_id)
        assert review_queue_id == self._review_queue_id, (
            f"a route asked for queue {review_queue_id!r}, not the authorized "
            f"{self._review_queue_id!r}"
        )
        assert self._repository is not None, "this binder was built without a repository"
        return self._repository

    def service_for(self, review_queue_id: str) -> ReviewQueueService:
        self.service_requests.append(review_queue_id)
        assert review_queue_id == self._review_queue_id, (
            f"a route asked for queue {review_queue_id!r}, not the authorized "
            f"{self._review_queue_id!r}"
        )
        assert self._service is not None, "this binder was built without a service"
        return self._service
