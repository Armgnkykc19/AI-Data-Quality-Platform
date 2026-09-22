"""Locate the CI evaluation dataset and refuse a missing or stale copy.

The product-gate tests consume ``datasets/generated/ci-smoke/v0.1.0``. That
path is gitignored, so CI must generate it before pytest, and a leftover local
artifact from an older config must not silently satisfy ``candidate_recall``.
This module does not generate anything and does not touch ``final_holdout``.
"""

from __future__ import annotations

import json
from pathlib import Path

from dataset.config import CANONICAL_FIELDS, load_dataset_config
from dataset.manifest import generation_fingerprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CI_DATASET = PROJECT_ROOT / "datasets" / "generated" / "ci-smoke" / "v0.1.0"
CI_DATASET_CONFIG = PROJECT_ROOT / "configs" / "dataset.ci.yaml"

REGENERATE_HINT = (
    "Regenerate the CI evaluation dataset with: "
    "python scripts/build_golden_dataset.py --config configs/dataset.ci.yaml"
)


class CiDatasetCompatibilityError(RuntimeError):
    """The CI dataset is missing or was built from a different generator config."""


def current_ci_config_fingerprint() -> str:
    config = load_dataset_config(CI_DATASET_CONFIG)
    return generation_fingerprint(
        version=config.version,
        seed=config.seed,
        record_count=config.record_count,
        splits=config.splits,
        sources=config.sources,
        hard_cases=config.hard_cases,
        canonical_fields=list(CANONICAL_FIELDS),
    )


def require_ci_evaluation_dataset(*, dataset_path: Path = CI_DATASET) -> Path:
    """Return the CI dataset path, or raise with an explicit regenerate instruction."""
    if not dataset_path.exists():
        raise CiDatasetCompatibilityError(
            "CI evaluation dataset is missing at "
            f"{dataset_path}. Product gates cannot run. {REGENERATE_HINT}"
        )

    manifest_path = dataset_path / "manifest.json"
    if not manifest_path.exists():
        raise CiDatasetCompatibilityError(
            f"CI evaluation dataset at {dataset_path} has no manifest.json. {REGENERATE_HINT}"
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CiDatasetCompatibilityError(
            f"CI evaluation dataset manifest is not valid JSON. {REGENERATE_HINT}"
        ) from exc

    stored = manifest.get("config_fingerprint")
    expected = current_ci_config_fingerprint()
    if stored != expected:
        raise CiDatasetCompatibilityError(
            "CI evaluation dataset is incompatible with the current "
            f"{CI_DATASET_CONFIG.name} (stored fingerprint {stored!r}, "
            f"required {expected!r}). {REGENERATE_HINT}"
        )
    return dataset_path
