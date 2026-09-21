"""Tenant authorization: which queue may this user reach, and what may they do in it?

This module answers exactly one question, and it is not "who is this". Identity
is already settled by the time anything here runs -- ``security`` resolved the
session cookie into an ``AuthenticatedPrincipal`` and renewed it once. What is
left is the access decision, and it is made against *current* server-side state
every time, never against anything the session captured.

Three layers, kept apart on purpose::

    authentication          who is the user             identity + security
    tenant authorization    may they reach this queue   THIS MODULE
    Sprint 08 human review  is this MATCH safe          human_review

Collapsing any two would be a security regression rather than a simplification.
A tenant authorization that could see review evidence could eventually overrule
Sprint 08; a Sprint 08 check that could see a role would let a REVIEWER merge
records the evidence forbids. So nothing here imports ``human_review``, and
:class:`TenantAuthorizationService` has no method that decides anything about a
review case.

**Nothing here trusts a caller.** The organization and the queue arrive as path
segments -- which is the caller naming what they want to reach, not the caller
deciding what they are allowed to reach. Every one of the four facts below is
read from storage:

1. the organization exists,
2. the queue exists **and belongs to that organization**,
3. the user holds a membership in that organization,
4. the membership's role carries the required capability.

The first three answer 404 and the fourth answers 403, and that split is the
whole existence-leakage policy. A caller who is not a member of an organization
must not be able to tell a real organization from an invented one, a real queue
from an invented one, or a queue owned by someone else from a queue that does
not exist -- all four are "not found". 403 is reachable only once membership is
established, at which point the caller already knows the tenant exists because
they belong to it.

**No RBAC framework, and no room for one.** Two roles, two capabilities, one
frozen table. A permission row, a role hierarchy, a wildcard grant or a policy
DSL would be machinery for distinctions this product does not draw: every other
privileged action -- creating a tenant, registering a workflow, granting a
membership -- is an operator action performed through the CLI, so a role for it
would gate nothing that exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from identity.models import MembershipRole, Organization, OrganizationMembership
from review_api.errors import TenantCapabilityError, TenantScopeNotVisibleError
from review_application import ReviewCaseRepository, ReviewQueue, ReviewQueueService

__all__ = [
    "ROLE_CAPABILITIES",
    "Capability",
    "ReviewQueueBinder",
    "TenantAuthorizationService",
    "TenantDirectory",
    "TenantScope",
]


class Capability(StrEnum):
    """What a request needs permission to do, named for the action and not the role.

    Routes ask for a capability rather than testing ``role == REVIEWER``. The
    difference matters the day a third role appears: with capabilities the
    change is one row in the table below, and with role comparisons it is a
    grep through every route.
    """

    READ_REVIEW_QUEUE = "READ_REVIEW_QUEUE"
    RESOLVE_REVIEW_CASE = "RESOLVE_REVIEW_CASE"


# The complete policy. Read-only at runtime -- a mutable module-level dict is a
# global an import could quietly rewrite, and this one decides who may record a
# human decision.
#
# REVIEWER's read capability is written out rather than derived from VIEWER's
# set. A derivation ("reviewer is viewer plus resolve") is a role hierarchy
# expressed in one line, and role hierarchies are where a capability arrives
# somewhere nobody meant it to.
ROLE_CAPABILITIES: Mapping[MembershipRole, frozenset[Capability]] = MappingProxyType(
    {
        MembershipRole.VIEWER: frozenset({Capability.READ_REVIEW_QUEUE}),
        MembershipRole.REVIEWER: frozenset(
            {Capability.READ_REVIEW_QUEUE, Capability.RESOLVE_REVIEW_CASE}
        ),
    }
)


class TenantDirectory(Protocol):
    """The authoritative tenant-graph reads an access decision is made from.

    Three lookups, no writes, and no listing. There is deliberately no
    ``list_organizations_for_user`` here: a request names its tenant
    explicitly, so nothing in this path ever needs to enumerate what a user can
    see, and a method that could would be one an endpoint might eventually use
    to answer "which tenants exist".

    ``SqliteTenantRepository`` satisfies this structurally. Typing it as a
    Protocol keeps ``review_persistence`` out of everything above the
    composition root, exactly as ``ReviewCaseRepository`` does for the queue.
    """

    def get_organization(self, organization_id: str) -> Organization | None: ...

    def get_review_queue(self, review_queue_id: str) -> ReviewQueue | None: ...

    def get_membership(
        self,
        *,
        organization_id: str,
        user_id: str,
    ) -> OrganizationMembership | None: ...


class ReviewQueueBinder(Protocol):
    """Builds the queue-scoped review layer for one explicitly authorized queue.

    The ``review_queue_id`` passed to either method is the one tenant
    authorization just proved this user may reach. It is not a "current queue"
    held anywhere: there is no setter, no default, and no state that could make
    two requests for different queues see each other's binding.

    That is why this is a factory rather than a pre-bound repository on
    application state. Sprint 13 Phase B made ``SqliteReviewCaseRepository``
    queue-bound at construction precisely so that no query can forget its
    tenant predicate; binding one instance at startup would have thrown that
    away by making the whole application single-tenant again.
    """

    def repository_for(self, review_queue_id: str) -> ReviewCaseRepository: ...

    def service_for(self, review_queue_id: str) -> ReviewQueueService: ...


@dataclass(frozen=True)
class TenantScope:
    """The proven result of one access decision, and the only way to act on it.

    A route receives one of these or an exception -- there is no partially
    authorized state and no boolean a caller could forget to check. Everything
    on it came from storage during this request: ``review_queue_id`` is the
    queue the URL named *and* storage confirmed belongs to ``organization_id``,
    and ``role`` is the role the membership table held a moment ago.

    ``user_id`` is carried here so the resolution route has a server-derived
    reviewer identity to hand the domain. It is the authenticated principal's
    id and nothing else; no request field contributes to it.
    """

    user_id: str
    organization_id: str
    review_queue_id: str
    role: MembershipRole
    capability: Capability


class TenantAuthorizationService:
    """Turns an authenticated user plus a named tenant scope into a proven scope.

    Holds no session, resolves no cookie, and touches nothing. It consumes an
    identity that has already been established, which is what keeps one request
    to exactly one session touch: if this re-resolved the session it would renew
    the idle window a second time, and "one request, one activity record" would
    quietly become false.
    """

    def __init__(self, directory: TenantDirectory) -> None:
        self._directory = directory

    def authorize(
        self,
        *,
        user_id: str,
        organization_id: str,
        review_queue_id: str,
        capability: Capability,
    ) -> TenantScope:
        """Prove all four facts, in this order, or raise.

        ``TenantScopeNotVisibleError`` carries a reason for the server log only.
        It never reaches a response: the handler in ``review_api.errors`` answers
        with the same static 404 body for all four, so "no such organization",
        "no such queue", "that queue belongs to someone else" and "you are not a
        member" are indistinguishable from outside.
        """
        organization = self._directory.get_organization(organization_id)
        if organization is None:
            raise TenantScopeNotVisibleError("unknown organization")

        queue = self._directory.get_review_queue(review_queue_id)
        if queue is None:
            raise TenantScopeNotVisibleError("unknown review queue")
        if queue.organization_id != organization.organization_id:
            # Never silently corrected to the queue's real owner. Serving it
            # under the organization the caller named would let a member of one
            # tenant address another tenant's queue by pairing it with their own
            # organization id.
            raise TenantScopeNotVisibleError("review queue belongs to another organization")

        membership = self._directory.get_membership(
            organization_id=organization.organization_id,
            user_id=user_id,
        )
        if membership is None:
            # Read now, not at login. A membership granted or removed a second
            # ago is in force for this request.
            raise TenantScopeNotVisibleError("no membership in this organization")

        if capability not in ROLE_CAPABILITIES[membership.role]:
            # The one condition that is *not* a 404. The caller has already
            # proven they belong here, so telling them their role is
            # insufficient discloses nothing they did not already know.
            raise TenantCapabilityError(f"role {membership.role.value} lacks {capability.value}")

        return TenantScope(
            user_id=user_id,
            organization_id=organization.organization_id,
            review_queue_id=queue.review_queue_id,
            role=membership.role,
            capability=capability,
        )
