from __future__ import annotations

import json
from pathlib import Path

from normalization.config import NormalizationConfig
from record_quality.models import DatasetQualityResult


def write_json_quality_report(
    result: DatasetQualityResult,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_quality_reports(
    result: DatasetQualityResult,
    config: NormalizationConfig,
) -> Path | None:
    if not config.report_json:
        return None
    output_path = config.report_output_directory / "normalization_report.json"
    write_json_quality_report(result, output_path)
    return output_path
