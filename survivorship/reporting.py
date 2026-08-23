from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from survivorship.config import SurvivorshipConfig
from survivorship.models import SurvivorshipResult


def write_survivorship_reports(
    result: SurvivorshipResult,
    config: SurvivorshipConfig,
) -> Path | None:
    if not config.report_json and not config.report_markdown:
        return None

    output_dir = config.report_output_directory
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat()

    if config.report_json:
        payload = {
            "generated_at": timestamp,
            **result.to_dict(),
        }
        json_path = output_dir / "survivorship_report.json"
        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if config.report_markdown:
        summary = result.summary
        lines = [
            "# Survivorship Report",
            "",
            f"- Generated at: {timestamp}",
            f"- Source: {result.source_label}",
            f"- Canonical entities: {summary.canonical_entity_count}",
            f"- Merged entities: {summary.merged_entity_count}",
            f"- Singleton entities: {summary.singleton_entity_count}",
            f"- Preserved field conflicts: {summary.preserved_conflict_count}",
            "",
        ]
        md_path = output_dir / "survivorship_report.md"
        md_path.write_text("\n".join(lines), encoding="utf-8")

    return output_dir
