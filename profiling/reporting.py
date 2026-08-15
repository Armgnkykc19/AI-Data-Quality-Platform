from __future__ import annotations

import json
from pathlib import Path

from profiling.models import DatasetProfile


def write_json_profile_report(profile: DatasetProfile, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(profile.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_markdown_profile_report(profile: DatasetProfile, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Profiling Report",
        "",
        f"**Format:** {profile.format}",
        f"**Accepted Rows:** {profile.accepted_rows}",
        f"**Rejected Rows:** {profile.rejected_rows}",
        f"**Columns:** {profile.column_count}",
        f"**Status:** {profile.status}",
        "",
        "## Columns",
        "",
        "| Column | Completeness | Uniqueness | Inferred Type |",
        "|---|---:|---:|---|",
    ]
    for column in profile.columns:
        lines.append(
            f"| {column.name} | {column.completeness_ratio:.4f} | "
            f"{column.uniqueness_ratio:.4f} | {column.type_inference.inferred_type} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
