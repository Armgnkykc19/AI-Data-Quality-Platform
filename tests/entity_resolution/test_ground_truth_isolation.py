from __future__ import annotations

from entity_resolution.config import load_entity_resolution_config
from entity_resolution.engine import resolve_entities
from entity_resolution.models import EntityRecord


def test_person_id_in_input_is_rejected():
    config = load_entity_resolution_config()
    record = EntityRecord(
        record_id="a-1",
        source_name="source_a",
        field_values={
            "person_id": "P-000001",
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
    try:
        resolve_entities([record], config=config)
    except ValueError as exc:
        assert "person_id" in str(exc)
    else:
        raise AssertionError("Expected ValueError for forbidden person_id field")


def test_engine_never_reads_ground_truth_module():
    config = load_entity_resolution_config()
    record = EntityRecord(
        record_id="a-1",
        source_name="source_a",
        field_values={
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
    result = resolve_entities([record], config=config)
    assert result.summary.record_count == 1
    assert result.decisions == ()


def test_source_records_are_not_mutated(sample_record_a):
    original = dict(sample_record_a.field_values)
    config = load_entity_resolution_config()
    resolve_entities([sample_record_a], config=config)
    assert sample_record_a.field_values == original
