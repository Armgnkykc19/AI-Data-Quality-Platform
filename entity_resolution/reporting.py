from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import ResolutionResult


def write_resolution_reports(
    result: ResolutionResult,
    config: EntityResolutionConfig,
) -> Path | None:
    if not config.report_json and not config.report_markdown:
        return None

    output_dir = config.report_output_directory
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat()

    if config.report_json:
        payload = {
            "generated_at": timestamp,
            "source_label": result.source_label,
            **result.to_dict(),
        }
        json_path = output_dir / "entity_resolution_report.json"
        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if config.report_markdown:
        md_path = output_dir / "entity_resolution_report.md"
        summary = result.summary
        lines = [
            "# Entity Resolution Report",
            "",
            f"- Generated at: {timestamp}",
            f"- Source: {result.source_label}",
            f"- Records: {summary.record_count}",
            f"- Candidate pairs: {summary.candidate_pair_count}",
            f"- Candidate reduction ratio: {summary.candidate_reduction_ratio:.4f}",
            f"- AUTO_MATCH: {summary.auto_match_count}",
            f"- REVIEW: {summary.review_count}",
            f"- NO_MATCH: {summary.no_match_count}",
            f"- Clusters: {summary.cluster_count}",
            "",
        ]
        md_path.write_text("\n".join(lines), encoding="utf-8")

    return output_dir
