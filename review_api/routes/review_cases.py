"""The review queue over HTTP: four projections and one decision.

The four ``GET`` routes are pure reads. Each calls the repository through the
``ReviewCaseRepository`` Protocol, projects what came back into an explicit
model, and returns it. None of them resolves a case, builds a ``ReviewWorkflow``,
loads a workflow bundle, rebuilds an AUTO_MATCH graph, reads an
entity-resolution config, evaluates authorization, writes a row, bumps a
version, or appends an event -- and ``tests/review_api/test_read_endpoints.py``
proves it by handing those routes a repository whose write methods raise.

``POST .../resolve`` is the one authoritative write, and it is equally thin: it
forwards three transport fields to ``ReviewQueueService.resolve_case`` and
projects the result. The service owns the entire resolution path. Nothing in
this module reads authorization material, and nothing in it can: the reads
depend on the repository and the write depends on the service, so no route has
both a way to ask about the AUTO_MATCH graph and a way to act on it.

Two read behaviours are worth stating because they are easy to get subtly wrong.

``list_events`` and ``list_semantic_suggestions`` return an empty tuple for an
unknown case, exactly as they do for a known case with no history. Neither can
distinguish the two, so both routes call ``get_case`` first purely to obtain
the 404. Skipping it would answer "200 []" for a case that does not exist.

Pagination happens here, after the repository returns its matching sequence,
and the repository's ordering is preserved exactly. That is a deliberate
limitation rather than an oversight: pushing limit/offset into SQL would mean
widening the Sprint 10 Protocol, and the queue is bounded by the one-database-
one-queue constraint. It is recorded as future scalability work.

Every route is ``async def`` and calls the synchronous repository inline. See
``review_api.dependencies`` for why that is load-bearing and temporary.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from human_review.models import ReviewStatus
from review_api.dependencies import get_repository, get_service
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
from review_application import ReviewCaseRepository, ReviewQueueService

router = APIRouter(prefix="/api/v1/review-cases", tags=["review-cases"])

# Typed against the Protocol, never the SQLite class: these routes must work
# against any implementation of the contract, and a concrete type here would let
# a storage-only method be called without anything noticing.
RepositoryDep = Annotated[ReviewCaseRepository, Depends(get_repository)]

# The resolution endpoint depends on the service and never on the repository.
# That is the whole separation: the repository can store a decision, and the
# service is the only thing that may decide one has been made.
ServiceDep = Annotated[ReviewQueueService, Depends(get_service)]

ReviewCaseIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_REVIEW_CASE_ID_LENGTH,
        description="Stable Sprint 08 review case identifier.",
    ),
]


@router.get("", summary="List review cases")
async def list_review_cases(
    repository: RepositoryDep,
    status: Annotated[ReviewStatus | None, Query(description="Exact review status.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewCaseListResponse:
    """One page of the queue, optionally filtered by status.

    ``status`` is the ``ReviewStatus`` enum, so FastAPI matches it exactly:
    ``PENDING`` is accepted and ``pending`` is a 422. Coercing case would mean
    this layer deciding what a status token means, and there is already exactly
    one answer to that in ``human_review.models``.

    Filtering is the repository's job and slicing is this route's. ``total``
    counts the filtered set before the slice, so an offset past the end returns
    an empty page that still says how many cases matched.
    """
    matching = repository.list_cases(status=status)
    page = matching[offset : offset + limit]
    items = [to_case_summary(persisted) for persisted in page]
    return ReviewCaseListResponse(
        items=items,
        count=len(items),
        total=len(matching),
        limit=limit,
        offset=offset,
    )


@router.get("/{review_case_id}", summary="Get one review case")
async def get_review_case(
    review_case_id: ReviewCaseIdPath,
    repository: RepositoryDep,
) -> ReviewCaseDetail:
    """Full detail for one case, including the version a resolution must echo.

    ``get_case`` raises ``ReviewCaseNotFoundError`` for an unknown id, which the
    registered handler turns into a 404 with a static message.
    """
    return to_case_detail(repository.get_case(review_case_id))


@router.get("/{review_case_id}/events", summary="List a case's history")
async def list_case_events(
    review_case_id: ReviewCaseIdPath,
    repository: RepositoryDep,
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
    repository: RepositoryDep,
) -> list[SemanticSuggestionRead]:
    """Stored Sprint 09 observations for one case. Reading only; never generating.

    No provider is contacted and no suggestion is created. Sprint 11 does not
    expose live semantic generation over HTTP at all: it is budget-controlled,
    disabled by default, and would put a network call and a spend decision in an
    unauthenticated request path.

    Observation order is preserved and means nothing beyond order. None of these
    is operative -- ``ReviewCase.status`` remains the only statement about what
    was decided.
    """
    repository.get_case(review_case_id)
    return [
        to_semantic_suggestion(suggestion)
        for suggestion in repository.list_semantic_suggestions(review_case_id)
    ]


@router.post("/{review_case_id}/resolve", status_code=200, summary="Record a human decision")
async def resolve_review_case(
    review_case_id: ReviewCaseIdPath,
    body: ResolveReviewCaseRequest,
    service: ServiceDep,
) -> ResolveReviewCaseResponse:
    """Apply one human decision, through the Sprint 08 authority and nothing else.

    This route decides nothing. It validates three transport fields, hands them
    to ``ReviewQueueService.resolve_case``, and projects what came back. Every
    input the authority actually reads -- the AUTO_MATCH graph, the entity
    records, the entity-resolution config and its thresholds, the queue's
    current state -- is loaded from storage by the service. None of it can be
    supplied, influenced, or narrowed from here, and the request model has no
    field through which a client could try.

    That matters because Sprint 08 authorization is not a property of the
    reviewed pair. It projects a connected component across every AUTO_MATCH
    edge and every recorded human decision, so a MATCH that is safe in isolation
    can be unsafe in context. A route that fetched one case and reasoned about
    it would silently weaken the check; this one cannot, because it never sees
    the material.

    No write happens before the domain approves. The service calls
    ``ReviewWorkflow.resolve_case`` first and opens the storage transaction only
    with what the workflow returned, so a refusal leaves no version bump, no
    timestamp change and no history row. The refusals arrive here as Sprint 08
    and Sprint 10 exceptions, each mapped to its own status and static message
    in ``review_api.errors``.

    ``200``, not ``201``: the case already existed and was transitioned. Nothing
    was created at a new location.

    ``expected_version`` is mandatory and is never defaulted to the stored
    value. Optimistic concurrency is the contract -- a reviewer decides against
    the version they were shown, and one who lost the race is told so rather
    than having their decision applied to a queue state they never saw.
    """
    result = service.resolve_case(
        review_case_id,
        decision=body.decision,
        reviewer_id=body.reviewer_id,
        expected_version=body.expected_version,
    )
    return to_resolution_response(result)
