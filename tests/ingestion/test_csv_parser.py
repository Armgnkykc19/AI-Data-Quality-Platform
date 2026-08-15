from pathlib import Path

import pytest

from ingestion.config import load_ingestion_config
from ingestion.errors import DuplicateHeader
from ingestion.parser import parse_file


def test_parse_comma_csv(sample_csv: Path) -> None:
    parsed = parse_file(sample_csv)
    assert parsed.metadata.delimiter == ","
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 2
    assert parsed.accounting.rejected_rows == 0


def test_parse_semicolon_fixture(malformed_dir: Path) -> None:
    parsed = parse_file(malformed_dir / "semicolon_delimiter.csv")
    assert parsed.metadata.delimiter == ";"
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 1


def test_parse_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes("\ufeffname,email\nAli,ali@example.test\n".encode("utf-8-sig"))
    parsed = parse_file(path)
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 1


def test_duplicate_header_raises(malformed_dir: Path) -> None:
    with pytest.raises(DuplicateHeader):
        parse_file(malformed_dir / "duplicate_header.csv")


def test_missing_column_row_is_rejected(malformed_dir: Path) -> None:
    parsed = parse_file(malformed_dir / "missing_column_row.csv")
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 0
    assert parsed.accounting.rejected_rows == 1


def test_header_only_has_zero_accounted_rows(malformed_dir: Path) -> None:
    parsed = parse_file(malformed_dir / "header_only.csv")
    assert parsed.accounting is not None
    assert parsed.accounting.source_data_rows == 0


def test_quoted_delimiters(tmp_path: Path) -> None:
    path = tmp_path / "quoted.csv"
    path.write_text(
        'name,company\nAli,"Anadolu Teknoloji, A.S."\n',
        encoding="utf-8",
    )
    parsed = parse_file(path)
    assert parsed.rows[0].values["company"] == "Anadolu Teknoloji, A.S."


def test_escaped_quotes_inside_quoted_field(tmp_path: Path) -> None:
    path = tmp_path / "escaped.csv"
    path.write_text(
        'name,company\nAli,"Anadolu ""Best"" Teknoloji"\n',
        encoding="utf-8",
    )
    parsed = parse_file(path)
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 1
    assert parsed.rows[0].values["company"] == 'Anadolu "Best" Teknoloji'


def test_multiline_quoted_field_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "multiline.csv"
    path.write_text(
        'name,company\nAli,"Anadolu\nTeknoloji"\n',
        encoding="utf-8",
    )
    parsed = parse_file(path)
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 1
    company = parsed.rows[0].values["company"]
    assert company is not None
    assert company.replace("\r\n", "\n") == "Anadolu\nTeknoloji"


def test_broken_quotes_fixture_is_rejected(malformed_dir: Path) -> None:
    parsed = parse_file(malformed_dir / "broken_quotes.csv")
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 0
    assert parsed.accounting.rejected_rows == 1
    assert parsed.rejected_rows[0].reason_code == "unclosed_quote"


def test_valid_quoted_field_still_parses(malformed_dir: Path) -> None:
    parsed = parse_file(malformed_dir / "utf8_turkish.csv")
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 1


def test_cp1254_encoding(tmp_path: Path) -> None:
    path = tmp_path / "turkish.csv"
    path.write_text(
        "first_name,last_name,city\nOğuz,Şahin,İstanbul\n",
        encoding="cp1254",
    )
    parsed = parse_file(path)
    assert parsed.rows[0].values["first_name"] == "Oğuz"


def test_row_limit_enforced(tmp_path: Path) -> None:
    config = load_ingestion_config()
    limited = config.raw.copy()
    limited["limits"] = {
        "max_file_size_bytes": 100000,
        "max_row_count": 1,
        "max_column_count": 10,
    }
    config_path = tmp_path / "ingestion.yaml"
    import yaml

    config_path.write_text(yaml.safe_dump(limited), encoding="utf-8")
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    from ingestion.config import load_ingestion_config as reload_config
    from ingestion.errors import RowLimitExceeded

    with pytest.raises(RowLimitExceeded):
        parse_file(csv_path, config=reload_config(config_path))
