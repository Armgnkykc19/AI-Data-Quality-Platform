from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MALFORMED_DIR = PROJECT_ROOT / "datasets" / "golden" / "v0.1.0" / "malformed"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def malformed_dir() -> Path:
    return MALFORMED_DIR


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "sample.csv"
    path.write_text(
        "name,email,phone\nAli,ali@example.test,+905551234567\nAyse,,+905551234568\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sample_xlsx(tmp_path: Path) -> Path:
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Customers"
    sheet.append(["name", "email", "phone"])
    sheet.append(["Ali", "ali@example.test", "+905551234567"])
    sheet.append(["Ayse", None, "+905551234568"])
    workbook.create_sheet("Archive")
    workbook.save(path)
    return path
