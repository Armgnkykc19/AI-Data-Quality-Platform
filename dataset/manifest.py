from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class CorruptionRecord:
    corruption_type: str
    field_name: str
    before_value: str | None
    after_value: str | None
    severity: str
    person_id: str
    source_record_id: str
    source_name: str


@dataclass
class SourceRecord:
    person_id: str
    source_record_id: str
    source_name: str
    fields: dict[str, str | None]
    corruptions: list[CorruptionRecord] = field(default_factory=list)


@dataclass
class DuplicateGroup:
    person_id: str
    source_record_ids: list[str]
    source_name: str


@dataclass
class MatchPair:
    person_id_a: str
    person_id_b: str
    source_record_id_a: str
    source_record_id_b: str
    source_name: str
    pair_type: str


@dataclass
class GroundTruth:
    person_mappings: dict[str, str]
    duplicate_groups: list[DuplicateGroup]
    positive_pairs: list[MatchPair]
    hard_negative_pairs: list[MatchPair]
    corruption_history: list[CorruptionRecord]
    expected_counts: dict[str, int]
    splits: dict[str, list[str]]


def compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False))
            file.write("\n")


def build_manifest(
    *,
    version: str,
    seed: int,
    record_count: int,
    output_base: Path,
    file_paths: dict[str, Path],
    corruption_counts: dict[str, int],
    expected_counts: dict[str, int],
    generation_config: dict[str, Any],
) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for name, path in sorted(file_paths.items()):
        if path.exists() and path.is_file():
            files[name] = {
                "path": str(path.relative_to(output_base)),
                "sha256": compute_file_sha256(path),
                "size_bytes": path.stat().st_size,
            }

    return {
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": seed,
        "record_count": record_count,
        "expected_counts": expected_counts,
        "corruption_counts": corruption_counts,
        "files": files,
        "generation_config": generation_config,
        "reproducibility": {
            "command": "python scripts/build_golden_dataset.py --config configs/dataset.yaml",
            "note": "Same seed and config produce identical content hashes.",
        },
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    write_json(path, manifest)


def ground_truth_to_dict(ground_truth: GroundTruth) -> dict[str, Any]:
    return {
        "person_mappings": ground_truth.person_mappings,
        "duplicate_groups": [asdict(group) for group in ground_truth.duplicate_groups],
        "positive_pairs": [asdict(pair) for pair in ground_truth.positive_pairs],
        "hard_negative_pairs": [asdict(pair) for pair in ground_truth.hard_negative_pairs],
        "expected_counts": ground_truth.expected_counts,
        "splits": ground_truth.splits,
    }


def corruption_to_dict(record: CorruptionRecord) -> dict[str, Any]:
    return asdict(record)
