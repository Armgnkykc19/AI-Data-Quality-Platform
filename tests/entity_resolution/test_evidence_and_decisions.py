from __future__ import annotations

from entity_resolution.candidates import compare_candidate_pair
from entity_resolution.clustering import inspect_pair
from entity_resolution.config import load_entity_resolution_config
from entity_resolution.decisions import decide_pair_match
from entity_resolution.evidence import collect_pair_conflicts, collect_pair_evidence
from entity_resolution.models import (
    EntityRecord,
    MatchCandidate,
    MatchDecisionType,
    PairComparison,
    RecordPair,
)
from entity_resolution.scoring import build_pair_comparison


def test_email_exact_evidence_requires_non_empty_values():
    config = load_entity_resolution_config()
    left = EntityRecord("a", "source_a", {"email": "", "phone": None})
    right = EntityRecord("b", "source_a", {"email": "", "phone": None})
    evidence = collect_pair_evidence(left, right, config=config)
    assert evidence == ()


def test_null_values_do_not_create_positive_evidence():
    config = load_entity_resolution_config()
    left = EntityRecord("a", "source_a", {"first_name": None, "last_name": None})
    right = EntityRecord("b", "source_a", {"first_name": None, "last_name": None})
    evidence = collect_pair_evidence(left, right, config=config)
    assert evidence == ()


def test_email_conflict_detected(sample_record_a, hard_negative_record):
    config = load_entity_resolution_config()
    conflicts = collect_pair_conflicts(sample_record_a, hard_negative_record, config=config)
    assert any(item.conflict_type.value == "EMAIL_CONFLICT" for item in conflicts)


def test_strong_email_match_can_auto_match(sample_record_a, sample_record_b):
    config = load_entity_resolution_config()
    decision = inspect_pair(sample_record_a, sample_record_b, config=config)
    assert decision.decision == MatchDecisionType.AUTO_MATCH


def test_same_name_city_hard_negative_is_not_auto_match(sample_record_a, hard_negative_record):
    config = load_entity_resolution_config()
    candidate = MatchCandidate(
        pair=RecordPair.ordered(sample_record_a.record_id, hard_negative_record.record_id),
        reasons=(),
    )
    comparison = compare_candidate_pair(
        candidate,
        {
            sample_record_a.record_id: sample_record_a,
            hard_negative_record.record_id: hard_negative_record,
        },
        config=config,
    )
    decision = decide_pair_match(comparison, config=config)
    assert decision.decision in {MatchDecisionType.REVIEW, MatchDecisionType.NO_MATCH}


def test_score_is_bounded():
    config = load_entity_resolution_config()
    left = EntityRecord(
        "a",
        "source_a",
        {
            "first_name": "Ali",
            "last_name": "Kaya",
            "email": "ali@example.com",
            "phone": "+905321234567",
            "company": "Acme",
            "city": "Ankara",
            "district": "Cankaya",
            "address": "Street",
        },
    )
    right = EntityRecord(
        "b",
        "source_a",
        {
            "first_name": "Ali",
            "last_name": "Kaya",
            "email": "veli@example.com",
            "phone": "+905559998877",
            "company": "Beta",
            "city": "İzmir",
            "district": "Konak",
            "address": "Other",
        },
    )
    evidence = collect_pair_evidence(left, right, config=config)
    conflicts = collect_pair_conflicts(left, right, config=config)
    comparison = build_pair_comparison(
        comparison=PairComparison(
            pair=RecordPair.ordered("a", "b"),
            candidate_reasons=(),
            evidence=evidence,
            conflicts=conflicts,
            score=0.0,
        ),
        config=config,
    )
    assert 0.0 <= comparison.score <= 1.0
