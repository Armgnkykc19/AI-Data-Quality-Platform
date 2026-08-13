from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dataset.config import (
    load_corruptions_config,
    load_dataset_config,
    load_schema_config,
    validate_canonical_record,
)


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_load_schema_config_requires_fields(tmp_path: Path) -> None:
    path = tmp_path / "schema.yaml"
    path.write_text("version: '0.1.0'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fields"):
        load_schema_config(path)


def test_load_dataset_config_rejects_out_of_range_record_count(tmp_path: Path) -> None:
    config_path = tmp_path / "dataset.yaml"
    _write_yaml(
        config_path,
        {
            "version": "0.1.0",
            "seed": 1,
            "generation": {
                "record_count": 100,
                "min_record_count": 5000,
                "max_record_count": 20000,
            },
        },
    )

    with pytest.raises(ValueError, match="record_count"):
        load_dataset_config(config_path)


def test_load_corruptions_config_requires_profiles(tmp_path: Path) -> None:
    path = tmp_path / "corruptions.yaml"
    path.write_text("version: '0.1.0'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="profiles"):
        load_corruptions_config(path)


def test_validate_canonical_record_rejects_invalid_person_id() -> None:
    with pytest.raises(ValueError, match="person_id"):
        validate_canonical_record(
            {
                "person_id": "INVALID",
                "first_name": "A",
                "last_name": "B",
                "email": "a@example.test",
                "phone": "+905551234567",
                "company": "Test",
                "city": "Istanbul",
                "district": "Kadikoy",
                "address": "Street 1",
            }
        )
