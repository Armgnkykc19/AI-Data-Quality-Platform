"""One organization's review queue over HTTP: four projections and one decision.

Every route here is tenant-scoped, and the scope is in the URL::

    /api/v1/organizations/{organization_id}/review-queues/{review_queue_id}/review-cases

Both segments are required by the path itself, so there is no spelling of these
endpoints in which a tenant could be defaulted, inferred from the session,
taken from a cookie, read out of the body, or guessed because the installation
happens to hold only one queue. The unscoped ``/api/v1/review-cases`` surface
Sprint 11 published is gone rather than authenticated in place: authenticating
a route that still infers its queue would leave every signed-in user reading
whatever queue the server picked at startup.

Naming a tenant is not reaching one. The scope dependency in
``review_api.security`` proves, from storage, that the organization exists, that
the queue belongs to it, that this principal is a member, and that the
membership's role carries the capability this route needs -- before any line
below runs. A route cannot skip it: the repository and the service are
themselves produced by dependencies that require a proven scope, so there is
nothing to read from and nothing to decide with until authorization has passed.

The four ``GET`` routes remain pure reads. Each calls the repository through the
``ReviewCaseRepository`` Protocol, projects what came back into an explicit
model, and returns it. None of them resolves a case, builds a ``ReviewWorkflow``,
loads a workflow bundle, rebuilds an AUTO_MATCH graph, reads an
entity-resolution config, evaluates authorization, writes a row, bumps a
version, or appends an event -- and ``tests/review_api/test_read_endpoints.py``
proves it by handing those routes a repository whose write methods raise.

``POST .../resolve`` is the one authoritative write, and it is equally thin: it
forwards the decision, the version, and the *server-derived* reviewer id to
``ReviewQueueService.resolve_case`` and projects the result. The service owns the
entire resolution path. Nothing in this module reads authorization material, and
nothing in it can: the reads depend on the repository and the write depends on
the service, so no route has both a way to ask about the AUTO_MATCH graph and a
way to act on it.

Two read behaviours are worth stating because they are easy to get subtly wrong.

``list_events`` and ``list_semantic_suggestions`` return an empty tuple for an
unknown case, exactly as they do for a known case with no history. Neither can
distinguish the two, so both routes call ``get_case`` first purely to obtain
the 404. Skipping it would answer "200 []" for a case that does not exist.

Pagination is the repository's job: ``count_cases`` reports the filtered
total and ``list_cases(limit=..., offset=...)`` returns one page, preserving
the repository's ordering. Both the filter and the page are queue-local
without this module doing anything to make them so: the repository it was
handed can only see one queue, so another tenant's cases cannot enter a
total, a count, or a page.

Every route is ``async def`` and calls the synchronous repository inline. See
``review_api.dependencies`` for why that is load-bearing and temporary.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from human_review.models import ReviewStatus
from review_api.models import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MAX_REVIEW_CASE_ID_LENGTH,
    ResolveReviewCaseRequest,
    ResolveReviewCaseResponse,
    ReviewCaseDetail,
    ReviewCaseListResponse,
    ReviewEventRead,
    SemanticSuggestionRead,
    to_case_detail,
    to_case_summary,
    to_event,
    to_resolution_response,
    to_semantic_suggestion,
)
from review_api.security import (
    ResolveScopeDep,
    ScopedRepositoryDep,
    ScopedServiceDep,
    require_trusted_origin,
)

router = APIRouter(
    prefix="/api/v1/organizations/{organization_id}/review-queues/{review_queue_id}/review-cases",
    tags=["review-cases"],
)

# Declared as a route dependency rather than called in the body, so FastAPI
# resolves it before anything else runs. A forged cross-site request therefore
# costs no session lookup, no membership read, and no domain work. The same
# validator the authentication routes use -- there is exactly one Origin policy
# in this API.
TrustedOrigin = Depends(require_trusted_origin)

ReviewCaseIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_REVIEW_CASE_ID_LENGTH,
        description="Stable Sprint 08 review case identifier, unique within this queue.",
    ),
]


@router.get("", summary="List review cases in one queue")
async def list_review_cases(
    repository: ScopedRepositoryDep,
    status: Annotated[ReviewStatus | None, Query(description="Exact review status.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewCaseListResponse:
    """One page of this queue, optionally filtered by status.

    ``status`` is the ``ReviewStatus`` enum, so FastAPI matches it exactly:
    ``PENDING`` is accepted and ``pending`` is a 422. Coercing case would mean
    this layer deciding what a status token means, and there is already exactly
    one answer to that in ``human_review.models``.

    Filtering is the repository's job and slicing is this route's. ``total``
    counts the filtered set before the slice, so an offset past the end returns
    an empty page that still says how many cases matched -- and every number
    here counts only this queue, because the repository cannot see another.
    """
    total = repository.count_cases(status=status)
    page = repository.list_cases(status=status, limit=limit, offset=offset)
    items = [to_case_summary(persisted) for persisted in page]
    return ReviewCaseListResponse(
        items=items,
        count=len(items),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{review_case_id}", summary="Get one review case")
async def get_review_case(
    review_case_id: ReviewCaseIdPath,
    repository: ScopedRepositoryDep,
) -> ReviewCaseDetail:
    """Full detail for one case in this queue, including the version to echo.

    ``get_case`` raises ``ReviewCaseNotFoundError`` for an id this queue does
    not hold, which the registered handler turns into a 404 with a static
    message. The same id existing in another tenant's queue changes nothing
    here: the repository's every statement carries its queue predicate, so a
    case that is not in *this* queue is simply absent.
    """
    return to_case_detail(repository.get_case(review_case_id))


@router.get("/{review_case_id}/events", summary="List a case's history")
async def list_case_events(
    review_case_id: ReviewCaseIdPath,
    repository: ScopedRepositoryDep,
) -> list[ReviewEventRead]:
    """The append-only history, oldest first, in the repository's own order.

    ``get_case`` runs first only to produce the 404: ``list_events`` answers an
    unknown case and an eventless case identically.
    """
    repository.get_case(review_case_id)
    return [to_event(event) for event in repository.list_events(review_case_id)]


@router.get("/{review_case_id}/semantic-suggestions", summary="List advisory suggestions")
async def list_case_semantic_suggestions(
    review_case_id: ReviewCaseIdPath,
    repository: ScopedRepositoryDep,
) -> list[SemanticSuggestionRead]:
    """Stored Sprint 09 observations for one case. Reading only; never generating.

    No provider is contacted and no suggestion is created. Sprint 13 does not
    expose live semantic generation over HTTP at all: it is budget-controlled,
    disabled by default, and would put a network call and a spend decision in a
    request path that a VIEWER can reach.

    Observation order is preserved and means nothing beyond order. None of these
    is operative -- ``ReviewCase.status`` remains the only statement about what
    was decided.
    """
    repository.get_case(review_case_id)
    return [
        to_semantic_suggestion(suggestion)
        for suggestion in repository.list_semantic_suggestions(review_case_id)
    ]


@router.post(
    "/{review_case_id}/resolve",
    status_code=200,
    dependencies=[TrustedOrigin],
    summary="Record a human decision",
)
async def resolve_review_case(
    review_case_id: ReviewCaseIdPath,
    body: ResolveReviewCaseRequest,
    scope: ResolveScopeDep,
    service: ScopedServiceDep,
) -> ResolveReviewCaseResponse:
    """Apply one human decision, through the Sprint 08 authority and nothing else.

    This route decides nothing. It validates two transport fields, adds the
    reviewer identity the server already established, hands all three to
    ``ReviewQueueService.resolve_case``, and projects what came back.

    **``reviewer_id`` comes from ``scope.user_id``.** That is the authenticated
    principal's opaque user id -- the durable audit identity ``identity`` mints
    once per person and never changes -- and there is no request field that
    could contribute to it. A body carrying ``reviewer_id`` is a 422 from
    ``extra="forbid"``, so spoofing another reviewer fails at validation rather
    than being silently discarded.

    **Two authorizations, and they answer different questions.** Reaching this
    line means tenant authorization confirmed the caller may *attempt* a
    resolution in this queue. Whether *this* MATCH is safe is Sprint 08's
    answer, reached from review evidence, and a REVIEWER role does not
    influence it: the deterministic boundary check still refuses a merge that
    would unite conflicting identities, and that refusal is still
    ``MATCH_NOT_AUTHORIZED`` rather than a 403. Collapsing the two would let a
    role decide a question about records.

    Every input the authority actually reads -- the AUTO_MATCH graph, the
    entity records, the entity-resolution config and its thresholds, the
    queue's current state -- is loaded from storage by the service, scoped to
    this queue. None of it can be supplied, influenced, or narrowed from here.

    That matters because Sprint 08 authorization is not a property of the
    reviewed pair. It projects a connected component across every AUTO_MATCH
    edge and every recorded human decision *in this queue*, so a MATCH that is
    safe in isolation can be unsafe in context, and a NO_MATCH recorded in
    another tenant's queue constrains nothing here.

    No write happens before the domain approves. The service calls
    ``ReviewWorkflow.resolve_case`` first and opens the storage transaction only
    with what the workflow returned, so a refusal leaves no version bump, no
    timestamp change and no history row.

    ``200``, not ``201``: the case already existed and was transitioned.

    ``expected_version`` is mandatory and is never defaulted to the stored
    value. Optimistic concurrency is the contract -- a reviewer decides against
    the version they were shown, and one who lost the race is told so rather
    than having their decision applied to a queue state they never saw.
    """
    result = service.resolve_case(
        review_case_id,
        decision=body.decision,
        reviewer_id=scope.user_id,
        expected_version=body.expected_version,
    )
    return to_resolution_response(result)
