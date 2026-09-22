"""The one supported way to resolve a persisted review case.

The service is an orchestrator, not a decision maker. It loads the complete
authorization material, hands it to the unchanged Sprint 08 ``ReviewWorkflow``,
and persists only what the workflow returned. Every rule about whether a MATCH
is allowed -- prior NO_MATCH constraints, transitive component membership,
severe identity conflicts -- stays in ``human_review``; none of it is repeated,
re-implemented, or approximated here.

Three properties are worth stating plainly, because the whole design follows
from them.

First, no durable write happens before ``ReviewWorkflow.resolve_case`` returns.
A refusal is raised out of the domain, which abandons the unit of work before
``apply_resolution`` is ever called. There is no path where a rejected decision
leaves a version bump, a timestamp change, or a history row behind.

The service also holds no persistence knowledge: it imports nothing from
``review_persistence``, names no table, and does not know the database schema
version. It speaks only domain objects and the repository Protocol, so the same
service would drive a different storage backend unchanged.

Second, authorization is evaluated against the whole queue, never one row. The
Sprint 08 boundary check projects a connected component across every AUTO_MATCH
edge and every human decision, so a MATCH that is safe in isolation can be
unsafe in context. Loading a single case would silently weaken it, which is why
``load_workflow_bundle`` is the only entry point used here.

Third -- and this is what Sprint 14 Phase A added -- that authorization is
performed and acted upon inside **one serialized write scope**. The reason is
the second property: if authorization is a statement about the whole queue,
then proving the one target row has not moved proves almost nothing about it.

Consider two reviewers holding the same bundle, resolving two different PENDING
cases that sit in one identity component. One records MATCH, the other
NO_MATCH. Each target row is at the version its reviewer read, so each
conditional update succeeds -- and the queue is left in a state the domain
would have refused had it been asked once, in order, because the MATCH now
transitively violates the NO_MATCH. Nothing was overwritten and no version was
lost; the two decisions were simply never evaluated against each other.

``expected_version`` cannot close that, and neither can a stricter version of
it: the gap is not "did this row change" but "was the graph still this graph".
``unit_of_work`` closes it by construction -- the bundle is read under the write
lock and the resolution commits before that lock is released, so there is no
interval in which a second writer could authorize against a state this one is
about to invalidate. The second writer does not race and lose; it waits, reads
the first decision, and is refused by the domain on the merits.

This is also why the queue-global ``resolution_sequence`` uniqueness in
``review_persistence.schema`` is a backstop rather than the control. The
sequence is taken from the bundle, so inside one serialized scope two writers
cannot arrive at the same ordinal. The index is what makes that true of the
*database* rather than only of this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from entity_resolution.config import (
    PROJECT_ROOT,
    EntityResolutionConfig,
    load_entity_resolution_config,
)
from entity_resolution.models import ResolutionResult
from human_review.errors import ReviewCaseNotFoundError
from human_review.models import (
    HumanReviewDecision,
    ReviewAuditEntry,
    ReviewWorkflowState,
)

# The public Sprint 08 reconstruction seam, and the same one
# ``load_human_review_report`` uses. The AUTO_MATCH edges it produces are an
# authorization input, so a second implementation that drifted by one edge
# would change which merges are allowed without any test noticing. Calling the
# Sprint 08 function makes that divergence impossible.
from human_review.reporting import rebuild_resolution_from_snapshot
from human_review.workflow import ReviewWorkflow
from review_application.clock import Clock, system_utc_now, utc_timestamp
from review_application.errors import (
    ReviewAuthorizationConfigError,
    ReviewConflictError,
    ReviewEventIntegrityError,
)
from review_application.models import PersistedCase, ReviewEvent, WorkflowBundle
from review_application.repository import ReviewCaseRepository


@dataclass(frozen=True)
class ReviewResolutionResult:
    """What one successful resolution produced, at every layer.

    ``workflow_state`` is the Sprint 08 state the domain returned, so a caller
    can write a ``human_review_report.json`` from it without reloading.
    """

    persisted_case: PersistedCase
    audit_entry: ReviewAuditEntry
    event: ReviewEvent
    workflow_state: ReviewWorkflowState

    @property
    def review_case_id(self) -> str:
        return self.persisted_case.review_case_id

    @property
    def version(self) -> int:
        return self.persisted_case.version


class ReviewQueueService:
    """Resolves persisted review cases through the unchanged Sprint 08 domain."""

    def __init__(self, repository: ReviewCaseRepository, *, clock: Clock = system_utc_now) -> None:
        self._repository = repository
        self._clock = clock
        # Loaded lazily, then held for the life of the service. The entity
        # resolution config decides what counts as a severe identity conflict,
        # so re-reading the file between two decisions in one session could
        # authorize the second against thresholds the first never saw.
        self._configs_by_path: dict[str, EntityResolutionConfig] = {}

    def resolve_case(
        self,
        review_case_id: str,
        *,
        decision: HumanReviewDecision,
        reviewer_id: str | None = None,
        expected_version: int,
    ) -> ReviewResolutionResult:
        """Apply one human decision to a persisted case.

        ``expected_version`` is the version the reviewer had in front of them.
        It is checked twice: here, before the domain is asked anything, and
        again inside the storage write as a conditional update. Both checks
        concern the *one* case being written -- they are lost-update protection,
        and neither says anything about the rest of the queue.

        What covers the rest of the queue is the unit of work. Everything from
        loading the bundle to committing the resolution happens inside one
        serialized write scope, so the queue state Sprint 08 authorized against
        is the state the write lands on. See the module docstring for why a
        per-case version cannot substitute for that.

        This scope never calls a semantic provider, LLM, or other network
        client. Authorization is the in-process Sprint 08 domain against the
        loaded bundle. A live suggestion path is a different command and is
        not taken here.

        Raises ``ReviewConflictError`` if the case moved, and propagates the
        Sprint 08 errors -- ``HumanReviewContradictionError``,
        ``HumanReviewAuthorizationError``,
        ``HumanReviewAuthorizationContextError``,
        ``InvalidReviewTransitionError`` -- unchanged and unwrapped, so a caller
        can still tell a refused decision from a storage failure. Every one of
        them leaves the scope by raising, which abandons it: a refusal writes
        nothing, exactly as before.
        """
        with self._repository.unit_of_work():
            bundle = self._repository.load_workflow_bundle()
            persisted = self._locate(bundle, review_case_id)
            self._assert_not_stale(persisted, expected_version)

            updated_state = self._resolve_through_domain(
                bundle,
                review_case_id,
                decision=decision,
                reviewer_id=reviewer_id,
            )

            audit_entry = self._appended_audit_entry(updated_state, review_case_id)
            updated_case = updated_state.case_by_id(review_case_id)
            if updated_case is None:  # pragma: no cover - the workflow just produced it
                raise ReviewEventIntegrityError(
                    f"Workflow returned no case for {review_case_id} after resolving it."
                )

            now = utc_timestamp(self._clock)
            # No schema version: the storage layer stamps the one it is writing.
            event = ReviewEvent.from_audit_entry(audit_entry, occurred_at_utc=now)
            stored = self._repository.apply_resolution(
                updated_case,
                expected_version=expected_version,
                event=event,
                now_utc=now,
            )

        return ReviewResolutionResult(
            persisted_case=stored,
            audit_entry=audit_entry,
            event=event,
            workflow_state=updated_state,
        )

    # -- domain orchestration ----------------------------------------------

    def _resolve_through_domain(
        self,
        bundle: WorkflowBundle,
        review_case_id: str,
        *,
        decision: HumanReviewDecision,
        reviewer_id: str | None,
    ) -> ReviewWorkflowState:
        """Rebuild Sprint 08's world from storage and let it decide.

        A fresh ``ReviewWorkflow`` is built for every call. The workflow holds
        mutable state and advances it on each resolution; a cached instance
        would authorize the next decision against a queue that may have changed
        under it, which is the exact failure optimistic concurrency exists to
        prevent.
        """
        workflow = ReviewWorkflow(bundle.to_workflow_state())
        context = self._authorization_context(bundle, decision)
        return workflow.resolve_case(
            review_case_id,
            decision=decision,
            reviewer_id=reviewer_id,
            **context,
        )

    def _authorization_context(
        self,
        bundle: WorkflowBundle,
        decision: HumanReviewDecision,
    ) -> dict[str, object]:
        """The entity-resolution material a MATCH must be authorized against.

        Assembled only for MATCH. NO_MATCH and DEFER need no authorization
        context in Sprint 08, and demanding a loadable config for them would
        block a reviewer from recording a refusal -- the safe decision -- over a
        problem that cannot affect it.
        """
        if decision is not HumanReviewDecision.MATCH:
            return {}
        return {
            "resolution": self._rebuild_auto_match_graph(bundle),
            "records_by_id": bundle.records_by_id(),
            "entity_resolution_config": self._entity_resolution_config(bundle),
        }

    @staticmethod
    def _rebuild_auto_match_graph(bundle: WorkflowBundle) -> ResolutionResult:
        """Turn the persisted reduced snapshot back into a ResolutionResult."""
        return rebuild_resolution_from_snapshot(
            bundle.entity_records,
            dict(bundle.resolution_snapshot),
        )

    def _entity_resolution_config(self, bundle: WorkflowBundle) -> EntityResolutionConfig | None:
        """Load the exact config the queue was generated with.

        Returning ``None`` when no path was persisted is deliberate: the Sprint
        08 workflow then refuses the MATCH with
        ``HumanReviewAuthorizationContextError``. Substituting the default
        config would authorize against thresholds that may not be the ones the
        queue was built from, and hard-coding a threshold here would put a
        tuning constant in the persistence layer, where nothing tests it.
        """
        stored_path = bundle.entity_resolution_config_path
        if not stored_path:
            return None
        cached = self._configs_by_path.get(stored_path)
        if cached is not None:
            return cached

        path = Path(stored_path)
        if not path.is_absolute():
            # Anchored to the project, matching how every other loader in this
            # codebase resolves a configured relative path.
            path = PROJECT_ROOT / path
        try:
            config = load_entity_resolution_config(path)
        except Exception as exc:
            # Fail closed. A queue whose generating config cannot be read must
            # not fall back to a different one, because the fallback could
            # permit a merge the real config forbids.
            raise ReviewAuthorizationConfigError(
                f"The entity-resolution config this review queue was generated with "
                f"({stored_path}) could not be loaded: {exc}. Refusing to authorize a "
                "MATCH against a different configuration."
            ) from exc
        self._configs_by_path[stored_path] = config
        return config

    # -- checks -------------------------------------------------------------

    @staticmethod
    def _locate(bundle: WorkflowBundle, review_case_id: str) -> PersistedCase:
        for persisted in bundle.persisted_cases:
            if persisted.review_case_id == review_case_id:
                return persisted
        raise ReviewCaseNotFoundError(f"Review case not found: {review_case_id}")

    @staticmethod
    def _assert_not_stale(persisted: PersistedCase, expected_version: int) -> None:
        """Refuse a decision taken against a version that has been superseded.

        Checked before the domain runs so a reviewer who lost the race is told
        so directly, rather than having their decision authorized against a
        queue state they never saw and then rejected by the database.
        """
        if persisted.version != expected_version:
            raise ReviewConflictError(
                f"Review case {persisted.review_case_id} has advanced to version "
                f"{persisted.version}; the decision was taken against version "
                f"{expected_version}. Reload the case and decide again.",
                review_case_id=persisted.review_case_id,
                expected_version=expected_version,
            )

    @staticmethod
    def _appended_audit_entry(
        state: ReviewWorkflowState,
        review_case_id: str,
    ) -> ReviewAuditEntry:
        """The entry the workflow just appended, verified to be that entry.

        ``resolve_case`` appends exactly one entry to the end of the trail.
        Taking the last element is only safe if it really belongs to the case
        that was just resolved, so that is asserted rather than assumed.
        """
        if not state.audit_trail:
            raise ReviewEventIntegrityError(
                f"Workflow resolved {review_case_id} without appending an audit entry."
            )
        entry = state.audit_trail[-1]
        if entry.review_case_id != review_case_id:
            raise ReviewEventIntegrityError(
                f"Workflow appended an audit entry for {entry.review_case_id} while "
                f"resolving {review_case_id}."
            )
        return entry
