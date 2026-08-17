from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.config import load_ingestion_config
from ingestion.models import ParsedDataset, ParsedRow, SourceMetadata
from profiling.profiler import profile_dataset
from schema_mapping.apply import apply_mapping_plan
from schema_mapping.config import SchemaMappingConfigError, load_schema_mapping_config
from schema_mapping.engine import build_mapping_plan
from schema_mapping.models import MappingDecisionType
from schema_mapping.preprocessing import normalize_header


def _parsed(headers: list[str], rows: list[list[str]]) -> ParsedDataset:
    parsed = ParsedDataset(
        metadata=SourceMetadata(path="test://inline", format="csv", size_bytes=0),
        headers=headers,
    )
    for index, raw_row in enumerate(rows, start=2):
        parsed.rows.append(
            ParsedRow(
                row_number=index,
                values={
                    headers[col]: raw_row[col] if col < len(raw_row) else None
                    for col in range(len(headers))
                },
            )
        )
    parsed.finalize_accounting()
    return parsed


@pytest.fixture
def mapping_config():
    return load_schema_mapping_config()


def test_normalize_header_turkish_and_spacing() -> None:
    assert normalize_header("E-Posta Adresi") == "e_posta_adresi"
    assert normalize_header("CEP TELEFONU") == "cep_telefonu"
    assert normalize_header("First_Name") == "first_name"


@pytest.fixture
def ingestion_config():
    return load_ingestion_config()


def test_exact_turkish_alias_auto_maps(mapping_config, ingestion_config) -> None:
    parsed = _parsed(["ad", "soyad"], [["Ali", "Kaya"]])
    plan = build_mapping_plan(parsed, profile=profile_dataset(parsed, ingestion_config))
    by_source = {item.source_column: item for item in plan.column_mappings}
    assert by_source["ad"].decision == MappingDecisionType.AUTO_MAP
    assert by_source["ad"].canonical_field == "first_name"


def test_unknown_column_unmapped(mapping_config, ingestion_config) -> None:
    parsed = _parsed(["notes"], [["internal memo"]])
    plan = build_mapping_plan(parsed, profile=profile_dataset(parsed, ingestion_config))
    assert plan.column_mappings[0].decision == MappingDecisionType.UNMAPPED


def test_ambiguous_contact_review_single_row(mapping_config, ingestion_config) -> None:
    parsed = _parsed(["contact"], [["ali@example.com"]])
    plan = build_mapping_plan(parsed, profile=profile_dataset(parsed, ingestion_config))
    mapping = plan.column_mappings[0]
    assert mapping.decision == MappingDecisionType.REVIEW
    assert mapping.canonical_field == "email"


def test_collision_marks_conflict(mapping_config, ingestion_config) -> None:
    parsed = _parsed(["email", "email_2"], [["a@example.com", "b@example.com"]])
    plan = build_mapping_plan(parsed, profile=profile_dataset(parsed, ingestion_config))
    by_source = {item.source_column: item for item in plan.column_mappings}
    assert by_source["email"].decision == MappingDecisionType.CONFLICT
    assert by_source["email_2"].decision == MappingDecisionType.CONFLICT


def test_apply_only_auto_map_fields(mapping_config, ingestion_config) -> None:
    parsed = _parsed(
        ["first_name", "contact"],
        [["Ali", "ali@example.com"]],
    )
    plan = build_mapping_plan(parsed, profile=profile_dataset(parsed, ingestion_config))
    applied = apply_mapping_plan(parsed, plan, config=mapping_config)
    assert "first_name" in applied.auto_map_fields_applied
    assert "contact" in applied.review_fields_skipped
    assert applied.records[0].unmapped_source_values.get("contact") == "ali@example.com"


def test_apply_preserves_record_count(mapping_config, ingestion_config) -> None:
    parsed = _parsed(["first_name", "notes"], [["Ali", "x"], ["Ayse", "y"]])
    plan = build_mapping_plan(parsed, profile=profile_dataset(parsed, ingestion_config))
    applied = apply_mapping_plan(parsed, plan, config=mapping_config)
    assert len(applied.records) == len(parsed.rows)


def test_invalid_threshold_order_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        "version: '0.1.0'\n"
        "canonical_schema_path: configs/canonical_schema.yaml\n"
        "mappable_fields: [first_name]\n"
        "non_auto_mappable_fields: [person_id]\n"
        "aliases:\n  first_name: [ad]\n"
        "thresholds:\n  auto_map: 0.5\n  review: 0.8\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaMappingConfigError):
        load_schema_mapping_config(config_path)


def test_deterministic_mapping_plan(mapping_config, ingestion_config) -> None:
    parsed = _parsed(["ad", "soyad"], [["Ali", "Kaya"], ["Veli", "Demir"]])
    profile = profile_dataset(parsed, ingestion_config)
    first = build_mapping_plan(parsed, profile=profile, config=mapping_config)
    second = build_mapping_plan(parsed, profile=profile, config=mapping_config)
    assert first.to_dict() == second.to_dict()
