from __future__ import annotations

from pathlib import Path

from dataset.build import build_golden_dataset
from dataset.validation import validate_dataset
from tests.dataset.test_build import _make_test_dataset_config


def test_validation_checks_hard_positive_pair_invariants(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=11)
    result = build_golden_dataset(dataset_config=config)
    validation = validate_dataset(result.output_base)
    assert validation.passed, [issue.message for issue in validation.issues]


def test_validation_detects_invalid_hard_positive_metadata(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=12)
    result = build_golden_dataset(dataset_config=config)
    summary_path = result.output_base / "ground_truth" / "summary.json"
    import json

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["positive_pairs"][0]["person_id_b"] = "P-999999"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    validation = validate_dataset(result.output_base)
    assert not validation.passed
    assert any(issue.code == "invalid_hard_positive" for issue in validation.issues)


def test_validation_detects_missing_hard_case_csv_record(tmp_path: Path) -> None:
    config = _make_test_dataset_config(tmp_path, seed=13)
    result = build_golden_dataset(dataset_config=config)
    hard_positive_path = result.output_base / "hard_cases" / "hard_positives.csv"
    lines = hard_positive_path.read_text(encoding="utf-8").splitlines()
    hard_positive_path.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")
    validation = validate_dataset(result.output_base)
    assert not validation.passed
    assert any(
        issue.code in {"missing_hard_positive_record", "hash_mismatch"}
        for issue in validation.issues
    )
