"""Proof that a reloaded bundle carries the whole Sprint 08 authorization graph.

Phase C does not authorize anything and does not call ``resolve_case``. What it
must guarantee is that a later service loading a bundle sees exactly what
Sprint 08 sees today when it reconstructs from ``human_review_report.json``.

The tests below establish that by running the existing, unmodified Sprint 08
checks against material that has been through SQLite and back, and requiring the
same verdict as against the original in-memory objects. The scenario is chosen so
that losing any single piece -- one entity record, one AUTO_MATCH edge, or one
resolved case -- would flip a refusal into an approval.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import MatchDecisionType, RecordPair, ResolutionResult
from human_review.authorization import (
    assert_human_match_authorization_boundary,
    projected_review_component_member_ids,
)
from human_review.cases import generate_review_cases
from human_review.errors import HumanReviewAuthorizationError, HumanReviewContradictionError
from human_review.models import (
    HumanReviewDecision,
    HumanReviewOutcome,
    ReviewStatus,
    ReviewWorkflowState,
)
from human_review.reporting import (
    REVIEW_REPORT_ARTIFACT_TYPE,
    REVIEW_REPORT_SCHEMA_VERSION,
    entity_records_to_dict,
    load_human_review_report,
    resolution_snapshot,
    workflow_state_to_dict,
)
from human_review.workflow import ReviewWorkflow
from review_application.models import WorkflowBundle
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.human_review.conftest import make_record, make_review_resolution


def _report_payload_from_bundle(bundle: WorkflowBundle) -> dict[str, Any]:
    """Rebuild the Sprint 08 report envelope from persisted material only.

    This is the move a future service makes: everything below comes from the
    bundle, using public Sprint 08 serializers. If persistence had dropped
    anything authorization needs, this payload could not be assembled and
    ``load_human_review_report`` would reject it.
    """
    return {
        "schema_version": REVIEW_REPORT_SCHEMA_VERSION,
        "artifact_type": REVIEW_REPORT_ARTIFACT_TYPE,
        "entity_resolution_config_path": bundle.entity_resolution_config_path,
        "entity_records": entity_records_to_dict(bundle.entity_records),
        "resolution_snapshot": bundle.resolution_snapshot,
        "summary": {},
        "workflow_state": workflow_state_to_dict(bundle.to_workflow_state()),
    }


def _reconstruct_through_sprint_08(bundle: WorkflowBundle, tmp_path: Path):
    """Run the persisted bundle through the public Sprint 08 loader."""
    report_path = tmp_path / "human_review_report.json"
    report_path.write_text(
        json.dumps(_report_payload_from_bundle(bundle), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return load_human_review_report(report_path)


def _auto_match_pairs(resolution: ResolutionResult) -> set[tuple[str, str]]:
    return {
        (decision.pair.record_a_id, decision.pair.record_b_id)
        for decision in resolution.decisions
        if decision.decision is MatchDecisionType.AUTO_MATCH
    }


@pytest.fixture
def bridge_workflow(
    repository: SqliteReviewCaseRepository,
    bridge_resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> tuple[ReviewWorkflowState, ResolutionResult]:
    """Persist the conflicting bridge workflow and return its domain originals."""
    state = generate_review_cases(bridge_resolution, config=resolution_config)
    repository.register_workflow(
        state,
        entity_records=bridge_resolution.records,
        resolution_snapshot=resolution_snapshot(bridge_resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    return state, bridge_resolution


# --------------------------------------------------------------------------
# Sprint 08 reconstruction equivalence
# --------------------------------------------------------------------------


def test_bundle_reconstructs_a_valid_sprint_08_report(
    repository: SqliteReviewCaseRepository,
    bridge_workflow: tuple[ReviewWorkflowState, ResolutionResult],
    tmp_path: Path,
) -> None:
    state, resolution = bridge_workflow

    loaded = _reconstruct_through_sprint_08(repository.load_workflow_bundle(), tmp_path)

    assert loaded.entity_records == resolution.records
    assert loaded.outcome.workflow_state.cases == state.cases
    assert loaded.entity_resolution_config_path == "configs/entity_resolution.yaml"


def test_reconstructed_resolution_carries_every_auto_match_edge(
    repository: SqliteReviewCaseRepository,
    bridge_workflow: tuple[ReviewWorkflowState, ResolutionResult],
    tmp_path: Path,
) -> None:
    _, resolution = bridge_workflow
    original_edges = _auto_match_pairs(resolution)
    assert len(original_edges) == 2, "Fixture must exercise more than one AUTO_MATCH edge."

    loaded = _reconstruct_through_sprint_08(repository.load_workflow_bundle(), tmp_path)

    assert _auto_match_pairs(loaded.resolution) == original_edges


def test_persisted_snapshot_equals_the_public_sprint_08_snapshot(
    repository: SqliteReviewCaseRepository,
    bridge_workflow: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    _, resolution = bridge_workflow

    bundle = repository.load_workflow_bundle()

    assert bundle.resolution_snapshot == resolution_snapshot(resolution)
    assert entity_records_to_dict(bundle.entity_records) == entity_records_to_dict(
        resolution.records
    )


# --------------------------------------------------------------------------
# Transitive authorization: the reloaded graph reaches the same verdict
# --------------------------------------------------------------------------


def test_reloaded_bundle_still_blocks_a_transitively_unsafe_match(
    repository: SqliteReviewCaseRepository,
    bridge_workflow: tuple[ReviewWorkflowState, ResolutionResult],
    resolution_config: EntityResolutionConfig,
    tmp_path: Path,
) -> None:
    """The heart of Phase C.

    A MATCH on the rec-b/rec-c bridge is unsafe only because the two AUTO_MATCH
    edges drag rec-a and rec-d into the same component, where the e-mail
    addresses conflict severely. A bundle that lost a record or an edge would
    make this check pass -- silently authorizing a merge Sprint 08 refuses.
    """
    state, resolution = bridge_workflow
    bridge_pair = state.cases[0].pair

    # Baseline: the in-memory Sprint 08 objects refuse this MATCH.
    with pytest.raises(HumanReviewAuthorizationError):
        assert_human_match_authorization_boundary(
            pair=bridge_pair,
            outcome=HumanReviewOutcome(workflow_state=state),
            resolution=resolution,
            records_by_id={record.record_id: record for record in resolution.records},
            config=resolution_config,
        )

    # Same refusal from material that has been through SQLite and back.
    bundle = repository.load_workflow_bundle()
    loaded = _reconstruct_through_sprint_08(bundle, tmp_path)

    with pytest.raises(HumanReviewAuthorizationError):
        assert_human_match_authorization_boundary(
            pair=bridge_pair,
            outcome=loaded.outcome,
            resolution=loaded.resolution,
            records_by_id=dict(bundle.records_by_id()),
            config=resolution_config,
        )


def test_reloaded_bundle_projects_the_same_component(
    repository: SqliteReviewCaseRepository,
    bridge_workflow: tuple[ReviewWorkflowState, ResolutionResult],
    resolution_config: EntityResolutionConfig,
    tmp_path: Path,
) -> None:
    """A single-row view would project a two-member component, not four."""
    state, resolution = bridge_workflow
    bridge_pair = state.cases[0].pair

    expected = projected_review_component_member_ids(
        anchor_pair=bridge_pair,
        outcome=HumanReviewOutcome(workflow_state=state),
        resolution=resolution,
        records_by_id={record.record_id: record for record in resolution.records},
        config=resolution_config,
        additional_match_pair=bridge_pair,
        force_additional_match=True,
    )
    assert len(expected) == 4

    bundle = repository.load_workflow_bundle()
    loaded = _reconstruct_through_sprint_08(bundle, tmp_path)

    actual = projected_review_component_member_ids(
        anchor_pair=bridge_pair,
        outcome=loaded.outcome,
        resolution=loaded.resolution,
        records_by_id=dict(bundle.records_by_id()),
        config=resolution_config,
        additional_match_pair=bridge_pair,
        force_additional_match=True,
    )
    assert actual == expected


def _edge_dependent_bridge() -> ResolutionResult:
    """Only the AUTO_MATCH-reachable records conflict with each other.

    rec-b and rec-c carry no e-mail, so the reviewed pair is safe in isolation.
    rec-a and rec-d conflict, and they are only in the component because the two
    AUTO_MATCH edges put them there. The edges alone decide the verdict.
    """
    from tests.human_review.conftest import make_bridge_resolution

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


def test_auto_match_edges_are_what_make_the_match_unsafe(
    repository: SqliteReviewCaseRepository,
    resolution_config: EntityResolutionConfig,
    tmp_path: Path,
) -> None:
    """Negative control: proves the transitive tests above are not vacuous.

    The same reviewed pair is refused with the persisted AUTO_MATCH edges and
    allowed without them. So a bundle that dropped the snapshot would silently
    authorize a merge Sprint 08 refuses -- exactly the failure Phase B declined
    to ship and Phase C exists to prevent.
    """
    resolution = _edge_dependent_bridge()
    state = generate_review_cases(resolution, config=resolution_config)
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=None,
    )
    bridge_pair = state.cases[0].pair
    assert bridge_pair == RecordPair.ordered("rec-b", "rec-c")

    bundle = repository.load_workflow_bundle()
    loaded = _reconstruct_through_sprint_08(bundle, tmp_path)

    # With the persisted edges: refused.
    with pytest.raises(HumanReviewAuthorizationError):
        assert_human_match_authorization_boundary(
            pair=bridge_pair,
            outcome=loaded.outcome,
            resolution=loaded.resolution,
            records_by_id=dict(bundle.records_by_id()),
            config=resolution_config,
        )

    # Strip only the AUTO_MATCH snapshot: allowed.
    crippled = WorkflowBundle(
        persisted_cases=bundle.persisted_cases,
        entity_records=bundle.entity_records,
        resolution_snapshot={"source_label": "stripped", "auto_match_pairs": []},
        next_resolution_sequence=bundle.next_resolution_sequence,
        entity_resolution_config_path=bundle.entity_resolution_config_path,
    )
    stripped = _reconstruct_through_sprint_08(crippled, tmp_path)
    assert_human_match_authorization_boundary(
        pair=bridge_pair,
        outcome=stripped.outcome,
        resolution=stripped.resolution,
        records_by_id=dict(crippled.records_by_id()),
        config=resolution_config,
    )


def test_bundle_covers_every_record_the_snapshot_references(
    repository: SqliteReviewCaseRepository,
    resolution_config: EntityResolutionConfig,
) -> None:
    """The record set matters as much as the edges.

    projected_review_component_member_ids indexes into records_by_id for every
    AUTO_MATCH endpoint, so an incomplete record set does not quietly shrink the
    component -- it makes the Sprint 08 check unable to run at all. Either way a
    partial bundle is unusable, which is why the loaded one must be complete.
    """
    resolution = _edge_dependent_bridge()
    state = generate_review_cases(resolution, config=resolution_config)
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=None,
    )
    bundle = repository.load_workflow_bundle()
    bridge_pair = state.cases[0].pair
    outcome = HumanReviewOutcome(workflow_state=bundle.to_workflow_state())

    referenced = {
        record_id for pair in bundle.resolution_snapshot["auto_match_pairs"] for record_id in pair
    }
    assert referenced <= set(bundle.records_by_id())

    full = projected_review_component_member_ids(
        anchor_pair=bridge_pair,
        outcome=outcome,
        resolution=resolution,
        records_by_id=dict(bundle.records_by_id()),
        config=resolution_config,
        additional_match_pair=bridge_pair,
        force_additional_match=True,
    )
    assert full == ("rec-a", "rec-b", "rec-c", "rec-d")

    partial = {
        record_id: record
        for record_id, record in bundle.records_by_id().items()
        if record_id != "rec-d"
    }
    with pytest.raises(KeyError):
        projected_review_component_member_ids(
            anchor_pair=bridge_pair,
            outcome=outcome,
            resolution=resolution,
            records_by_id=partial,
            config=resolution_config,
            additional_match_pair=bridge_pair,
            force_additional_match=True,
        )


def test_reloaded_bundle_still_sees_a_prior_human_no_match(
    repository: SqliteReviewCaseRepository,
    resolution_config: EntityResolutionConfig,
    tmp_path: Path,
) -> None:
    """A NO_MATCH recorded on one pair must still constrain another pair later.

    Three records in a chain: a human NO_MATCH on rec-a/rec-c has to survive
    persistence, or a later MATCH on rec-a/rec-b plus rec-b/rec-c would
    transitively merge two records a human said were different.
    """
    from tests.human_review.conftest import make_triangle_review_resolution

    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    state = generate_review_cases(resolution, config=resolution_config)
    workflow = ReviewWorkflow(state)

    ac_case = next(
        case for case in state.cases if case.pair == RecordPair.ordered("rec-a", "rec-c")
    )
    resolved_state = workflow.resolve_case(
        ac_case.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
    )

    repository.register_workflow(
        resolved_state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )

    bundle = repository.load_workflow_bundle()
    loaded = _reconstruct_through_sprint_08(bundle, tmp_path)

    # The persisted NO_MATCH is visible as a constraint, not just as a status.
    assert loaded.outcome.resolved_no_match_pairs() == frozenset(
        {RecordPair.ordered("rec-a", "rec-c")}
    )
    assert any(case.status is ReviewStatus.NO_MATCH for case in bundle.cases())

    # And it still blocks the transitive merge that would contradict it.
    ab_pair = RecordPair.ordered("rec-a", "rec-b")
    bc_pair = RecordPair.ordered("rec-b", "rec-c")
    with pytest.raises(HumanReviewContradictionError):
        assert_human_match_authorization_boundary(
            pair=bc_pair,
            outcome=loaded.outcome,
            resolution=loaded.resolution,
            records_by_id=dict(bundle.records_by_id()),
            config=resolution_config,
            existing_match_pairs=frozenset({ab_pair, bc_pair}),
        )


def test_bundle_records_cover_every_case_record(
    repository: SqliteReviewCaseRepository,
    bridge_workflow: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    bundle = repository.load_workflow_bundle()
    known = set(bundle.records_by_id())

    for case in bundle.cases():
        assert case.pair.record_a_id in known
        assert case.pair.record_b_id in known
    assert known == {"rec-a", "rec-b", "rec-c", "rec-d"}


def test_single_pair_workflow_still_loads(
    repository: SqliteReviewCaseRepository,
    resolution_config: EntityResolutionConfig,
    tmp_path: Path,
) -> None:
    # Sanity: the machinery is not specific to the bridge fixture.
    resolution = make_review_resolution("a-1", "a-2")
    state = generate_review_cases(resolution, config=resolution_config)
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=None,
    )

    loaded = _reconstruct_through_sprint_08(repository.load_workflow_bundle(), tmp_path)

    assert loaded.entity_records == resolution.records
    assert loaded.entity_resolution_config_path is None
    assert make_record  # imported fixture helper stays referenced
