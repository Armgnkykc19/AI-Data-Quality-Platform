from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dataset.config import (
    CANONICAL_FIELDS,
    DatasetConfig,
    load_corruptions_config,
    load_dataset_config,
    load_schema_config,
)
from dataset.generator.clean_base import generate_clean_base
from dataset.generator.hard_cases import (
    HARD_NEGATIVE_COLUMNS,
    HARD_POSITIVE_COLUMNS,
    generate_hard_negatives,
    generate_hard_positives,
)
from dataset.generator.malformed import generate_malformed_fixtures
from dataset.generator.sources import (
    SOURCE_A_COLUMNS,
    generate_source_a,
    generate_source_b,
    generate_source_c,
    write_canonical_csv,
    write_source_csv,
)
from dataset.manifest import (
    GroundTruth,
    build_manifest,
    corruption_to_dict,
    ground_truth_to_dict,
    write_json,
    write_jsonl,
    write_manifest,
)
from dataset.splits import build_split_metadata


@dataclass
class BuildResult:
    output_base: Path
    manifest_path: Path
    ground_truth: GroundTruth
    corruption_counts: dict[str, int]


class DatasetBuildError(Exception):
    """Raised when dataset generation fails or leaves inconsistent artifacts."""


def _count_corruptions(records: list[Any]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        counter[record.corruption_type] += 1
    return dict(counter)


def _atomic_output_base(output_base: Path) -> Path:
    temp_base = output_base.with_name(output_base.name + ".tmp")
    if temp_base.exists():
        shutil.rmtree(temp_base)
    temp_base.mkdir(parents=True, exist_ok=True)
    return temp_base


def _finalize_output(temp_base: Path, output_base: Path) -> None:
    if output_base.exists():
        shutil.rmtree(output_base)
    temp_base.rename(output_base)


def build_golden_dataset(
    *,
    dataset_config: DatasetConfig,
    corruptions_config_path: Path | None = None,
) -> BuildResult:
    from dataset.config import DEFAULT_CORRUPTIONS_CONFIG

    corruptions_path = corruptions_config_path or DEFAULT_CORRUPTIONS_CONFIG
    temp_base = _atomic_output_base(dataset_config.output_base)

    try:
        corruptions_config = load_corruptions_config(corruptions_path)
        schema_config = load_schema_config(dataset_config.schema_path)
        clean_records = generate_clean_base(
            seed=dataset_config.seed,
            record_count=dataset_config.record_count,
        )

        clean_dir = temp_base / "clean"
        clean_path = clean_dir / "canonical.csv"
        write_canonical_csv(clean_path, clean_records)

        all_corruptions = []
        all_source_records = []
        duplicate_groups = []
        positive_pairs = []
        hard_negative_pairs = []

        profiles = corruptions_config.profiles
        severities = corruptions_config.severities

        source_a_rows, source_a_records, source_a_corruptions = generate_source_a(
            canonical_records=clean_records,
            profile=profiles["formatting_noise"],
            severities=severities,
            seed=dataset_config.seed + 1,
        )
        source_a_path = temp_base / "sources" / "source_a.csv"
        write_source_csv(source_a_path, source_a_rows, SOURCE_A_COLUMNS)
        all_corruptions.extend(source_a_corruptions)
        all_source_records.extend(source_a_records)

        source_b_rows, source_b_records, source_b_corruptions, source_b_columns = generate_source_b(
            canonical_records=clean_records,
            profile=profiles["schema_variation"],
            severities=severities,
            seed=dataset_config.seed + 2,
        )
        source_b_path = temp_base / "sources" / "source_b.csv"
        write_source_csv(source_b_path, source_b_rows, source_b_columns)
        all_corruptions.extend(source_b_corruptions)
        all_source_records.extend(source_b_records)

        (
            source_c_rows,
            source_c_records,
            source_c_corruptions,
            source_c_duplicates,
            source_c_pairs,
        ) = generate_source_c(
            canonical_records=clean_records,
            profile=profiles["semantic_noise"],
            severities=severities,
            seed=dataset_config.seed + 3,
        )
        source_c_path = temp_base / "sources" / "source_c.csv"
        write_source_csv(source_c_path, source_c_rows, SOURCE_A_COLUMNS)
        all_corruptions.extend(source_c_corruptions)
        all_source_records.extend(source_c_records)
        duplicate_groups.extend(source_c_duplicates)
        positive_pairs.extend(source_c_pairs)

        hard_positive_count = int(dataset_config.hard_cases.get("hard_positives_count", 200))
        hard_negative_count = int(dataset_config.hard_cases.get("hard_negatives_count", 200))

        hp_rows, hp_records, hp_corruptions, hp_pairs = generate_hard_positives(
            canonical_records=clean_records,
            profile=profiles["hard_positive"],
            severities=severities,
            count=hard_positive_count,
            seed=dataset_config.seed + 4,
        )
        hard_positive_path = temp_base / "hard_cases" / "hard_positives.csv"
        write_source_csv(hard_positive_path, hp_rows, HARD_POSITIVE_COLUMNS)
        all_corruptions.extend(hp_corruptions)
        all_source_records.extend(hp_records)
        positive_pairs.extend(hp_pairs)

        hn_rows, hn_records, hn_pairs = generate_hard_negatives(
            canonical_records=clean_records,
            count=hard_negative_count,
            seed=int(dataset_config.hard_cases.get("hard_negative_similarity_seed", 7))
            + dataset_config.seed,
        )
        hard_negative_path = temp_base / "hard_cases" / "hard_negatives.csv"
        write_source_csv(hard_negative_path, hn_rows, HARD_NEGATIVE_COLUMNS)
        all_source_records.extend(hn_records)
        hard_negative_pairs.extend(hn_pairs)

        malformed_dir = temp_base / dataset_config.malformed.get("output_subdirectory", "malformed")
        malformed_manifest = generate_malformed_fixtures(malformed_dir)

        person_mappings = {
            record.source_record_id: record.person_id for record in all_source_records
        }

        hard_negative_person_pairs = [
            (pair.person_id_a, pair.person_id_b) for pair in hard_negative_pairs
        ]
        splits = build_split_metadata(
            person_ids=[record["person_id"] for record in clean_records],
            split_config=dataset_config.splits,
            hard_negative_pairs=hard_negative_person_pairs,
        )

        expected_counts = {
            "canonical_records": len(clean_records),
            "source_a_records": len(source_a_rows),
            "source_b_records": len(source_b_rows),
            "source_c_records": len(source_c_rows),
            "hard_positive_records": len(hp_rows),
            "hard_negative_records": len(hn_rows),
            "duplicate_groups": len(duplicate_groups),
            "positive_pairs": len(positive_pairs),
            "hard_negative_pairs": len(hard_negative_pairs),
            "corruption_events": len(all_corruptions),
        }

        ground_truth = GroundTruth(
            person_mappings=person_mappings,
            duplicate_groups=duplicate_groups,
            positive_pairs=positive_pairs,
            hard_negative_pairs=hard_negative_pairs,
            corruption_history=all_corruptions,
            expected_counts=expected_counts,
            splits=splits,
        )

        ground_truth_dir = temp_base / "ground_truth"
        write_json(ground_truth_dir / "summary.json", ground_truth_to_dict(ground_truth))
        write_json(
            ground_truth_dir / "duplicate_groups.json",
            [group.__dict__ for group in duplicate_groups],
        )
        write_json(
            ground_truth_dir / "positive_pairs.json",
            [pair.__dict__ for pair in positive_pairs],
        )
        write_json(
            ground_truth_dir / "hard_negative_pairs.json",
            [pair.__dict__ for pair in hard_negative_pairs],
        )
        write_jsonl(
            ground_truth_dir / "corruption_log.jsonl",
            [corruption_to_dict(record) for record in all_corruptions],
        )

        schema_dir = temp_base / "schema"
        write_json(schema_dir / "canonical_schema.json", schema_config)

        config_dir = temp_base / "config"
        write_json(config_dir / "dataset_config.json", dataset_config.raw)
        write_json(config_dir / "corruptions_config.json", corruptions_config.raw)

        splits_dir = temp_base / "splits"
        write_json(splits_dir / "person_splits.json", splits)

        corruption_counts = _count_corruptions(all_corruptions)

        file_paths = {
            "canonical": clean_path,
            "source_a": source_a_path,
            "source_b": source_b_path,
            "source_c": source_c_path,
            "hard_positives": hard_positive_path,
            "hard_negatives": hard_negative_path,
            "ground_truth_summary": ground_truth_dir / "summary.json",
            "corruption_log": ground_truth_dir / "corruption_log.jsonl",
            "person_splits": splits_dir / "person_splits.json",
        }

        manifest = build_manifest(
            version=dataset_config.version,
            seed=dataset_config.seed,
            record_count=dataset_config.record_count,
            output_base=temp_base,
            file_paths=file_paths,
            corruption_counts=corruption_counts,
            expected_counts=expected_counts,
            generation_config={
                "dataset_config": str(dataset_config.raw),
                "canonical_fields": list(CANONICAL_FIELDS),
            },
        )
        manifest["malformed_fixtures"] = malformed_manifest

        manifest_path = temp_base / "manifest.json"
        write_manifest(manifest_path, manifest)

        data_card_path = temp_base / "README.md"
        data_card_path.write_text(
            _render_data_card(
                dataset_config=dataset_config,
                expected_counts=expected_counts,
                corruption_counts=corruption_counts,
            ),
            encoding="utf-8",
        )

        _finalize_output(temp_base, dataset_config.output_base)

    except Exception as exc:
        if temp_base.exists():
            shutil.rmtree(temp_base)
        raise DatasetBuildError(str(exc)) from exc

    return BuildResult(
        output_base=dataset_config.output_base,
        manifest_path=dataset_config.output_base / "manifest.json",
        ground_truth=ground_truth,
        corruption_counts=corruption_counts,
    )


def _render_data_card(
    *,
    dataset_config: DatasetConfig,
    expected_counts: dict[str, int],
    corruption_counts: dict[str, int],
) -> str:
    lines = [
        "# Golden Dataset Data Card",
        "",
        f"- Version: `{dataset_config.version}`",
        f"- Seed: `{dataset_config.seed}`",
        f"- Canonical records: `{expected_counts['canonical_records']}`",
        "",
        "## Source Variants",
        "",
        f"- Source A records: `{expected_counts['source_a_records']}`",
        f"- Source B records: `{expected_counts['source_b_records']}`",
        f"- Source C records: `{expected_counts['source_c_records']}`",
        f"- Hard positive records: `{expected_counts['hard_positive_records']}`",
        f"- Hard negative records: `{expected_counts['hard_negative_records']}`",
        "",
        "## Ground Truth",
        "",
        f"- Duplicate groups: `{expected_counts['duplicate_groups']}`",
        f"- Positive pairs: `{expected_counts['positive_pairs']}`",
        f"- Hard negative pairs: `{expected_counts['hard_negative_pairs']}`",
        f"- Corruption events: `{expected_counts['corruption_events']}`",
        "",
        "## Corruption Distribution",
        "",
    ]
    for corruption_type, count in sorted(corruption_counts.items()):
        lines.append(f"- `{corruption_type}`: `{count}`")

    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            "```bash",
            "python scripts/build_golden_dataset.py --config configs/dataset.yaml",
            "```",
            "",
            "Ground truth is derived from canonical clean-base identities and is stored",
            "separately from source CSV files.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_from_config_path(config_path: Path) -> BuildResult:
    dataset_config = load_dataset_config(config_path)
    return build_golden_dataset(dataset_config=dataset_config)
