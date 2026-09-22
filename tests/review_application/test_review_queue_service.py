"""End-to-end proof that ReviewQueueService decides nothing itself.

Every test here goes through the real ``ReviewQueueService.resolve_case``
against a real SQLite database. Nothing constructs a resolved case by hand and
nothing calls the repository's write path directly, because the property under
test is precisely that the service cannot produce a decision Sprint 08 would
refuse.

The two refusal scenarios are the ones Phase C proved survive persistence: a
MATCH that transitively violates a prior human NO_MATCH, and a MATCH that is
unsafe only once the whole AUTO_MATCH-connected component is considered. Both
are re-run here through the service, so what is demonstrated is not that the
Sprint 08 checks still work -- Sprint 08 tests cover that -- but that the
service hands them the complete persisted graph rather than one database row.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import RecordPair, ResolutionResult
from human_review.cases import generate_review_cases
from human_review.errors import (
    HumanReviewAuthorizationContextError,
    HumanReviewAuthorizationError,
    HumanReviewContradictionError,
    InvalidReviewTransitionError,
    ReviewCaseNotFoundError,
)
from human_review.models import (
    HumanReviewDecision,
    ReviewStatus,
    ReviewWorkflowState,
)
from human_review.reporting import load_human_review_report, write_review_reports
from human_review.workflow import ReviewWorkflow
from review_application import service as service_module
from review_application.errors import ReviewAuthorizationConfigError, ReviewConflictError
from review_application.models import ReviewEventType
from review_application.service import ReviewQueueService
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.human_review.conftest import (
    make_bridge_resolution,
    make_record,
    make_triangle_review_resolution,
)
from tests.review_application.conftest import ENTITY_RESOLUTION_CONFIG_PATH, register
from tests.review_persistence.conftest import conflicting_bridge_resolution

SERVICE_SOURCE = Path("review_application/service.py")

# Names that would mean the service had reimplemented Sprint 08 authorization
# instead of calling it.
SPRINT_08_AUTHORIZATION_FUNCTIONS = frozenset(
    {
        "assert_human_match_allowed",
        "assert_human_match_authorization_boundary",
        "build_human_match_components",
        "component_has_severe_internal_conflict",
        "projected_review_component_member_ids",
        "_cluster_has_severe_internal_conflict",
    }
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def snapshot(repository: SqliteReviewCaseRepository, review_case_id: str) -> dict[str, object]:
    """Everything a refused decision must leave untouched."""
    persisted = repository.get_case(review_case_id)
    return {
        "status": persisted.status,
        "version": persisted.version,
        "created_at_utc": persisted.created_at_utc,
        "updated_at_utc": persisted.updated_at_utc,
        "resolution": persisted.case.resolution,
        "event_count": len(repository.list_events(review_case_id)),
    }


def total_events(repository: SqliteReviewCaseRepository) -> int:
    bundle = repository.load_workflow_bundle()
    return sum(len(repository.list_events(case.review_case_id)) for case in bundle.persisted_cases)


# --------------------------------------------------------------------------
# Successful decisions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_event"),
    [
        (HumanReviewDecision.MATCH, ReviewStatus.MATCH, ReviewEventType.MATCH),
        (HumanReviewDecision.NO_MATCH, ReviewStatus.NO_MATCH, ReviewEventType.NO_MATCH),
        # The one place decision and status differ by name. DEFER is the
        # reviewer's verb; DEFERRED is the state it leaves the case in.
        (HumanReviewDecision.DEFER, ReviewStatus.DEFERRED, ReviewEventType.DEFERRED),
    ],
)
def test_each_decision_is_persisted_with_one_event_and_one_version_bump(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    registered_case: str,
    decision: HumanReviewDecision,
    expected_status: ReviewStatus,
    expected_event: ReviewEventType,
) -> None:
    before = snapshot(repository, registered_case)
    assert before["status"] is ReviewStatus.PENDING
    assert before["version"] == 1

    result = service.resolve_case(
        registered_case,
        decision=decision,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    stored = repository.get_case(registered_case)
    assert stored.status is expected_status
    assert stored.version == 2
    assert result.version == 2

    events = repository.list_events(registered_case)
    assert len(events) == 1
    assert events[0].event_type is expected_event
    assert events[0].resolution_sequence == 1
    assert events[0].reviewer_id == "reviewer-1"


def test_the_persisted_resolution_is_the_one_the_domain_produced(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    registered_case: str,
) -> None:
    """The stored ReviewResolution must be the workflow's object, field for field."""
    result = service.resolve_case(
        registered_case,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    domain_case = result.workflow_state.case_by_id(registered_case)
    assert domain_case is not None
    assert repository.get_case(registered_case).case == domain_case
    assert domain_case.resolution is not None
    assert domain_case.resolution.downstream_action == (
        "eligible_for_human_confirmed_canonical_merge"
    )


def test_the_event_is_a_projection_of_the_domain_audit_entry(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    registered_case: str,
) -> None:
    # The caller asked for a decision; what was written is the workflow's audit
    # entry. These are only the same thing because the workflow approved it.
    result = service.resolve_case(
        registered_case,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    (event,) = repository.list_events(registered_case)
    assert event.audit_entry_payload == result.audit_entry.to_dict()
    assert event.suggestion_id is None


def test_a_resolution_event_never_claims_a_semantic_suggestion(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    registered_case: str,
) -> None:
    service.resolve_case(
        registered_case,
        decision=HumanReviewDecision.DEFER,
        reviewer_id=None,
        expected_version=1,
    )

    (event,) = repository.list_events(registered_case)
    assert event.suggestion_id is None
    assert event.reviewer_id is None


# --------------------------------------------------------------------------
# Optimistic concurrency
# --------------------------------------------------------------------------


def test_two_reviewers_racing_on_one_case_produce_exactly_one_decision(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    registered_case: str,
) -> None:
    """The mandatory two-reviewer scenario.

    Both reviewers loaded the case at version 1. Whoever writes second is
    deciding against a queue state that no longer exists -- and in the general
    case their decision might not even be authorized any more -- so it is
    refused rather than applied on top.
    """
    reviewer_a_version = repository.get_case(registered_case).version
    reviewer_b_version = repository.get_case(registered_case).version
    assert reviewer_a_version == reviewer_b_version == 1

    service.resolve_case(
        registered_case,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-a",
        expected_version=reviewer_a_version,
    )

    with pytest.raises(ReviewConflictError) as raised:
        service.resolve_case(
            registered_case,
            decision=HumanReviewDecision.NO_MATCH,
            reviewer_id="reviewer-b",
            expected_version=reviewer_b_version,
        )
    assert raised.value.review_case_id == registered_case
    assert raised.value.expected_version == 1

    final = repository.get_case(registered_case)
    assert final.status is ReviewStatus.MATCH
    assert final.version == 2
    assert final.case.resolution is not None
    assert final.case.resolution.reviewer_id == "reviewer-a"

    events = repository.list_events(registered_case)
    assert len(events) == 1
    assert events[0].event_type is ReviewEventType.MATCH


def test_a_conflict_is_not_reported_as_a_storage_failure(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    registered_case: str,
) -> None:
    # A caller must be able to retry a conflict and must not retry a storage
    # failure, so the two cannot share a base class.
    from review_application.errors import ReviewPersistenceError

    service.resolve_case(
        registered_case,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-a",
        expected_version=1,
    )
    with pytest.raises(ReviewConflictError) as raised:
        service.resolve_case(
            registered_case,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-b",
            expected_version=1,
        )
    assert not isinstance(raised.value, ReviewPersistenceError)


def test_a_stale_decision_is_refused_before_the_domain_is_consulted(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    registered_case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stale check runs first, so no authorization work happens on a lost race."""
    calls: list[str] = []
    original = ReviewWorkflow.resolve_case

    def spy(self: ReviewWorkflow, review_case_id: str, **kwargs: object) -> ReviewWorkflowState:
        calls.append(review_case_id)
        return original(self, review_case_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ReviewWorkflow, "resolve_case", spy)

    with pytest.raises(ReviewConflictError):
        service.resolve_case(
            registered_case,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-b",
            expected_version=99,
        )
    assert calls == []


# --------------------------------------------------------------------------
# Refusals: the database must be untouched
# --------------------------------------------------------------------------


def test_a_transitively_contradicted_match_is_refused_and_writes_nothing(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    resolution_config: EntityResolutionConfig,
) -> None:
    """Section 15: a prior human NO_MATCH still constrains a later MATCH.

    Three records. A human already said rec-a and rec-c are different. Matching
    rec-a/rec-b is fine on its own; matching rec-b/rec-c afterwards would pull
    rec-a and rec-c into one entity, contradicting the earlier decision. Only a
    service that authorizes against the whole persisted queue can see that.
    """
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    state = generate_review_cases(resolution, config=resolution_config)
    ac_case = next(
        case for case in state.cases if case.pair == RecordPair.ordered("rec-a", "rec-c")
    )
    register(repository, state, resolution)

    # The prior decision is recorded through the service, which is now the only
    # way a stored case becomes decided: registration refuses a resolved case.
    # That makes the whole test run on the authoritative path rather than
    # seeding the constraint it then relies on.
    service.resolve_case(
        ac_case.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    ab_case = next(
        case for case in state.cases if case.pair == RecordPair.ordered("rec-a", "rec-b")
    )
    bc_case = next(
        case for case in state.cases if case.pair == RecordPair.ordered("rec-b", "rec-c")
    )

    # Safe in isolation, and allowed.
    service.resolve_case(
        ab_case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-2",
        expected_version=1,
    )

    before = snapshot(repository, bc_case.review_case_id)
    with pytest.raises(HumanReviewContradictionError):
        service.resolve_case(
            bc_case.review_case_id,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-3",
            expected_version=1,
        )
    assert snapshot(repository, bc_case.review_case_id) == before
    assert repository.list_events(bc_case.review_case_id) == ()


def edge_dependent_bridge() -> ResolutionResult:
    """A bridge whose safety depends on the AUTO_MATCH edges and nothing else.

    rec-b and rec-c carry no e-mail, so the reviewed pair is safe considered by
    itself. rec-a and rec-d conflict, and they are only in the same component
    because the two AUTO_MATCH edges put them there. The edges alone decide the
    verdict, which is what makes the counterfactual below meaningful.

    This is the Phase C scenario that proved the persisted bundle carries the
    whole graph; here it is driven through the real service instead.
    """
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz"),
        make_record("rec-c", first_name="Ali", last_name="Yilmaz"),
        make_record("rec-d", first_name="Ali", last_name="Yilmaz", email="d@example.com"),
    )
    return make_bridge_resolution(
        left_ids=("rec-a", "rec-b"),
        right_ids=("rec-c", "rec-d"),
        bridge_ids=("rec-b", "rec-c"),
        records=records,
    )


def test_a_severe_conflict_only_visible_across_the_component_is_refused(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    resolution_config: EntityResolutionConfig,
) -> None:
    """Section 16: the service authorizes against the whole persisted graph.

    The reviewed pair is rec-b/rec-c, and nothing about that pair is unsafe --
    neither record even has an e-mail. The MATCH is refused only because two
    persisted AUTO_MATCH edges drag rec-a and rec-d into the same component,
    where the addresses conflict.

    The counterfactual is the point. ``test_the_same_pair_is_allowed_once_the_
    bridge_is_removed`` registers this identical queue with an empty AUTO_MATCH
    snapshot and the same MATCH is allowed. So this refusal is produced by the
    persisted edges, not by the reviewed pair -- which is the only way to show
    that the service loads the complete authorization component rather than one
    case row.
    """
    resolution = edge_dependent_bridge()
    state = generate_review_cases(resolution, config=resolution_config)
    register(repository, state, resolution)
    bridge_case = state.cases[0]
    assert bridge_case.pair == RecordPair.ordered("rec-b", "rec-c")

    before = snapshot(repository, bridge_case.review_case_id)
    with pytest.raises(HumanReviewAuthorizationError):
        service.resolve_case(
            bridge_case.review_case_id,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )

    assert snapshot(repository, bridge_case.review_case_id) == before
    assert total_events(repository) == 0


def test_the_same_pair_is_allowed_once_the_bridge_is_removed(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    resolution_config: EntityResolutionConfig,
) -> None:
    """The counterfactual that makes the refusal above non-vacuous.

    Same records, same reviewed pair, same service call -- only the persisted
    AUTO_MATCH snapshot is empty. The MATCH now succeeds, so the refusal above
    was caused by the edges the service loaded and by nothing else.

    Read the other way round: a service that dropped the AUTO_MATCH snapshot, or
    that authorized against a single case row, would reach *this* verdict on
    *that* queue and silently approve a merge Sprint 08 refuses.
    """
    resolution = edge_dependent_bridge()
    state = generate_review_cases(resolution, config=resolution_config)
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot={"source_label": "no-auto-match", "auto_match_pairs": []},
        entity_resolution_config_path=ENTITY_RESOLUTION_CONFIG_PATH,
    )
    bridge_case = state.cases[0]
    assert bridge_case.pair == RecordPair.ordered("rec-b", "rec-c")

    result = service.resolve_case(
        bridge_case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    assert result.audit_entry.human_decision == "MATCH"
    stored = repository.get_case(bridge_case.review_case_id)
    assert stored.status is ReviewStatus.MATCH
    assert stored.version == 2


def test_the_bridge_with_a_directly_conflicting_pair_is_also_refused(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    resolution_config: EntityResolutionConfig,
) -> None:
    """The simpler refusal, kept separate so neither claims the other's proof.

    Here rec-b and rec-c conflict with each other, so this MATCH is unsafe on
    the reviewed pair alone and would be refused with or without the AUTO_MATCH
    edges. That is a real case worth covering -- it just proves a different,
    weaker thing than the component test above, and conflating the two is what
    made the original single test vacuous.
    """
    resolution = conflicting_bridge_resolution()
    state = generate_review_cases(resolution, config=resolution_config)
    register(repository, state, resolution)
    bridge_case = state.cases[0]

    before = snapshot(repository, bridge_case.review_case_id)
    with pytest.raises(HumanReviewAuthorizationError):
        service.resolve_case(
            bridge_case.review_case_id,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )
    assert snapshot(repository, bridge_case.review_case_id) == before
    assert total_events(repository) == 0


def test_the_same_bridge_pair_is_still_deferrable(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    resolution_config: EntityResolutionConfig,
) -> None:
    # Negative control: the refusal above is specific to MATCH authorization,
    # not a blanket failure to resolve this case.
    resolution = conflicting_bridge_resolution()
    state = generate_review_cases(resolution, config=resolution_config)
    register(repository, state, resolution)
    bridge_case = state.cases[0]

    service.resolve_case(
        bridge_case.review_case_id,
        decision=HumanReviewDecision.DEFER,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    assert repository.get_case(bridge_case.review_case_id).status is ReviewStatus.DEFERRED


def test_resolving_an_already_resolved_case_is_an_invalid_transition(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    registered_case: str,
) -> None:
    service.resolve_case(
        registered_case,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    before = snapshot(repository, registered_case)

    # Version 2 is correct now, so this is not a conflict: the case is terminal.
    with pytest.raises(InvalidReviewTransitionError):
        service.resolve_case(
            registered_case,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-2",
            expected_version=2,
        )
    assert snapshot(repository, registered_case) == before


def test_an_unknown_case_is_not_found_rather_than_created(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    registered_case: str,
) -> None:
    with pytest.raises(ReviewCaseNotFoundError):
        service.resolve_case(
            "RC-does-not-exist",
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )
    assert total_events(repository) == 0


# --------------------------------------------------------------------------
# Entity-resolution configuration: fail closed, never substitute
# --------------------------------------------------------------------------


def test_a_match_without_a_persisted_config_fails_closed_in_the_domain(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    review_case: object,
) -> None:
    """No config path stored means no authorization context, and Sprint 08 refuses.

    Substituting the default config would be the dangerous alternative: it
    would authorize against thresholds the queue may never have been generated
    with, and the refusal would silently become an approval.
    """
    register(repository, review_state, resolution, entity_resolution_config_path=None)
    case_id = review_state.cases[0].review_case_id
    before = snapshot(repository, case_id)

    with pytest.raises(HumanReviewAuthorizationContextError):
        service.resolve_case(
            case_id,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )
    assert snapshot(repository, case_id) == before


def test_no_match_still_works_without_a_persisted_config(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    # NO_MATCH needs no authorization context in Sprint 08, so a missing config
    # must not block a reviewer from recording the safe decision.
    register(repository, review_state, resolution, entity_resolution_config_path=None)
    case_id = review_state.cases[0].review_case_id

    service.resolve_case(
        case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    assert repository.get_case(case_id).status is ReviewStatus.NO_MATCH


def test_an_unloadable_config_refuses_the_match_instead_of_falling_back(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    register(
        repository,
        review_state,
        resolution,
        entity_resolution_config_path="configs/does_not_exist.yaml",
    )
    case_id = review_state.cases[0].review_case_id
    before = snapshot(repository, case_id)

    with pytest.raises(ReviewAuthorizationConfigError):
        service.resolve_case(
            case_id,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )
    assert snapshot(repository, case_id) == before


def test_the_service_hard_codes_no_threshold(
    resolution_config: EntityResolutionConfig,
) -> None:
    """Section 6: no tuning constant may be copied into the persistence layer.

    ``auto_match_threshold`` belongs to configs/entity_resolution.yaml. A
    literal here would silently stop tracking it, and every Sprint 10 test would
    still pass while authorization drifted from the configured behaviour.
    """
    source = SERVICE_SOURCE.read_text(encoding="utf-8")
    for value in (
        resolution_config.auto_match_threshold,
        resolution_config.review_threshold,
    ):
        assert str(value) not in source


# --------------------------------------------------------------------------
# Reuse, not reimplementation
# --------------------------------------------------------------------------


def test_the_service_calls_the_sprint_08_workflow(
    service: ReviewQueueService,
    registered_case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the workflow is not what decides, nothing else in this file means much."""
    calls: list[str] = []
    original = ReviewWorkflow.resolve_case

    def spy(self: ReviewWorkflow, review_case_id: str, **kwargs: object) -> ReviewWorkflowState:
        calls.append(review_case_id)
        assert kwargs["decision"] is HumanReviewDecision.MATCH
        # MATCH must arrive with the complete authorization context.
        assert kwargs["resolution"] is not None
        assert kwargs["records_by_id"]
        assert kwargs["entity_resolution_config"] is not None
        return original(self, review_case_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ReviewWorkflow, "resolve_case", spy)
    service.resolve_case(
        registered_case,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    assert calls == [registered_case]


def test_every_workflow_is_built_fresh(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    resolution_config: EntityResolutionConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached workflow would authorize against a queue it had not reloaded."""
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    state = generate_review_cases(resolution, config=resolution_config)
    register(repository, state, resolution)

    built: list[int] = []
    original_init = ReviewWorkflow.__init__

    def spy(self: ReviewWorkflow, workflow_state: ReviewWorkflowState) -> None:
        built.append(id(self))
        original_init(self, workflow_state)

    monkeypatch.setattr(ReviewWorkflow, "__init__", spy)

    for case in state.cases[:2]:
        service.resolve_case(
            case.review_case_id,
            decision=HumanReviewDecision.DEFER,
            reviewer_id="reviewer-1",
            expected_version=1,
        )
    assert len(set(built)) == 2


def test_the_service_reuses_the_sprint_08_auto_match_reconstruction(
    tmp_path: Path,
    repository: SqliteReviewCaseRepository,
    resolution_config: EntityResolutionConfig,
) -> None:
    """The seam the service uses is the public report loader's own path.

    ``rebuild_resolution_from_snapshot`` is what ``load_human_review_report``
    calls, and this pins the consequence: the AUTO_MATCH graph the service
    authorizes against must equal the one a real Sprint 08 report round-trip
    produces. A behaviour change fails here rather than quietly changing which
    merges are allowed.
    """
    resolution = conflicting_bridge_resolution()
    state = generate_review_cases(resolution, config=resolution_config)
    register(repository, state, resolution)
    bundle = repository.load_workflow_bundle()

    from_service = ReviewQueueService._rebuild_auto_match_graph(bundle)

    report_path = write_review_reports(
        ReviewWorkflow(state).to_outcome(),
        output_directory=tmp_path,
        entity_records=resolution.records,
        resolution=resolution,
    )
    from_sprint_08 = load_human_review_report(report_path).resolution

    assert from_service.decisions == from_sprint_08.decisions
    assert from_service.records == from_sprint_08.records
    assert from_service.source_label == from_sprint_08.source_label


def _code_without_prose(path: Path) -> str:
    """The module's executable code, with comments and docstrings removed.

    Scanning raw source would flag the module docstring, which legitimately
    explains what AUTO_MATCH edges and versioned updates are for. What matters
    is whether the *code* does any of it, so the prose is stripped first.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if ast.get_docstring(node) is not None:
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_the_service_does_not_reimplement_authorization() -> None:
    """No Sprint 08 authorization logic may be defined in the service module."""
    defined = {
        node.name
        for node in ast.walk(ast.parse(SERVICE_SOURCE.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert not defined & SPRINT_08_AUTHORIZATION_FUNCTIONS
    # The boundary check is a union-find over the record graph. Rebuilding one
    # here would be reimplementing authorization under another name.
    assert not defined & {"find", "union", "component_members", "_UnionFind"}

    code = _code_without_prose(SERVICE_SOURCE)
    for token in ("_UnionFind", "MatchDecisionType", "resolved_match_pairs", "cluster"):
        assert token not in code, token


def test_the_service_contains_no_sql() -> None:
    """Persistence is reached only through the repository Protocol."""
    code = _code_without_prose(SERVICE_SOURCE).upper()
    for verb in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "COMMIT", "ROLLBACK", "SQLITE3"):
        assert verb not in code, verb


# --------------------------------------------------------------------------
# Semantic isolation
# --------------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_the_service_never_reads_a_semantic_suggestion() -> None:
    """Sprint 09 suggestions are advisory. Nothing here may consult one."""
    modules = _imported_modules(SERVICE_SOURCE)
    assert not {module for module in modules if module.split(".")[0] == "semantic_review"}

    source = SERVICE_SOURCE.read_text(encoding="utf-8")
    for token in (
        "semantic_suggestions",
        "SemanticSuggestion",
        "list_semantic_suggestions",
        "record_semantic_suggestion",
        "openai",
        "requests",
        "httpx",
        "urllib",
    ):
        assert token not in source, token


def test_the_service_never_infers_a_decision() -> None:
    """``decision`` is a required keyword argument and has no default.

    A default would be the first step toward a decision the service chose
    itself. The signature is what makes "the caller decided" checkable.
    """
    signature = inspect.signature(ReviewQueueService.resolve_case)
    decision = signature.parameters["decision"]
    assert decision.default is inspect.Parameter.empty
    assert decision.kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["expected_version"].default is inspect.Parameter.empty


def test_the_service_module_opens_no_network_connection() -> None:
    # Belt and braces alongside the import check: no client, no URL, no socket.
    source = service_module.__file__
    assert source is not None
    code = _code_without_prose(Path(source))
    assert not re.search(r"https?://", code)
    assert "socket" not in code
