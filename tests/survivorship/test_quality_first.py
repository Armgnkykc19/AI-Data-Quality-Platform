from __future__ import annotations

from entity_resolution.engine import resolve_entities
from entity_resolution.models import EntityRecord
from survivorship.config import load_survivorship_config
from survivorship.engine import build_canonical_entities
from survivorship.rules import apply_field_survivorship
from tests.survivorship.conftest import make_record


def test_valid_shorter_city_beats_corrupted_longer_city():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"city": "Bursa (Merged)", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"city": "Bursa", "email": "x@example.com"},
        ),
    ]
    values, provenance, conflicts = apply_field_survivorship(records, config=config)
    assert values["city"] == "Bursa"
    city_prov = next(item for item in provenance if item.field_name == "city")
    assert city_prov.source_record_id == "b-1"
    assert any(item.field_name == "city" for item in conflicts)


def test_valid_shorter_company_beats_hyphenated_corruption():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={
                "company": "Boğaziçi-Sağlık-A.Ş.",
                "email": "x@example.com",
            },
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={
                "company": "Boğaziçi Sağlık A.Ş.",
                "email": "x@example.com",
            },
        ),
    ]
    values, _, _ = apply_field_survivorship(records, config=config)
    assert values["company"] == "Boğaziçi Sağlık A.Ş."


def test_valid_shorter_address_beats_hyphenated_corruption():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={
                "address": "Gazi-Cad.-No:111,-Çankaya/Bursa",
                "email": "x@example.com",
            },
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={
                "address": "Gazi Cad. No:111, Çankaya/Bursa",
                "email": "x@example.com",
            },
        ),
    ]
    values, _, _ = apply_field_survivorship(records, config=config)
    assert "Gazi Cad." in (values["address"] or "")


def test_invalid_source_a_phone_loses_to_valid_source_b():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"phone": "abc", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"phone": "+905321234567", "email": "x@example.com"},
        ),
    ]
    values, prov, _ = apply_field_survivorship(records, config=config)
    assert values["phone"] == "+905321234567"
    phone_prov = next(item for item in prov if item.field_name == "phone")
    assert phone_prov.source_record_id == "b-1"


def test_blank_source_a_loses_to_present_source_b():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"company": "", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"company": "Acme Corp", "email": "x@example.com"},
        ),
    ]
    values, _, _ = apply_field_survivorship(records, config=config)
    assert values["company"] == "Acme Corp"


def test_source_priority_breaks_equal_quality_tie():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"company": "Beta Co", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"company": "Alpha Co", "email": "x@example.com"},
        ),
    ]
    values, prov, conflicts = apply_field_survivorship(records, config=config)
    assert values["company"] == "Alpha Co"
    company_prov = next(item for item in prov if item.field_name == "company")
    assert company_prov.source_record_id == "a-1"
    assert any(item.field_name == "company" for item in conflicts)


def test_conflicting_valid_emails_preserve_conflict():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"email": "alice@example.com", "phone": "+905321111111"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"email": "bob@example.com", "phone": "+905321111111"},
        ),
    ]
    values, prov, conflicts = apply_field_survivorship(records, config=config)
    assert values["email"] == "alice@example.com"
    assert any(item.field_name == "email" for item in conflicts)
    email_prov = next(item for item in prov if item.field_name == "email")
    assert email_prov.source_value == values["email"]


def test_provenance_points_to_actual_member_value():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"city": "Bursa (Merged)", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"city": "Bursa", "email": "x@example.com"},
        ),
    ]
    _, provenance, _ = apply_field_survivorship(records, config=config)
    city_prov = next(item for item in provenance if item.field_name == "city")
    member = next(record for record in records if record.record_id == city_prov.source_record_id)
    assert city_prov.source_value == member.get("city")
    assert city_prov.rule == "quality_first"


def test_review_cluster_exclusion_regression(survivorship_config):
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


def test_source_diversity_beats_source_priority_for_address():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={
                "address": "Moda Cad, No:115, Çankaya/Bursa",
                "email": "x@example.com",
            },
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={
                "address": "Moda Cad. No:115, Çankaya/Bursa",
                "email": "x@example.com",
            },
        ),
        EntityRecord(
            record_id="c-1",
            source_name="source_c",
            field_values={
                "address": "Moda Cad. No:115, Çankaya/Bursa",
                "email": "x@example.com",
            },
        ),
    ]
    values, _, _ = apply_field_survivorship(records, config=config)
    assert values["address"] == "Moda Cad. No:115, Çankaya/Bursa"


def test_source_diversity_does_not_override_conflicting_valid_emails():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"email": "alice@example.com", "phone": "+905321111111"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"email": "bob@example.com", "phone": "+905321111111"},
        ),
        EntityRecord(
            record_id="c-1",
            source_name="source_c",
            field_values={"email": "bob@example.com", "phone": "+905321111111"},
        ),
    ]
    values, _, conflicts = apply_field_survivorship(records, config=config)
    assert values["email"] == "alice@example.com"
    assert any(item.field_name == "email" for item in conflicts)


def test_two_corrupted_sources_do_not_beat_one_clean_candidate():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"company": "Acme A,Ş,", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"company": "Acme A,Ş,", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="c-1",
            source_name="source_c",
            field_values={"company": "Acme A.Ş.", "email": "x@example.com"},
        ),
    ]
    values, _, _ = apply_field_survivorship(records, config=config)
    assert values["company"] == "Acme A.Ş."


def test_name_with_legal_suffix_contamination_penalized():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"first_name": "Emre", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"first_name": "Emre", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="c-1",
            source_name="source_c",
            field_values={"first_name": "Emre Ltd.", "email": "x@example.com"},
        ),
    ]
    values, _, _ = apply_field_survivorship(records, config=config)
    assert values["first_name"] == "Emre"


def test_company_comma_suffix_corruption_penalized():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"company": "Zenith Finans A,Ş,", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"company": "Zenith Finans A.Ş.", "email": "x@example.com"},
        ),
    ]
    values, _, _ = apply_field_survivorship(records, config=config)
    assert values["company"] == "Zenith Finans A.Ş."


def test_address_trailing_ltd_suffix_penalized():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={
                "address": "Fatih Cad. No:48, Nilüfer/Mersin",
                "email": "x@example.com",
            },
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={
                "address": "Fatih Cad. No:48, Nilüfer/Mersin",
                "email": "x@example.com",
            },
        ),
        EntityRecord(
            record_id="c-1",
            source_name="source_c",
            field_values={
                "address": "Fatih Cad. No:48, Nilüfer/Mersin Ltd.",
                "email": "x@example.com",
            },
        ),
    ]
    values, _, _ = apply_field_survivorship(records, config=config)
    assert values["address"] == "Fatih Cad. No:48, Nilüfer/Mersin"


def test_three_valid_company_values_use_deterministic_tie_break():
    config = load_survivorship_config()
    records = [
        EntityRecord(
            record_id="a-1",
            source_name="source_a",
            field_values={"company": "Alpha Co", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="b-1",
            source_name="source_b",
            field_values={"company": "Beta Co", "email": "x@example.com"},
        ),
        EntityRecord(
            record_id="c-1",
            source_name="source_c",
            field_values={"company": "Gamma Co", "email": "x@example.com"},
        ),
    ]
    values, _, conflicts = apply_field_survivorship(records, config=config)
    assert values["company"] == "Alpha Co"
    assert any(item.field_name == "company" for item in conflicts)
