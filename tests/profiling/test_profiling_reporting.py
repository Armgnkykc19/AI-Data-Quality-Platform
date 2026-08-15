from pathlib import Path

from profiling.models import DatasetProfile
from profiling.reporting import write_json_profile_report, write_markdown_profile_report


def test_profile_report_serialization(tmp_path: Path) -> None:
    profile = DatasetProfile(
        format="csv",
        row_count=1,
        column_count=1,
        accepted_rows=1,
        rejected_rows=0,
        empty_columns=(),
        parse_warning_count=0,
        status="ok",
    )
    json_path = tmp_path / "profile.json"
    md_path = tmp_path / "profile.md"
    write_json_profile_report(profile, json_path)
    write_markdown_profile_report(profile, md_path)
    assert "csv" in json_path.read_text(encoding="utf-8")
    assert "Profiling Report" in md_path.read_text(encoding="utf-8")
