from __future__ import annotations

from entity_resolution.config import load_entity_resolution_config
from entity_resolution.engine import resolve_entities


def test_repeated_runs_are_identical(sample_record_a, sample_record_b, hard_negative_record):
    config = load_entity_resolution_config()
    records = [sample_record_a, sample_record_b, hard_negative_record]
    first = resolve_entities(records, config=config)
    second = resolve_entities(records, config=config)

    first_pairs = [
        (
            decision.pair.record_a_id,
            decision.pair.record_b_id,
            decision.decision.value,
            round(decision.comparison.score, 6),
        )
        for decision in first.decisions
    ]
    second_pairs = [
        (
            decision.pair.record_a_id,
            decision.pair.record_b_id,
            decision.decision.value,
            round(decision.comparison.score, 6),
        )
        for decision in second.decisions
    ]
    assert first_pairs == second_pairs
    assert [item.to_dict() for item in first.review_queue] == [
        item.to_dict() for item in second.review_queue
    ]
