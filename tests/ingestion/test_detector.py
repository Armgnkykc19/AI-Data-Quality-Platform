from pathlib import Path

import pytest

from ingestion.config import load_ingestion_config
from ingestion.errors import EmptyFile, UnsupportedFileType
from ingestion.parser import parse_file


def test_detect_csv_and_xlsx(sample_csv: Path, sample_xlsx: Path) -> None:
    config = load_ingestion_config()
    csv_result = parse_file(sample_csv, config=config)
    xlsx_result = parse_file(sample_xlsx, config=config)
    assert csv_result.metadata.format == "csv"
    assert xlsx_result.metadata.format == "xlsx"


def test_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(UnsupportedFileType):
        parse_file(path)


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    with pytest.raises(EmptyFile):
        parse_file(path)
