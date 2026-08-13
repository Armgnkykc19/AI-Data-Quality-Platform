from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dataset.build import build_golden_dataset
from dataset.config import DatasetConfig, load_corruptions_config
from dataset.manifest import compute_file_sha256
from dataset.validation import validate_dataset


def _make_test_dataset_config(tmp_path: Path, seed: int = 42) -> DatasetConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    output_base = tmp_path / "golden" / "v0.1.0"
    dataset_yaml = tmp_path / "dataset.yaml"
    dataset_yaml.write_text(
        yaml.safe_dump(
            {
                "version": "0.1.0",
                "seed": seed,
                "generation": {
                    "record_count": 200,
                    "min_record_count": 100,
                    "max_record_count": 500,
                },
                "output": {"base_directory": str(output_base).replace("\\", "/")},
                "schema": {"config_path": "configs/canonical_schema.yaml"},
                "splits": {
                    "train_ratio": 0.70,
                    "validation_ratio": 0.15,
                    "test_ratio": 0.15,
                    "holdout_seed": 99,
                },
                "hard_cases": {
                    "hard_positives_count": 10,
                    "hard_negatives_count": 10,
                    "hard_negative_similarity_seed": 7,
                },
                "malformed": {"enabled": True, "output_subdirectory": "malformed"},
            }
        ),
        encoding="utf-8",
    )

    from dataset.config import load_dataset_config

    return load_dataset_config(dataset_yaml)


def test_build_golden_dataset_is_reproducible(tmp_path: Path) -> None:
    config_a = _make_test_dataset_config(tmp_path / "a", seed=11)
    config_b = _make_test_dataset_config(tmp_path / "b", seed=11)

    result_a = build_golden_dataset(dataset_config=config_a)
    manifest_a = (result_a.output_base / "manifest.json").read_text(encoding="utf-8")

    result_b = build_golden_dataset(dataset_config=config_b)
    manifest_b = (result_b.output_base / "manifest.json").read_text(encoding="utf-8")

    canonical_a = result_a.output_base / "clean" / "canonical.csv"
    canonical_b = result_b.output_base / "clean" / "canonical.csv"

    assert compute_file_sha256(canonical_a) == compute_file_sha256(canonical_b)

    import json

    manifest_a_data = json.loads(manifest_a)
    manifest_b_data = json.loads(manifest_b)

    for key in ("version", "seed", "record_count", "expected_counts", "corruption_counts"):
        assert manifest_a_data[key] == manifest_b_data[key]

    for name in manifest_a_data["files"]:
        assert (
            manifest_a_data["files"][name]["sha256"]
            == manifest_b_data["files"][name]["sha256"]
        )


def test_different_seed_changes_dataset_content(tmp_path: Path) -> None:
    config_a = _make_test_dataset_config(tmp_path / "a", seed=11)
    config_b = _make_test_dataset_config(tmp_path / "b", seed=12)

    result_a = build_golden_dataset(dataset_config=config_a)
    result_b = build_golden_dataset(dataset_config=config_b)

    canonical_a = result_a.output_base / "clean" / "canonical.csv"
    canonical_b = result_b.output_base / "clean" / "canonical.csv"

    assert compute_file_sha256(canonical_a) != compute_file_sha256(canonical_b)


def test_build_and_validate_dataset_passes(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=5)
    result = build_golden_dataset(dataset_config=config)

    validation = validate_dataset(result.output_base)
    assert validation.passed, [issue.message for issue in validation.issues]


def test_ground_truth_hard_negatives_are_different_people(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=3)
    result = build_golden_dataset(dataset_config=config)

    for pair in result.ground_truth.hard_negative_pairs:
        assert pair.person_id_a != pair.person_id_b


def test_corruption_counts_match_manifest(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=8)
    result = build_golden_dataset(dataset_config=config)

    import json

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["corruption_counts"] == result.corruption_counts


def test_build_failure_does_not_leave_partial_output(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=4)
    broken = DatasetConfig(
        version=config.version,
        seed=config.seed,
        record_count=config.record_count,
        min_record_count=config.min_record_count,
        max_record_count=config.max_record_count,
        output_base=config.output_base,
        schema_path=tmp_path / "missing_schema.yaml",
        splits=config.splits,
        sources=config.sources,
        hard_cases=config.hard_cases,
        malformed=config.malformed,
        raw=config.raw,
    )

    from dataset.build import DatasetBuildError

    with pytest.raises(DatasetBuildError):
        build_golden_dataset(dataset_config=broken)

    assert not config.output_base.exists()


def test_load_corruptions_config_from_repo() -> None:
    config = load_corruptions_config()
    assert "formatting_noise" in config.profiles
