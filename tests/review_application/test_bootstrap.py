"""The production path that brings a review queue into existence.

Every test here goes through ``register_review_queue`` against the real SQLite
repository, because the thing being checked is not that the function calls
``register_workflow`` -- it is that the Sprint 10 guarantees survive being
driven by production code that regenerates its input from scratch.

That is the failure this file exists to catch. Deterministic case generation
always emits the PENDING form of every case, so re-running the pipeline after a
reviewer has worked is the normal operational rhythm, and a bootstrap that
merely "stored what it was given" would erase human decisions on every run
without a single error.

The fixtures are the Sprint 10 ones (``tests/review_application/conftest.py``):
a real resolution, real ``generate_review_cases`` output, and a tmp_path
database. No test here may touch ``storage/review_queue.db``.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import MatchDecisionType, ResolutionResult
from human_review.cases import generate_review_cases
from human_review.models import HumanReviewDecision, ReviewStatus, ReviewWorkflowState
from human_review.reporting import resolution_snapshot
from review_application import (
    ReviewQueueRegistration,
    ReviewQueueService,
    register_review_queue,
)
from review_application.errors import (
    ReviewPersistenceError,
    ReviewWorkflowContextConflictError,
)
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite.database import open_review_database
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.human_review.conftest import make_record
from tests.review_application.conftest import ENTITY_RESOLUTION_CONFIG_PATH
from tests.review_persistence.conftest import FrozenClock
from tests.review_persistence.semantic_fixtures import make_suggestion


def bootstrap(
    repository: SqliteReviewCaseRepository,
    state: ReviewWorkflowState,
    resolution: ResolutionResult,
    *,
    config_path: str | None = ENTITY_RESOLUTION_CONFIG_PATH,
) -> ReviewQueueRegistration:
    """One production registration, spelled the way the CLI spells it."""
    return register_review_queue(
        repository,
        state=state,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path=config_path,
    )


def resolve(
    service: ReviewQueueService,
    review_case_id: str,
    *,
    decision: HumanReviewDecision = HumanReviewDecision.MATCH,
    expected_version: int = 1,
) -> None:
    """Resolve through the Sprint 10 application service, never by hand."""
    service.resolve_case(
        review_case_id,
        decision=decision,
        reviewer_id="reviewer-1",
        expected_version=expected_version,
    )


def records_by_id(resolution: ResolutionResult) -> dict:
    return {record.record_id: record for record in resolution.records}


def empty_workflow(
    resolution: ResolutionResult,
    config: EntityResolutionConfig,
) -> tuple[ReviewWorkflowState, ResolutionResult]:
    """A resolution that routed nothing to REVIEW, and its generated state.

    Produced by dropping the review queue from a real resolution and running
    production case generation over it, so the emptiness comes from Sprint 08
    rather than from a hand-built state object.
    """
    quiet = replace(resolution, review_queue=())
    return generate_review_cases(quiet, config=config), quiet


# --------------------------------------------------------------------------
# Normal registration
# --------------------------------------------------------------------------


def test_registration_persists_every_generated_case(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    registration = bootstrap(repository, review_state, resolution)

    stored = repository.list_cases()
    assert len(stored) == len(review_state.cases)
    assert {case.review_case_id for case in stored} == {
        case.review_case_id for case in review_state.cases
    }
    assert registration.total_cases == len(review_state.cases)
    assert registration.newly_registered == len(review_state.cases)
    assert registration.already_present == 0
    assert registration.pending_cases == len(review_state.cases)
    assert registration.resolved_cases == 0
    assert registration.created_anything
    assert not registration.was_idempotent


def test_registration_persists_the_trusted_authorization_context(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """A queue is only usable if the context loads back as a complete bundle.

    ``load_workflow_bundle`` fails closed on a missing record or a missing
    AUTO_MATCH snapshot, so a bundle that loads is the proof that bootstrap
    stored the authorization material and not only the cases.
    """
    bootstrap(repository, review_state, resolution)

    bundle = repository.load_workflow_bundle()

    assert {record.record_id for record in bundle.entity_records} == {
        record.record_id for record in resolution.records
    }
    assert bundle.resolution_snapshot == resolution_snapshot(resolution)
    assert bundle.entity_resolution_config_path == ENTITY_RESOLUTION_CONFIG_PATH


def test_the_stored_snapshot_is_the_sprint_08_reduction(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """The caller hands over a ResolutionResult; bootstrap reduces it.

    Reducing it here rather than in the command is what keeps a queue's
    authorization graph from disagreeing with the report written beside it.
    """
    bootstrap(repository, review_state, resolution)

    assert repository.load_workflow_bundle().resolution_snapshot == resolution_snapshot(resolution)


def test_a_registered_queue_survives_a_restart(
    persistence_config: ReviewPersistenceConfig,
    clock: FrozenClock,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """Register, close the database entirely, reopen, and read it back."""
    first = open_review_database(persistence_config, clock=clock)
    try:
        bootstrap(SqliteReviewCaseRepository(first, clock=clock), review_state, resolution)
    finally:
        first.close()

    second = open_review_database(persistence_config, clock=clock)
    try:
        reopened = SqliteReviewCaseRepository(second, clock=clock)
        assert len(reopened.list_cases()) == len(review_state.cases)
        assert reopened.load_workflow_bundle().entity_records
    finally:
        second.close()


# --------------------------------------------------------------------------
# Exact re-bootstrap
# --------------------------------------------------------------------------


def test_re_bootstrapping_the_same_workflow_stores_nothing_new(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    bootstrap(repository, review_state, resolution)
    before = repository.list_cases()

    second = bootstrap(repository, review_state, resolution)

    after = repository.list_cases()
    assert after == before
    assert second.newly_registered == 0
    assert second.already_present == len(before)
    assert second.was_idempotent
    assert not second.created_anything


def test_re_bootstrapping_appends_no_event(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """History is what an audit reads. Registration is not an event in it."""
    bootstrap(repository, review_state, resolution)
    case_id = review_state.cases[0].review_case_id
    before = repository.list_events(case_id)

    bootstrap(repository, review_state, resolution)

    assert repository.list_events(case_id) == before


# --------------------------------------------------------------------------
# The decision that must never be erased
# --------------------------------------------------------------------------


@pytest.fixture
def resolved_queue(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> str:
    """A bootstrapped queue with one case resolved through the real service."""
    bootstrap(repository, review_state, resolution)
    case_id = review_state.cases[0].review_case_id
    resolve(service, case_id)
    return case_id


def test_re_bootstrapping_never_returns_a_resolved_case_to_pending(
    repository: SqliteReviewCaseRepository,
    resolved_queue: str,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """The central Phase D guarantee.

    ``review_state`` is the generated PENDING form of exactly this workflow --
    the same object a re-run of the pipeline would produce -- so registering it
    again is the real operational hazard, not a contrived one.
    """
    bootstrap(repository, review_state, resolution)

    assert repository.get_case(resolved_queue).status is ReviewStatus.MATCH
    assert not [case for case in repository.list_cases() if case.status is ReviewStatus.PENDING]


def test_re_bootstrapping_preserves_version_resolution_and_history(
    repository: SqliteReviewCaseRepository,
    resolved_queue: str,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    before = repository.get_case(resolved_queue)
    events_before = repository.list_events(resolved_queue)

    bootstrap(repository, review_state, resolution)

    after = repository.get_case(resolved_queue)
    assert after.version == before.version
    assert after.case.resolution == before.case.resolution
    assert after.updated_at_utc == before.updated_at_utc
    assert repository.list_events(resolved_queue) == events_before


def test_a_re_bootstrapped_queue_counts_the_resolved_case_as_resolved(
    repository: SqliteReviewCaseRepository,
    resolved_queue: str,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    registration = bootstrap(repository, review_state, resolution)

    assert registration.resolved_cases == 1
    assert registration.newly_registered == 0
    assert registration.pending_cases == registration.total_cases - 1


# --------------------------------------------------------------------------
# Advisory suggestions are not registration material either
# --------------------------------------------------------------------------


def test_a_stored_suggestion_survives_re_bootstrap_unchanged(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    bootstrap(repository, review_state, resolution)
    case = review_state.cases[0]
    suggestion = make_suggestion(case, records_by_id(resolution))
    repository.record_semantic_suggestion(suggestion, now_utc="2026-09-12T09:00:00Z")
    before = repository.list_semantic_suggestions(case.review_case_id)
    events_before = repository.list_events(case.review_case_id)

    bootstrap(repository, review_state, resolution)

    assert repository.list_semantic_suggestions(case.review_case_id) == before
    assert len(before) == 1
    assert repository.list_events(case.review_case_id) == events_before


# --------------------------------------------------------------------------
# A changed context is refused, and refused without touching anything
# --------------------------------------------------------------------------


def changed_records(resolution: ResolutionResult) -> ResolutionResult:
    """The same pair, one record carrying a different value."""
    first, second = resolution.records
    edited = make_record(
        first.record_id,
        first_name="Ali",
        last_name="Yilmaz",
        email="moved@example.com",
    )
    return replace(resolution, records=(edited, second))


def changed_snapshot(resolution: ResolutionResult) -> ResolutionResult:
    """An AUTO_MATCH edge that was not there when the queue was generated.

    This is the dangerous shape: an extra AUTO_MATCH edge widens the component
    that MATCH authorization projects over, so accepting it would re-evaluate
    decisions already recorded against a graph nobody reviewed.
    """
    promoted = replace(resolution.decisions[0], decision=MatchDecisionType.AUTO_MATCH)
    return replace(resolution, decisions=(*resolution.decisions, promoted))


CONTEXT_MUTATIONS = [
    pytest.param(changed_records, id="changed-records"),
    pytest.param(changed_snapshot, id="changed-snapshot"),
]


@pytest.mark.parametrize("mutate", CONTEXT_MUTATIONS)
def test_a_changed_context_is_refused(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    mutate,
) -> None:
    bootstrap(repository, review_state, resolution)

    with pytest.raises(ReviewWorkflowContextConflictError):
        bootstrap(repository, review_state, mutate(resolution))


def test_a_different_entity_resolution_config_path_is_refused(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """The config decides what a severe conflict is; authorizing against another is unsafe."""
    bootstrap(repository, review_state, resolution)

    with pytest.raises(ReviewWorkflowContextConflictError):
        bootstrap(repository, review_state, resolution, config_path="configs/other.yaml")


@pytest.mark.parametrize("mutate", CONTEXT_MUTATIONS)
def test_a_refused_registration_leaves_the_queue_and_its_decisions_intact(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    mutate,
) -> None:
    """Fail-closed is only worth anything if it is also fail-without-writing."""
    bootstrap(repository, review_state, resolution)
    case_id = review_state.cases[0].review_case_id
    resolve(service, case_id)
    before = repository.list_cases()
    bundle_before = repository.load_workflow_bundle()
    events_before = repository.list_events(case_id)

    with pytest.raises(ReviewWorkflowContextConflictError):
        bootstrap(repository, review_state, mutate(resolution))

    assert repository.list_cases() == before
    assert repository.get_case(case_id).status is ReviewStatus.MATCH
    assert repository.list_events(case_id) == events_before
    after = repository.load_workflow_bundle()
    assert after.entity_records == bundle_before.entity_records
    assert after.resolution_snapshot == bundle_before.resolution_snapshot


def test_a_refused_config_path_writes_no_new_case(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """The conflicting registration carries every case, so a partial write would show."""
    bootstrap(repository, review_state, resolution)
    before = repository.list_cases()

    with pytest.raises(ReviewWorkflowContextConflictError):
        bootstrap(repository, review_state, resolution, config_path="configs/other.yaml")

    assert repository.list_cases() == before


# --------------------------------------------------------------------------
# Edge shapes with a pinned answer rather than a guess
# --------------------------------------------------------------------------


def test_a_workflow_with_no_review_cases_registers_a_valid_empty_queue(
    repository: SqliteReviewCaseRepository,
    resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> None:
    """Zero REVIEW cases is a legitimate production outcome, not an error.

    The entity records and the AUTO_MATCH snapshot are still the authorization
    context, and storing them is what makes the next run an idempotent no-op
    rather than a first registration against a different graph.
    """
    empty_state, quiet = empty_workflow(resolution, resolution_config)
    assert empty_state.cases == ()

    registration = bootstrap(repository, empty_state, quiet)

    assert registration.total_cases == 0
    assert repository.list_cases() == ()
    assert repository.load_workflow_bundle().entity_records
    # Neither label applies: case counts cannot tell whether the stored
    # authorization context is new, so bootstrap claims neither.
    assert not registration.created_anything
    assert not registration.was_idempotent


def test_an_empty_workflow_can_be_re_bootstrapped(
    repository: SqliteReviewCaseRepository,
    resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> None:
    empty_state, quiet = empty_workflow(resolution, resolution_config)
    bootstrap(repository, empty_state, quiet)

    assert bootstrap(repository, empty_state, quiet).total_cases == 0
    assert repository.list_cases() == ()


def test_an_empty_workflow_does_not_erase_a_queue_registered_from_the_same_context(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> None:
    """Registration never deletes. An empty run confirms the context, nothing more."""
    bootstrap(repository, review_state, resolution)
    before = repository.list_cases()
    empty_state, quiet = empty_workflow(resolution, resolution_config)

    registration = bootstrap(repository, empty_state, quiet)

    assert repository.list_cases() == before
    assert registration.total_cases == 0


def test_registering_without_entity_records_is_refused(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """MATCH authorization would fail closed forever after, so the write is refused."""
    with pytest.raises(ReviewPersistenceError):
        bootstrap(repository, review_state, replace(resolution, records=()))

    assert repository.list_cases() == ()


# --------------------------------------------------------------------------
# What the production path is not allowed to depend on
# --------------------------------------------------------------------------

PRODUCTION_SOURCES = (
    Path("review_application/bootstrap.py"),
    Path("scripts/manage_human_review.py"),
)

# Benchmark and ground-truth infrastructure. A production registration that
# could reach any of it would be deciding review outcomes from the answer key.
FORBIDDEN_IN_THE_PRODUCTION_PATH = (
    "final_holdout",
    "golden",
    "oracle",
    "expected_cluster",
    "ground_truth",
    "evaluation",
    "benchmark",
)


def code_without_prose(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if ast.get_docstring(node) is not None:
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


@pytest.mark.parametrize("path", PRODUCTION_SOURCES, ids=lambda path: path.name)
@pytest.mark.parametrize("token", FORBIDDEN_IN_THE_PRODUCTION_PATH)
def test_the_bootstrap_path_names_no_benchmark_material(path: Path, token: str) -> None:
    assert token not in code_without_prose(path).lower(), f"{path} names {token}"


def test_the_bootstrap_module_holds_no_threshold_of_its_own() -> None:
    """Bootstrap stores an authorization graph; it never decides one.

    A float literal here would mean a second place that could disagree with
    ``configs/entity_resolution.yaml`` about what AUTO_MATCH means.
    """
    tree = ast.parse(Path("review_application/bootstrap.py").read_text(encoding="utf-8"))
    floats = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]

    assert floats == []
