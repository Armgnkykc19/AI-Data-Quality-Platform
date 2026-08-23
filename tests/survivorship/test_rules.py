from __future__ import annotations

from entity_resolution.models import EntityRecord
from survivorship.config import load_survivorship_config
from survivorship.rules import apply_field_survivorship


def test_source_priority_selects_higher_priority_record():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="c-1",
            source_name="source_c",
            field_values={"company": "Gamma"},
        ),
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"company": "Alpha"},
        ),
    ]
    field_values, provenance, conflicts = apply_field_survivorship(records, config=config)
    assert field_values["company"] == "Alpha"
    company_prov = next(item for item in provenance if item.field_name == "company")
    assert company_prov.source_record_id == "a-1"
    assert any(item.field_name == "company" for item in conflicts)


def test_identity_consensus_breaks_tie_by_source_priority():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"email": " ALI@Example.com "},
        ),
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"email": "ali@example.com"},
        ),
    ]
    field_values, provenance, conflicts = apply_field_survivorship(records, config=config)
    assert field_values["email"] in {"ali@example.com", " ALI@Example.com "}
    email_prov = next(item for item in provenance if item.field_name == "email")
    assert email_prov.source_record_id in {"a-1", "b-1"}
    assert not conflicts


def test_conflict_is_detected_for_distinct_normalized_values():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"company": "Acme Corp"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"company": "Acme Incorporated"},
        ),
    ]
    _, _, conflicts = apply_field_survivorship(records, config=config)
    assert any(item.field_name == "company" for item in conflicts)
