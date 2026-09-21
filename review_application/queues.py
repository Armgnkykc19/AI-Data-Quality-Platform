"""The review queue: an organization's container for one workflow.

A ``ReviewQueue`` is ownership and context metadata, and nothing else. It holds
no cases, decides no transitions, and has no method that could resolve one.
``ReviewWorkflow`` remains the only thing that resolves a case and
``ReviewQueueService`` the only thing that persists a resolution; a queue is
simply the name of the boundary they operate inside.

Why the queue rather than the organization owns review data
-----------------------------------------------------------

Scoping every review table directly by ``organization_id`` was a perfectly
implementable alternative -- composite keys of ``(organization_id,
review_case_id)`` would have prevented the identifier collision just as well.
It was not chosen because it models the system less accurately.

The review queue is already the natural boundary of five separate things that
existed before tenancy did:

* the stored workflow authorization context (one record set, one AUTO_MATCH
  snapshot, one entity-resolution config path),
* Sprint 08 MATCH authorization, which projects a connected component across
  *every* AUTO_MATCH edge and *every* recorded human decision in that context,
* review-case identity, which ``human_review.ids.stable_review_case_id``
  derives from the reviewed record pair,
* registration and its idempotence, which compares a context fingerprint,
* the deterministic-identifier collisions that fingerprint has to tolerate.

All five are properties of one workflow, not of a tenant. An organization with
two queues has two independent authorization graphs, and a NO_MATCH recorded in
one must not constrain a MATCH in the other. Making the organization the direct
owner would leave that distinction expressible only by convention; making the
queue the owner makes it structural, and gives tenancy a boundary that already
had to exist for correctness reasons that have nothing to do with SaaS.

Tenant ownership therefore flows one way and one way only::

    event / suggestion -> review case -> review queue -> organization

so no child table carries a redundant ``organization_id`` that could disagree
with its parent.
"""

from __future__ import annotations

from dataclasses import dataclass

from identity.errors import IdentityValidationError
from identity.ids import (
    ORGANIZATION_ID_PREFIX,
    assert_opaque_id,
    generate_opaque_id,
)

__all__ = [
    "MAX_REVIEW_QUEUE_NAME_LENGTH",
    "REVIEW_QUEUE_ID_PREFIX",
    "ReviewQueue",
    "new_review_queue_id",
]

REVIEW_QUEUE_ID_PREFIX = "RQ-"
MAX_REVIEW_QUEUE_NAME_LENGTH = 200


def new_review_queue_id() -> str:
    """Mint one opaque queue identifier.

    Random, like every other tenant-graph id, and for the same reason: a queue
    id derived from its name would change when the name changed, and every
    review case in it references the queue by id.
    """
    return generate_opaque_id(REVIEW_QUEUE_ID_PREFIX)


@dataclass(frozen=True)
class ReviewQueue:
    """One organization-owned workflow container.

    ``name`` is operator-facing and unique within its organization, so two
    organizations may both call a queue "default" without colliding. It is
    presentation, never a key: every reference from review data uses
    ``review_queue_id``.
    """

    review_queue_id: str
    organization_id: str
    name: str
    created_at_utc: str

    def __post_init__(self) -> None:
        assert_opaque_id(
            self.review_queue_id,
            prefix=REVIEW_QUEUE_ID_PREFIX,
            field_name="review_queue_id",
        )
        assert_opaque_id(
            self.organization_id,
            prefix=ORGANIZATION_ID_PREFIX,
            field_name="organization_id",
        )
        object.__setattr__(self, "name", _require_queue_name(self.name))
        if not isinstance(self.created_at_utc, str) or not self.created_at_utc.strip():
            raise IdentityValidationError("created_at_utc must be a non-empty string.")

    @classmethod
    def create(
        cls,
        *,
        organization_id: str,
        name: str,
        created_at_utc: str,
        review_queue_id: str | None = None,
    ) -> ReviewQueue:
        return cls(
            review_queue_id=review_queue_id or new_review_queue_id(),
            organization_id=organization_id,
            name=name,
            created_at_utc=created_at_utc,
        )


def _require_queue_name(value: object) -> str:
    """A queue name is trimmed and bounded, and otherwise left alone.

    No casefolding: unlike an organization slug, a queue name is not something
    an operator types as a lookup key across a whole installation, and folding
    it would make two deliberately distinct names collide inside one
    organization.
    """
    if not isinstance(value, str):
        raise IdentityValidationError("name must be a string.")
    stripped = value.strip()
    if not stripped:
        raise IdentityValidationError("name must not be empty.")
    if len(stripped) > MAX_REVIEW_QUEUE_NAME_LENGTH:
        raise IdentityValidationError(f"name exceeds {MAX_REVIEW_QUEUE_NAME_LENGTH} characters.")
    return stripped
