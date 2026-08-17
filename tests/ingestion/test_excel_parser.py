from pathlib import Path

import pytest

from ingestion.errors import WorksheetNotFound
from ingestion.parser import parse_file


def test_parse_xlsx_first_worksheet(sample_xlsx: Path) -> None:
    parsed = parse_file(sample_xlsx)
    assert parsed.metadata.worksheet == "Customers"
    assert parsed.metadata.worksheet_selection_policy == "first"
    assert "Archive" in parsed.metadata.available_worksheets
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 2


def test_parse_xlsx_explicit_worksheet(sample_xlsx: Path) -> None:
    parsed = parse_file(sample_xlsx, worksheet_name="Customers")
    assert parsed.metadata.worksheet == "Customers"


def test_missing_worksheet_raises(sample_xlsx: Path) -> None:
    with pytest.raises(WorksheetNotFound):
        parse_file(sample_xlsx, worksheet_name="Missing")


def test_extra_column_row_is_rejected(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    path = tmp_path / "extra_col.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["name", "email"])
    sheet.append(["Ali", "ali@example.test", "extra-value"])
    workbook.save(path)

    parsed = parse_file(path)
    assert parsed.accounting is not None
    assert parsed.accounting.accepted_rows == 0
    assert parsed.accounting.rejected_rows == 1
    assert parsed.rejected_rows[0].reason_code == "inconsistent_column_count"

    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    path = tmp_path / "empty.xlsx"
    workbook = Workbook()
    workbook.active.title = "Empty"
    workbook.save(path)
    from ingestion.errors import EmptyFile

    with pytest.raises(EmptyFile):
        parse_file(path)
