from __future__ import annotations

from schema_mapping.conflicts import decide_column_mapping
from schema_mapping.models import (
    EvidenceType,
    MappingCandidate,
    MappingConflict,
    MappingDecisionType,
    MappingEvidence,
)


def _pattern_email_evidence(value: float = 0.98) -> tuple[MappingEvidence, ...]:
    return (
        MappingEvidence(
            evidence_type=EvidenceType.PATTERN_EMAIL,
            value=value,
            weight=0.40,
            contribution=0.40,
            description="test pattern",
            source="test",
        ),
    )


def test_strong_pattern_does_not_bypass_ambiguity_margin(mapping_config) -> None:
    candidates = [
        MappingCandidate("email", 0.96, _pattern_email_evidence()),
        MappingCandidate("phone", 0.93, ()),
    ]
    decision, field, _, _, reason = decide_column_mapping(
        header="contact",
        candidates=candidates,
        config=mapping_config,
        collision_conflicts=(),
    )
    assert decision == MappingDecisionType.REVIEW
    assert field == "email"
    assert "ambiguity margin" in reason


def test_strong_pattern_allows_auto_map_when_margin_clear(mapping_config) -> None:
    candidates = [
        MappingCandidate("email", 0.96, _pattern_email_evidence()),
        MappingCandidate("phone", 0.70, ()),
    ]
    decision, field, _, _, _ = decide_column_mapping(
        header="contact",
        candidates=candidates,
        config=mapping_config,
        collision_conflicts=(),
    )
    assert decision == MappingDecisionType.AUTO_MAP
    assert field == "email"


def test_exact_tie_routes_to_review(mapping_config) -> None:
    shared_evidence = (
        MappingEvidence(
            evidence_type=EvidenceType.LEXICAL_SIMILARITY,
            value=0.80,
            weight=0.35,
            contribution=0.28,
            description="test",
            source="test",
        ),
    )
    candidates = [
        MappingCandidate("email", 0.92, shared_evidence),
        MappingCandidate("phone", 0.92, shared_evidence),
    ]
    decision, _, _, _, _ = decide_column_mapping(
        header="customer_email",
        candidates=candidates,
        config=mapping_config,
        collision_conflicts=(),
    )
    assert decision == MappingDecisionType.REVIEW


def test_pattern_does_not_override_collision(mapping_config) -> None:
    candidates = [
        MappingCandidate("email", 0.96, _pattern_email_evidence()),
        MappingCandidate("phone", 0.70, ()),
    ]
    collision = (
        MappingConflict(
            conflict_type="ONE_TO_ONE_COLLISION",
            message="duplicate email targets",
            related_columns=("email", "email_2"),
        ),
    )
    decision, field, _, _, reason = decide_column_mapping(
        header="email",
        candidates=candidates,
        config=mapping_config,
        collision_conflicts=collision,
    )
    assert decision == MappingDecisionType.CONFLICT
    assert field == "email"
    assert "duplicate email targets" in reason


def test_default_apply_never_applies_review_mappings(
    mapping_config,
    ingestion_config,
) -> None:
    from ingestion.models import ParsedDataset, ParsedRow, SourceMetadata
    from profiling.profiler import profile_dataset
    from schema_mapping.apply import apply_mapping_plan
    from schema_mapping.engine import build_mapping_plan

    parsed = ParsedDataset(
        metadata=SourceMetadata(path="test://inline", format="csv", size_bytes=0),
        headers=["first_name", "contact"],
    )
    parsed.rows.append(
        ParsedRow(
            row_number=2,
            values={"first_name": "Ali", "contact": "ali@example.com"},
        )
    )
    parsed.finalize_accounting()

    plan = build_mapping_plan(parsed, profile=profile_dataset(parsed, ingestion_config))
    contact_mapping = next(
        item for item in plan.column_mappings if item.source_column == "contact"
    )
    assert contact_mapping.decision == MappingDecisionType.REVIEW

    applied = apply_mapping_plan(parsed, plan, config=mapping_config)
    assert "email" not in applied.auto_map_fields_applied
    assert contact_mapping.source_column in applied.review_fields_skipped
    assert applied.records[0].canonical_values.get("email") is None
    assert applied.records[0].unmapped_source_values.get("contact") == "ali@example.com"
