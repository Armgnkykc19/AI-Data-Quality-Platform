from __future__ import annotations

import pytest

from entity_resolution.models import EntityRecord


@pytest.fixture
def sample_record_a() -> EntityRecord:
    return EntityRecord(
        record_id="source_a-000001",
        source_name="source_a",
        field_values={
            "first_name": "Ayşe",
            "last_name": "Yılmaz",
            "email": "ayse@example.com",
            "phone": "+905321234567",
            "company": "Acme A.Ş.",
            "city": "İstanbul",
            "district": "Kadıköy",
            "address": "Bağdat Caddesi No:1",
        },
    )


@pytest.fixture
def sample_record_b() -> EntityRecord:
    return EntityRecord(
        record_id="source_a-000002",
        source_name="source_a",
        field_values={
            "first_name": "Ayse",
            "last_name": "Yilmaz",
            "email": "ayse@example.com",
            "phone": "+905321234567",
            "company": "Acme AS",
            "city": "Istanbul",
            "district": "Kadikoy",
            "address": "Bagdat Caddesi No:1",
        },
    )


@pytest.fixture
def hard_negative_record() -> EntityRecord:
    return EntityRecord(
        record_id="hard_negative-000002",
        source_name="hard_negative",
        field_values={
            "first_name": "Mehmet",
            "last_name": "Yılmaz",
            "email": "mehmet@example.com",
            "phone": "+905559998877",
            "company": "Beta Ltd.",
            "city": "İstanbul",
            "district": "Kadıköy",
            "address": "Başka Sokak No:2",
        },
    )
