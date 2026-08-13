from __future__ import annotations

from dataset.config import validate_canonical_record
from dataset.generator.clean_base import generate_clean_base


def test_same_seed_produces_identical_clean_base() -> None:
    first = generate_clean_base(seed=42, record_count=100)
    second = generate_clean_base(seed=42, record_count=100)
    assert first == second


def test_different_seed_produces_different_clean_base() -> None:
    first = generate_clean_base(seed=42, record_count=100)
    second = generate_clean_base(seed=43, record_count=100)
    assert first != second


def test_clean_base_has_unique_person_email_phone() -> None:
    records = generate_clean_base(seed=7, record_count=500)
    person_ids = [record["person_id"] for record in records]
    emails = [record["email"] for record in records]
    phones = [record["phone"] for record in records]

    assert len(person_ids) == len(set(person_ids))
    assert len(emails) == len(set(emails))
    assert len(phones) == len(set(phones))

    for record in records:
        validate_canonical_record(record)
        assert record["person_id"].startswith("P-")
