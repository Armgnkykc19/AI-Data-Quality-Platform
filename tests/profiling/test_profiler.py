from pathlib import Path

from ingestion.config import load_ingestion_config
from ingestion.parser import parse_file
from profiling.profiler import profile_dataset


def test_profile_null_blank_completeness(sample_csv: Path) -> None:
    parsed = parse_file(sample_csv)
    config = load_ingestion_config()
    profile = profile_dataset(parsed, config)
    email_column = next(column for column in profile.columns if column.name == "email")
    assert email_column.row_count == 2
    assert email_column.null_count == 1
    assert email_column.blank_count == 0
    assert email_column.non_null_count == 1
    assert email_column.completeness_ratio == 0.5


def test_type_inference_integer_column(tmp_path, sample_csv: Path) -> None:
    path = tmp_path / "ints.csv"
    path.write_text("value\n1\n2\n3\n", encoding="utf-8")
    parsed = parse_file(path)
    profile = profile_dataset(parsed, load_ingestion_config())
    column = profile.columns[0]
    assert column.type_inference.inferred_type == "integer"
    assert column.type_inference.confidence == "high"


def test_pattern_profiling_email_like(tmp_path: Path) -> None:
    path = tmp_path / "emails.csv"
    path.write_text("email\nali@example.test\nbad-value\n", encoding="utf-8")
    parsed = parse_file(path)
    profile = profile_dataset(parsed, load_ingestion_config())
    column = profile.columns[0]
    email_pattern = next(
        pattern for pattern in column.patterns if pattern.pattern_name == "email_like"
    )
    assert email_pattern.match_count == 1
    assert email_pattern.sample_size == 2
