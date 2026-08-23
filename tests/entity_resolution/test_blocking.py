from __future__ import annotations

from entity_resolution.blocking import generate_candidates, possible_pair_count
from entity_resolution.config import load_entity_resolution_config
from entity_resolution.engine import resolve_entities
from entity_resolution.models import EntityRecord


def test_candidate_pairs_are_deduplicated_and_ordered():
    config = load_entity_resolution_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={
                "first_name": "Ali",
                "last_name": "Kaya",
                "email": "shared@example.com",
                "phone": "+905321111111",
                "company": "Acme",
                "city": "Ankara",
                "district": "Cankaya",
                "address": "Street 1",
            },
        ),
        EntityRecord(
            record_id="a-2",
            source_name="source_a",
            field_values={
                "first_name": "Veli",
                "last_name": "Kaya",
                "email": "shared@example.com",
                "phone": "+905322222222",
                "company": "Acme",
                "city": "Ankara",
                "district": "Cankaya",
                "address": "Street 2",
            },
        ),
        EntityRecord(
            record_id="a-3",
            source_name="source_a",
            field_values={
                "first_name": "Ayşe",
                "last_name": "Demir",
                "email": "other@example.com",
                "phone": "+905323333333",
                "company": "Beta",
                "city": "İzmir",
                "district": "Konak",
                "address": "Street 3",
            },
        ),
    ]
    candidates = generate_candidates(records, config=config)
    pair_keys = [(item.pair.record_a_id, item.pair.record_b_id) for item in candidates]
    assert len(pair_keys) == len(set(pair_keys))
    assert pair_keys == sorted(pair_keys)
    assert possible_pair_count(len(records)) == 3


def test_blocking_uses_indexes_not_all_pairs(
    sample_record_a, sample_record_b, hard_negative_record
):
    config = load_entity_resolution_config()
    records = [sample_record_a, sample_record_b, hard_negative_record]
    result = resolve_entities(records, config=config)
    assert result.summary.candidate_pair_count < result.summary.possible_pair_count
    assert result.summary.candidate_reduction_ratio > 0.0


def test_same_email_generates_candidate_with_reason(sample_record_a, sample_record_b):
    config = load_entity_resolution_config()
    result = resolve_entities([sample_record_a, sample_record_b], config=config)
    assert len(result.candidates) == 1
    assert result.candidates[0].reasons[0].reason_type.value == "EMAIL_EXACT_BLOCK"
