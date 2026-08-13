from __future__ import annotations

from pathlib import Path

import yaml

from dataset.build import build_golden_dataset
from dataset.generator.malformed import MALFORMED_FIXTURES
from dataset.validation import validate_dataset
from tests.dataset.test_build import _make_test_dataset_config


def test_malformed_fixtures_have_expected_categories(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=21)
    build_golden_dataset(dataset_config=config)

    malformed_dir = config.output_base / "malformed"
    for filename, spec in MALFORMED_FIXTURES.items():
        path = malformed_dir / filename
        assert path.exists()
        assert str(spec["category"]) in (malformed_dir / "README.md").read_text(encoding="utf-8")


def test_invalid_schema_config_fails_validation(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=22)
    build_golden_dataset(dataset_config=config)

    schema_path = config.output_base / "schema" / "canonical_schema.json"
    schema_path.write_text(yaml.safe_dump({"version": "broken"}), encoding="utf-8")

    result = validate_dataset(config.output_base)
    assert not result.passed
    assert any(issue.code == "invalid_schema" for issue in result.issues)


def test_hash_mismatch_fails_validation(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=23)
    build_golden_dataset(dataset_config=config)

    canonical_path = config.output_base / "clean" / "canonical.csv"
    canonical_path.write_text("corrupted\n", encoding="utf-8")

    result = validate_dataset(config.output_base)
    assert not result.passed
    assert any(issue.code == "hash_mismatch" for issue in result.issues)
