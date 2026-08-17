from __future__ import annotations

import json
from pathlib import Path

from schema_mapping.config import SchemaMappingConfig
from schema_mapping.models import MappingPlan


def write_mapping_reports(
    plan: MappingPlan,
    config: SchemaMappingConfig,
) -> Path | None:
    output_dir = config.report_output_directory
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mapping_report.json"
    if config.report_json:
        json_path.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if config.report_markdown:
        md_path = output_dir / "mapping_report.md"
        md_path.write_text(_render_markdown(plan), encoding="utf-8")
    return json_path if config.report_json else None


def _render_markdown(plan: MappingPlan) -> str:
    lines = [
        "# Schema Mapping Report",
        "",
        f"**Source:** {plan.source_path}",
        "",
        "## Summary",
        "",
        f"- AUTO_MAP: {plan.summary.auto_map_count}",
        f"- REVIEW: {plan.summary.review_count}",
        f"- UNMAPPED: {plan.summary.unmapped_count}",
        f"- CONFLICT: {plan.summary.conflict_count}",
        "",
        "| Source Column | Canonical Field | Decision | Score | Reason |",
        "|---|---|---|---:|---|",
    ]
    for mapping in plan.column_mappings:
        canonical = mapping.canonical_field or "-"
        lines.append(
            f"| {mapping.source_column} | {canonical} | {mapping.decision.value} | "
            f"{mapping.score:.4f} | {mapping.reason} |"
        )
    lines.append("")
    return "\n".join(lines)
