from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.ci_dataset import (
    CiDatasetCompatibilityError,
    current_ci_config_fingerprint,
    require_ci_evaluation_dataset,
)


def test_missing_ci_dataset_fails_instead_of_passing(tmp_path: Path) -> None:
    missing = tmp_path / "datasets" / "generated" / "ci-smoke" / "v0.1.0"
    with pytest.raises(CiDatasetCompatibilityError, match="missing"):
        require_ci_evaluation_dataset(dataset_path=missing)


def test_stale_ci_dataset_without_fingerprint_fails(tmp_path: Path) -> None:
    dataset = tmp_path / "ci-smoke"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps({"version": "0.1.0", "seed": 42, "record_count": 200}),
        encoding="utf-8",
    )
    with pytest.raises(CiDatasetCompatibilityError, match="incompatible"):
        require_ci_evaluation_dataset(dataset_path=dataset)


def test_ci_dataset_with_current_fingerprint_is_accepted(tmp_path: Path) -> None:
    dataset = tmp_path / "ci-smoke"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps({"config_fingerprint": current_ci_config_fingerprint()}),
        encoding="utf-8",
    )
    assert require_ci_evaluation_dataset(dataset_path=dataset) == dataset
