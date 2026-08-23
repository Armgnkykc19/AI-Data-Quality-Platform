from __future__ import annotations

import pytest

from entity_resolution.models import EntityRecord
from survivorship.config import load_survivorship_config


@pytest.fixture
def survivorship_config():
    return load_survivorship_config()


def make_record(
    record_id: str,
    *,
    source_name: str = "source_a",
    **fields: str,
) -> EntityRecord:
    base = {
        "first_name": "Ali",
        "last_name": "Kaya",
        "email": f"{record_id}@example.com",
        "phone": "+905321234567",
        "company": "Acme",
        "city": "Ankara",
        "district": "Cankaya",
        "address": "Street 1",
    }
    base.update(fields)
    return EntityRecord(record_id=record_id, source_name=source_name, field_values=base)
