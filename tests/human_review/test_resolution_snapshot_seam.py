"""``rebuild_resolution_from_snapshot`` is the inverse of ``resolution_snapshot``.

The helper was made public so a persisted review queue can rebuild the
AUTO_MATCH graph without a report file and without a second implementation. It
is authorization-sensitive: the edges it produces decide which records end up in
one component, and therefore which human MATCH decisions are allowed.

What is pinned here is that the public helper and the public report loader agree
exactly. They share one implementation today; these tests make a future
divergence a failure rather than a quiet change in what merges are permitted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import MatchDecisionType, ResolutionResult
from human_review.cases import generate_review_cases
from human_review.errors import HumanReviewReportError
from human_review.reporting import (
    load_human_review_report,
    rebuild_resolution_from_snapshot,
    resolution_snapshot,
    write_review_reports,
)
from human_review.workflow import ReviewWorkflow
from tests.human_review.conftest import make_bridge_resolution, make_record


def bridge_resolution() -> ResolutionResult:
    """Two AUTO_MATCH pairs and one REVIEW bridge: more than one edge to lose."""
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-c", first_name="Ali", last_name="Yilmaz", email="c@example.com"),
        make_record("rec-d", first_name="Ali", last_name="Yilmaz", email="d@example.com"),
    )
    return make_bridge_resolution(
        left_ids=("rec-a", "rec-b"),
        right_ids=("rec-c", "rec-d"),
        bridge_ids=("rec-b", "rec-c"),
        records=records,
    )


def auto_match_pairs(resolution: ResolutionResult) -> set[tuple[str, str]]:
    return {
        (decision.pair.record_a_id, decision.pair.record_b_id)
        for decision in resolution.decisions
        if decision.decision is MatchDecisionType.AUTO_MATCH
    }


def test_the_public_helper_matches_the_report_loader_exactly(
    tmp_path: Path,
    resolution_config: EntityResolutionConfig,
) -> None:
    """The seam and the loader must produce the same ResolutionResult.

    The loader is the path Sprint 08 already trusted. Anything the public helper
    returns that differs from it would be a reconstruction nobody reviewed.
    """
    resolution = bridge_resolution()
    state = generate_review_cases(resolution, config=resolution_config)
    report_path = write_review_reports(
        ReviewWorkflow(state).to_outcome(),
        output_directory=tmp_path,
        entity_records=resolution.records,
        resolution=resolution,
    )
    loaded = load_human_review_report(report_path)

    rebuilt = rebuild_resolution_from_snapshot(
        resolution.records,
        resolution_snapshot(resolution),
    )

    assert rebuilt.decisions == loaded.resolution.decisions
    assert rebuilt.records == loaded.resolution.records
    assert rebuilt.source_label == loaded.resolution.source_label
    assert rebuilt.summary == loaded.resolution.summary
    assert rebuilt == loaded.resolution


def test_the_snapshot_round_trip_preserves_every_auto_match_edge() -> None:
    resolution = bridge_resolution()
    original = auto_match_pairs(resolution)
    assert len(original) == 2, "Fixture must exercise more than one edge."

    rebuilt = rebuild_resolution_from_snapshot(
        resolution.records,
        resolution_snapshot(resolution),
    )

    assert auto_match_pairs(rebuilt) == original
    assert rebuilt.summary.auto_match_count == len(original)


def test_only_auto_match_decisions_are_rebuilt() -> None:
    # REVIEW and NO_MATCH decisions are not persisted in the snapshot and are
    # not authorization inputs, so the rebuild must not invent them.
    resolution = bridge_resolution()
    assert any(decision.decision is MatchDecisionType.REVIEW for decision in resolution.decisions)

    rebuilt = rebuild_resolution_from_snapshot(
        resolution.records,
        resolution_snapshot(resolution),
    )

    assert {decision.decision for decision in rebuilt.decisions} == {MatchDecisionType.AUTO_MATCH}
    assert rebuilt.review_queue == ()


def test_an_empty_snapshot_rebuilds_an_empty_graph() -> None:
    resolution = bridge_resolution()

    rebuilt = rebuild_resolution_from_snapshot(
        resolution.records,
        {"source_label": "none", "auto_match_pairs": []},
    )

    assert rebuilt.decisions == ()
    assert rebuilt.records == resolution.records


@pytest.mark.parametrize(
    "snapshot",
    [
        {"source_label": "bad", "auto_match_pairs": "not-a-list"},
        {"source_label": "bad", "auto_match_pairs": [["only-one-id"]]},
        {"source_label": "bad", "auto_match_pairs": [{"a": "b"}]},
        {"source_label": "bad"},
    ],
)
def test_a_malformed_snapshot_fails_closed(snapshot: dict) -> None:
    # Same refusal the report loader has always given. Making the helper public
    # must not make it lenient.
    with pytest.raises(HumanReviewReportError):
        rebuild_resolution_from_snapshot((), snapshot)
