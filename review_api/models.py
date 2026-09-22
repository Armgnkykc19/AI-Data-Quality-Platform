"""The published HTTP contract, and the projections that produce it.

Every response this API returns is an explicit model declared here. No domain
dataclass, no ``to_dict()`` output, no persistence row, and no bare
``dict[str, Any]`` is ever the contract. That indirection is the point: the
internal models carry more than a reviewer needs, and a contract that tracks
them automatically publishes each new field the moment someone adds one.

So the API surface is **deliberately smaller than the models behind it**. Three
kinds of field are dropped on the way out.

Duplicates. ``ReviewCase`` carries both ``pair`` and ``record_ids``, which say
the same thing, and ``ReviewResolution`` re-states the case's own
``machine_decision`` and ``machine_reason`` verbatim -- ``ReviewWorkflow`` copies
them straight off the case when it builds the resolution. Publishing a value
twice invites two readings of one fact.

Persistence and provenance metadata. Schema versions, stored audit payloads,
config paths, cost and token telemetry: operator material, not reviewer
material, and every one of them describes the deployment to whoever can reach
the port.

Customer-derived data that is already published in a better form. See
``BlockingReasonRead``.

The mapping functions at the bottom are pure: they read a domain object and
build a model, with no I/O, no ordering changes, and no derived state. A route
must never compute anything a reviewer will act on.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from entity_resolution.models import MatchDecisionType
from human_review.models import (
    HumanReviewDecision,
    ReviewBlockingReason,
    ReviewCase,
    ReviewConflictEvidence,
    ReviewEvidence,
    ReviewResolution,
    ReviewStatus,
)
from review_application import (
    PersistedCase,
    ReviewEvent,
    ReviewEventType,
    ReviewResolutionResult,
)
from semantic_review.models import (
    SemanticFailureCode,
    SemanticSuggestion,
    SemanticSuggestionType,
)

# Bounds for the list endpoint. A default small enough to be a sane page and a
# ceiling low enough that no single request can pull a whole queue in one go.
# Authentication narrows who may ask; it does not make an unbounded page a good
# idea, because the caller is now an authorized member whose one request would
# otherwise materialize every case their tenant holds.
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

# A review case id is a Sprint 08 stable id (``RC-`` plus a digest, from
# ``human_review.ids.stable_review_case_id``). The HTTP layer deliberately does
# not restate that format: a second copy of the rule would be a second source of
# truth, and the two would drift. Bounds only -- existence is the repository's
# answer to give.
MAX_REVIEW_CASE_ID_LENGTH = 128

# The two tenant path segments. Bounds only, matching MAX_OPAQUE_ID_LENGTH in
# ``identity.ids`` -- the transport layer does not restate the ``ORG-``/``RQ-``
# prefix rule, because a second copy of an identifier format is a second thing
# to keep in step.
#
# There is no MAX_REVIEWER_ID_LENGTH any more, and no bound to put on one: a
# tenant-scoped resolution has no client-supplied reviewer field. The server
# derives reviewer identity from the authenticated principal, so the only bound
# that matters is the one ``identity`` already enforces on a user_id.
MAX_TENANT_ID_LENGTH = 128


class ApiResponseModel(BaseModel):
    """Base for every payload this API returns."""

    model_config = ConfigDict(extra="forbid")


# ``extra="forbid"`` on a request model is a security control, not tidiness.
#
# The resolution endpoint drives the Sprint 08 authority, and every input that
# authority reads -- the AUTO_MATCH graph, the entity records, the
# entity-resolution config and its thresholds -- is loaded from storage by the
# service. A client has no field through which to supply any of it, and the
# reason it has none is this line: an unrecognised key is rejected outright
# rather than ignored. Silently dropping unknown fields would make a request
# carrying ``records_by_id`` or ``auto_match_threshold`` look accepted.
class ApiRequestModel(BaseModel):
    """Base for every payload this API accepts."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiResponseModel):
    """The complete health payload. One field, and it never grows.

    ``status`` is typed as a literal rather than a free string because the only
    body this endpoint may return is ``{"status": "ok"}``. An unhealthy process
    does not answer here at all -- it fails to start. See ``routes.health``.
    """

    status: Literal["ok"]


# --------------------------------------------------------------------------
# Review cases
# --------------------------------------------------------------------------


class ReviewCaseSummary(ApiResponseModel):
    """One row of the reviewer's queue.

    Enough to identify the pair, read the machine's verdict and confidence, see
    where the case stands, and carry the version a later resolution will have to
    echo back. Evidence is deliberately absent: it belongs to the decision
    screen, and shipping it for every row would put the whole queue's
    customer-derived detail into one response.
    """

    review_case_id: str
    record_a_id: str
    record_b_id: str
    status: ReviewStatus
    machine_decision: MatchDecisionType
    machine_score: float
    version: int
    created_at_utc: str
    updated_at_utc: str


# The prose form of this reason is dropped, and that is the minimization.
# Production blocking builds it as "Records share blocking key '<key>' via
# <TYPE>." (``entity_resolution.blocking``) -- the same sensitive value a second
# time, wrapped in prose and fully reconstructible from the two structured
# fields kept here. Publishing it would double the exposure and add nothing a
# reviewer could act on. ``ReviewCaseDetail`` closes a third channel for the
# same reason, so the key reaches a client exactly once per reason.
class BlockingReasonRead(ApiResponseModel):
    """Why these two records were ever compared.

    ``blocking_key`` is customer-derived: for the email and phone strategies it
    is a normalized email address or a phone fragment. It is published because a
    reviewer judging whether two records are the same person needs to know what
    put them in the same bucket, and withholding it would leave the decision
    unsupported. Treat it as personal data.
    """

    reason_type: str
    blocking_key: str


class SupportingEvidenceRead(ApiResponseModel):
    """One reason the records look like the same entity.

    ``description`` is kept here, unlike on a blocking reason, because it holds
    no customer data: Sprint 08 builds it from the field *name* and, for fuzzy
    comparisons, the similarity score -- which is the one number not otherwise
    present, since ``ReviewEvidence`` keeps ``contribution`` but not ``value``.
    """

    evidence_type: str
    field_name: str
    strength: str
    contribution: float
    description: str


class ConflictingEvidenceRead(ApiResponseModel):
    """One reason the records may not be the same entity.

    Conflict descriptions are fixed sentences ("Both records have different
    non-empty email addresses."), so they name the disagreement without
    reproducing either value.
    """

    conflict_type: str
    field_name: str
    severity: str
    penalty: float
    description: str


class ReviewResolutionRead(ApiResponseModel):
    """The human decision already recorded against a case.

    ``review_case_id`` is dropped because the enclosing response already
    identifies the case, and ``machine_decision`` / ``machine_reason`` because
    ``ReviewWorkflow`` copies them off the case when it builds the resolution --
    they are already published one level up, and a second copy could only ever
    agree or reveal a bug.
    """

    human_decision: HumanReviewDecision
    reviewer_id: str | None
    resolution_sequence: int
    downstream_action: str


# Rationale in a comment for the same reason as the models below: a docstring is
# published as the OpenAPI description, and naming an omitted field there would
# advertise it.
#
# The duplicate pair of record ids is dropped; the two id fields say it once. No
# persistence metadata, workflow context, entity record, AUTO_MATCH snapshot, or
# authorization material appears at any depth.
#
# Sprint 08's flattened machine-readable reason tokens are dropped for two
# reasons at once. Each token restates something already published here in a
# structured form -- the machine decision, the machine reason, the evidence
# types, whether the score met the review threshold -- and one of them repeats
# the customer-derived blocking key, which would make a third copy after the
# structured field and the prose description. Nothing is lost to a reviewer
# except the extra copy of the key.
#
# ``human_summary`` and ``missing_evidence_notes`` are kept: Sprint 08 builds
# both from field *names*, evidence type tokens and scores, never from values.
class ReviewCaseDetail(ApiResponseModel):
    """Everything a reviewer needs to decide one case, and nothing else.

    The thresholds are published because they are what make ``machine_score``
    legible: a score of 0.81 means something only once a reader knows it fell
    between the review and AUTO_MATCH thresholds, which is the entire reason the
    case exists.
    """

    review_case_id: str
    record_a_id: str
    record_b_id: str
    status: ReviewStatus
    machine_decision: MatchDecisionType
    machine_score: float
    auto_match_threshold: float
    review_threshold: float
    machine_reason: str
    human_summary: str
    missing_evidence_notes: list[str]
    blocking_reasons: list[BlockingReasonRead]
    supporting_evidence: list[SupportingEvidenceRead]
    conflicting_evidence: list[ConflictingEvidenceRead]
    resolution: ReviewResolutionRead | None
    version: int
    created_at_utc: str
    updated_at_utc: str


class ReviewCaseListResponse(ApiResponseModel):
    """A page of the queue.

    ``total`` counts every case matching the filter, before slicing, so a client
    can tell "this page is empty" from "nothing matches".
    """

    items: list[ReviewCaseSummary]
    count: int
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


# A model's docstring is published verbatim as its OpenAPI description, so the
# rationale for what is *left out* lives here in a comment rather than there.
# Naming an omitted field in the schema would advertise it to every client and
# code generator reading the document, which is the opposite of omitting it.
#
# The stored audit payload is not published: it is the persistence-owned
# projection of the domain audit entry, and publishing it would restate the
# resolution in a second shape this API does not own. The stored database
# schema version is operator metadata. The review case id is already in the path.
#
# ``is_resolution`` is carried explicitly rather than left for a client to infer
# from ``event_type``, because an advisory suggestion event and a human decision
# both appear in this list and only one of them decided anything.
class ReviewEventRead(ApiResponseModel):
    """One entry of a case's append-only history, oldest first."""

    event_id: int | None
    event_type: ReviewEventType
    occurred_at_utc: str
    resolution_sequence: int | None
    reviewer_id: str | None
    suggestion_id: str | None
    is_resolution: bool


# --------------------------------------------------------------------------
# Semantic suggestions
# --------------------------------------------------------------------------


# Rationale in a comment, not the docstring: the docstring is published as this
# model's OpenAPI description, and naming an omitted field there would advertise
# it to every client and code generator reading the schema.
#
# The model's free-form rationale text is the field this model most pointedly
# does not carry. Sprint 09 excludes it from its own durable representation
# because model prose can repeat values out of untrusted customer records, so
# nothing is stored and nothing can be rebuilt. A reloaded suggestion carries an
# empty string, and publishing that -- or null, or a "not persisted" note --
# would imply an original text the database never held. The field is absent from
# this model and therefore from the OpenAPI schema; ``test_openapi.py`` asserts
# the word appears nowhere in the published document.
#
# Provider telemetry is absent for a different reason: token counts, latency,
# cost, cost mode, pricing version, request fingerprints, prompt hashes, schema
# versions, attempt count, returned model, model snapshot and provider request
# id say nothing about whether two records are the same person, and several of
# them describe the deployment's spending.
class SemanticSuggestionRead(ApiResponseModel):
    """A stored Sprint 09 advisory observation. It decided nothing.

    ``advisory`` is a constant this API adds. It cannot be false, and it exists
    so no client reads a suggestion as an outcome: a case's ``status`` remains
    the only statement about what was decided.
    """

    suggestion_id: str
    suggestion: SemanticSuggestionType
    reason_codes: list[str]
    provider: str
    requested_model: str
    failure_code: SemanticFailureCode | None
    live: bool
    created_at_utc: str
    advisory: Literal[True] = True


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


# Three fields, and the smallness is the security property. Everything the
# Sprint 08 authority reads comes from server-side storage; this model is the
# complete list of what a reviewer may contribute.
#
# ``decision`` is the ``HumanReviewDecision`` vocabulary -- MATCH, NO_MATCH,
# DEFER -- which is *not* the ``ReviewStatus`` vocabulary the response reports.
# A DEFER decision produces a DEFERRED status. The request accepts only the
# decision spelling; "DEFERRED" is rejected rather than quietly translated,
# because translating it would mean this layer deciding what a reviewer meant.
#
# Both scalars are strict. Pydantic would otherwise coerce the JSON string "3"
# into version 3 and the number 123 into reviewer id "123". A concurrency token
# that accepts a string is a token whose type the client and server disagree
# about, and an audit identity that accepts a number records something the
# reviewer did not send.
# Two fields, and no third.
#
# Sprint 11 accepted a client-supplied reviewer label here, which was honest
# while the API had no accounts and is unacceptable now that it has. Who
# recorded a decision is not something a client gets to assert: the server takes
# it from the authenticated principal and hands that to the domain, so the
# durable audit row names the session that was actually used.
#
# Dropping the field is not enough on its own -- an ignored field looks
# accepted. ``extra="forbid"`` on ``ApiRequestModel`` is what turns a body
# carrying a forged reviewer identity into a 422 that never reaches the
# authority, so an attempt to sign a decision as another person fails loudly
# instead of silently becoming an anonymous one.
#
# The rationale lives in a comment rather than the docstring because Pydantic
# publishes a model's docstring as the schema ``description``, and a published
# description naming a field is a field a code generator's reader will look
# for.
class ResolveReviewCaseRequest(ApiRequestModel):
    """One human decision, as a reviewer submits it."""

    decision: HumanReviewDecision
    expected_version: int = Field(
        strict=True,
        ge=1,
        description="The case version the reviewer had in front of them.",
    )


class ResolveReviewCaseResponse(ApiResponseModel):
    """What the resolution produced, at the two levels a client needs.

    ``case`` is the updated case: its new status, its new version, and the
    resolution the domain recorded. ``event`` is the history entry that was
    appended alongside it, so a client can extend its timeline without
    re-fetching.
    """

    case: ReviewCaseDetail
    event: ReviewEventRead


# --------------------------------------------------------------------------
# Projections -- pure, ordering-preserving, no derived state
# --------------------------------------------------------------------------


def to_blocking_reason(reason: ReviewBlockingReason) -> BlockingReasonRead:
    return BlockingReasonRead(reason_type=reason.reason_type, blocking_key=reason.blocking_key)


def to_supporting_evidence(evidence: ReviewEvidence) -> SupportingEvidenceRead:
    return SupportingEvidenceRead(
        evidence_type=evidence.evidence_type,
        field_name=evidence.field_name,
        strength=evidence.strength,
        contribution=evidence.contribution,
        description=evidence.description,
    )


def to_conflicting_evidence(conflict: ReviewConflictEvidence) -> ConflictingEvidenceRead:
    return ConflictingEvidenceRead(
        conflict_type=conflict.conflict_type,
        field_name=conflict.field_name,
        severity=conflict.severity,
        penalty=conflict.penalty,
        description=conflict.description,
    )


def to_resolution(resolution: ReviewResolution | None) -> ReviewResolutionRead | None:
    """Project the resolution the domain already recorded on the case.

    Read straight off ``ReviewCase.resolution``; never replayed from event
    history. The case is the domain's own statement of what was decided, and
    reconstructing it from events would create a second answer that could
    disagree with the first.
    """
    if resolution is None:
        return None
    return ReviewResolutionRead(
        human_decision=resolution.human_decision,
        reviewer_id=resolution.reviewer_id,
        resolution_sequence=resolution.resolution_sequence,
        downstream_action=resolution.downstream_action,
    )


def to_case_summary(persisted: PersistedCase) -> ReviewCaseSummary:
    case: ReviewCase = persisted.case
    return ReviewCaseSummary(
        review_case_id=case.review_case_id,
        record_a_id=case.pair.record_a_id,
        record_b_id=case.pair.record_b_id,
        status=case.status,
        machine_decision=case.machine_decision,
        machine_score=case.machine_score,
        version=persisted.version,
        created_at_utc=persisted.created_at_utc,
        updated_at_utc=persisted.updated_at_utc,
    )


def to_case_detail(persisted: PersistedCase) -> ReviewCaseDetail:
    case: ReviewCase = persisted.case
    return ReviewCaseDetail(
        review_case_id=case.review_case_id,
        record_a_id=case.pair.record_a_id,
        record_b_id=case.pair.record_b_id,
        status=case.status,
        machine_decision=case.machine_decision,
        machine_score=case.machine_score,
        auto_match_threshold=case.auto_match_threshold,
        review_threshold=case.review_threshold,
        machine_reason=case.machine_reason,
        human_summary=case.human_summary,
        missing_evidence_notes=list(case.missing_evidence_notes),
        blocking_reasons=[to_blocking_reason(item) for item in case.blocking_reasons],
        supporting_evidence=[to_supporting_evidence(item) for item in case.supporting_evidence],
        conflicting_evidence=[to_conflicting_evidence(item) for item in case.conflicting_evidence],
        resolution=to_resolution(case.resolution),
        version=persisted.version,
        created_at_utc=persisted.created_at_utc,
        updated_at_utc=persisted.updated_at_utc,
    )


def to_event(event: ReviewEvent) -> ReviewEventRead:
    return ReviewEventRead(
        event_id=event.event_id,
        event_type=event.event_type,
        occurred_at_utc=event.occurred_at_utc,
        resolution_sequence=event.resolution_sequence,
        reviewer_id=event.reviewer_id,
        suggestion_id=event.suggestion_id,
        is_resolution=event.is_resolution,
    )


def to_semantic_suggestion(suggestion: SemanticSuggestion) -> SemanticSuggestionRead:
    """Project the durable advisory fields. ``explanation`` is never read."""
    return SemanticSuggestionRead(
        suggestion_id=suggestion.suggestion_id,
        suggestion=suggestion.suggestion,
        reason_codes=list(suggestion.reason_codes),
        provider=suggestion.provider,
        requested_model=suggestion.requested_model,
        failure_code=suggestion.failure_code,
        live=suggestion.live,
        created_at_utc=suggestion.created_at_utc,
    )


def to_resolution_response(result: ReviewResolutionResult) -> ResolveReviewCaseResponse:
    """Project what the service returned, and only that.

    ``ReviewResolutionResult`` also carries ``workflow_state`` -- the entire
    queue plus its audit trail -- and the raw ``audit_entry``. Neither is
    published: the workflow state is authorization material, and the audit entry
    is already projected, field by field, through the event below.

    ``result.event`` is the history row storage just wrote, so ``event_id`` is
    the durable identifier of that row. ``ReviewEventRead.event_id`` remains
    optional for events that have not been stored, which keeps older clients
    compatible.
    """
    return ResolveReviewCaseResponse(
        case=to_case_detail(result.persisted_case),
        event=to_event(result.event),
    )
