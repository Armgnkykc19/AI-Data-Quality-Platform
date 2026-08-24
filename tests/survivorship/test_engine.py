from __future__ import annotations

import pytest

from entity_resolution.engine import resolve_entities
from entity_resolution.models import (
    EntityRecord,
    ResolutionResult,
    ResolutionSummary,
)
from survivorship.engine import build_canonical_entities
from tests.survivorship.conftest import make_record


def test_build_canonical_entities_from_auto_match_cluster(survivorship_config):
    left = make_record("a-1", email="shared@example.com", first_name="Ali")
    right = make_record(
        "b-1",
        source_name="source_b",
        email="shared@example.com",
        first_name="Aliye",
    )
    resolution = resolve_entities([left, right])
    result = build_canonical_entities(resolution, config=survivorship_config)

    merged = next(entity for entity in result.entities if len(entity.member_record_ids) > 1)
    assert set(merged.member_record_ids) == {"a-1", "b-1"}
    assert merged.field_values["email"] == left.field_values["email"]
    assert merged.field_values["first_name"] == "Aliye"
    assert any(item.field_name == "email" for item in merged.provenance)
    assert result.summary.merged_entity_count == 1


def test_preserves_field_conflicts_without_hiding_disagreement(survivorship_config):
    left = make_record("a-1", email="shared@example.com", company="Acme Corp")
    right = make_record(
        "b-1",
        source_name="source_b",
        email="shared@example.com",
        company="Acme Incorporated",
    )
    resolution = resolve_entities([left, right])
    result = build_canonical_entities(resolution, config=survivorship_config)
    merged = next(entity for entity in result.entities if len(entity.member_record_ids) > 1)

    assert merged.preserved_conflicts
    assert merged.has_unresolved_conflicts
    assert any(item.field_name == "company" for item in merged.preserved_conflicts)


def test_review_queue_records_are_excluded_from_merged_entities(survivorship_config):
    auto_left = make_record("auto-1", email="shared@example.com")
    auto_right = make_record("auto-2", email="shared@example.com")
    review_left = make_record("rev-1", email="shared@example.com", company="Alpha")
    review_right = make_record("rev-2", email="different@example.com", company="Alpha")
    resolution = resolve_entities([auto_left, auto_right, review_left, review_right])
    result = build_canonical_entities(resolution, config=survivorship_config)

    assert "rev-1" in result.review_excluded_record_ids
    assert result.entity_for_record("rev-1") is None
    merged = [entity for entity in result.entities if len(entity.member_record_ids) > 1]
    for entity in merged:
        assert "rev-1" not in entity.member_record_ids


def test_review_queue_records_are_excluded_from_canonical_entities(survivorship_config):
    left = make_record("a-1", email="left@example.com", phone="+905321111111")
    right = make_record("a-2", email="right@example.com", phone="+905321111111")
    resolution = resolve_entities([left, right])
    result = build_canonical_entities(resolution, config=survivorship_config)

    assert "a-1" in result.review_excluded_record_ids or "a-2" in result.review_excluded_record_ids
    assert result.entity_for_record("a-1") is None or result.entity_for_record("a-2") is None


def test_forbidden_person_id_is_rejected(survivorship_config):
    bad = EntityRecord(
        record_id="bad-1",
        source_name="source_a",
        field_values={"person_id": "P-001", "email": "bad@example.com"},
    )
    resolution = ResolutionResult(
        source_label="inline",
        records=(bad,),
        candidates=(),
        decisions=(),
        review_queue=(),
        clusters=(),
        summary=ResolutionSummary(
            record_count=1,
            possible_pair_count=0,
            candidate_pair_count=0,
            candidate_reduction_ratio=0.0,
            auto_match_count=0,
            review_count=0,
            no_match_count=0,
            cluster_count=0,
            conflict_guarded_clusters=0,
        ),
    )
    with pytest.raises(ValueError, match="person_id"):
        build_canonical_entities(resolution, config=survivorship_config)
