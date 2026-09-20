"""Registering a generated workflow into one durable review queue.

This is the operator-side counterpart to ``ReviewQueueService``: that one applies
a decision to a queue, this one fills a queue with the cases to decide. Both are
thin, and for the same reason -- every rule about what a review case is, and
which AUTO_MATCH edges authorize a merge, already lives in Sprint 08.

So nothing here builds a ``ReviewCase``, a context payload, or an AUTO_MATCH
pair. The caller hands over what the deterministic pipeline already produced,
this function asks ``human_review.reporting.resolution_snapshot`` for the same
reduction Sprint 08 writes into its own report, and passes both to
``register_workflow``. One snapshot implementation, one registration primitive.

**Which queue is not a parameter here.** The repository handed in is already
bound to exactly one, and everything this function relies on is therefore
queue-local: the context fingerprint it compares against, the idempotence of
re-registering a case, and the conflict raised when a stored context disagrees.
Registering an identical workflow into a second queue is a first registration
there, not a replay -- two organizations that happen to generate the same
workflow are independent and cannot observe one another.

The function is named for what it does: it registers a *workflow* into a queue
that already exists. It does not create the queue, and it must not -- a review
queue is owned by an organization, and manufacturing one here would mean a
misspelled slug quietly producing review data owned by nobody. Creating the
queue is an explicit operator step.

The safety properties are Sprint 10's and are consumed rather than restated
here: context and cases commit together, re-registering an identical workflow
rewrites nothing, a resolved case is never returned to PENDING, and a changed
record set, changed snapshot, or different entity-resolution config path is
refused rather than merged. Deterministic generation always emits the PENDING
form of every case, so a re-run after review would silently erase human
decisions if any of that were weaker.

It lives in ``review_application`` rather than in the CLI so that the command
never needs to know how a workflow is serialized, and so the orchestration can
be tested against the repository Protocol without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from entity_resolution.models import EntityRecord, ResolutionResult
from human_review.models import ReviewStatus, ReviewWorkflowState

# The public Sprint 08 reduction, and the same one ``write_review_reports``
# uses. A second implementation could differ by one AUTO_MATCH edge and change
# which merges are authorized, with nothing to catch it.
from human_review.reporting import resolution_snapshot
from review_application.models import PersistedCase
from review_application.repository import ReviewCaseRepository


@dataclass(frozen=True)
class ReviewWorkflowRegistration:
    """What one registration found and what it changed, in one queue.

    Deliberately counts only. An operator needs to know whether the command
    filled a queue or confirmed an existing one, and how much of it is still
    waiting for a reviewer -- not what is in any particular case.
    """

    total_cases: int
    newly_registered: int
    already_present: int
    pending_cases: int
    resolved_cases: int

    @property
    def created_anything(self) -> bool:
        return self.newly_registered > 0

    @property
    def was_idempotent(self) -> bool:
        """True when every case offered was already stored."""
        return self.total_cases > 0 and self.newly_registered == 0


def register_review_workflow(
    repository: ReviewCaseRepository,
    *,
    state: ReviewWorkflowState,
    entity_records: Sequence[EntityRecord],
    resolution: ResolutionResult,
    entity_resolution_config_path: str | None = None,
    now_utc: str | None = None,
) -> ReviewWorkflowRegistration:
    """Register a generated workflow and report what the queue now holds.

    ``resolution`` is reduced to the Sprint 08 AUTO_MATCH snapshot here rather
    than by the caller, so a command cannot accidentally register a queue whose
    authorization graph disagrees with the report written beside it.

    The count of pre-existing cases is read before registering. That read is not
    part of the safety story -- ``register_workflow`` is atomic and idempotent on
    its own -- it exists only so the operator can be told whether this run
    filled the queue or confirmed one that was already there. It counts only
    the bound queue's cases, so a busy installation does not make a fresh
    queue look pre-populated.

    A workflow with no review cases is registered as a valid empty queue: the
    entity records and the AUTO_MATCH snapshot are still the authorization
    context, and storing them is what makes a later re-run idempotent instead of
    a first registration. Registering without any entity records is refused by
    the repository, because MATCH authorization would fail closed forever after.

    Propagates ``ReviewQueueNotFoundError``,
    ``ReviewWorkflowContextConflictError``, ``DuplicateCaseRegistrationError``
    and ``ReviewPersistenceError`` unchanged. Every one of them means the
    stored queue was left exactly as it was.
    """
    already_present = len(repository.list_cases())

    stored = repository.register_workflow(
        state,
        entity_records=entity_records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=entity_resolution_config_path,
        now_utc=now_utc,
    )
    return _summarize(stored, already_present=already_present)


def _summarize(
    stored: Sequence[PersistedCase],
    *,
    already_present: int,
) -> ReviewWorkflowRegistration:
    pending = sum(1 for case in stored if case.status is ReviewStatus.PENDING)
    return ReviewWorkflowRegistration(
        total_cases=len(stored),
        # Clamped rather than subtracted blindly: registration never deletes a
        # case, so a negative difference would mean the count is measuring
        # something other than what it claims.
        newly_registered=max(0, len(stored) - already_present),
        already_present=min(already_present, len(stored)),
        pending_cases=pending,
        resolved_cases=len(stored) - pending,
    )
