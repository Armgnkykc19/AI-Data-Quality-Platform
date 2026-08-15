import sys
from pathlib import Path

from scripts import profile_dataset


def test_profile_dataset_cli_csv_success(sample_csv: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile_dataset.py",
            str(sample_csv),
            "--report-dir",
            str(tmp_path / "reports"),
        ],
    )
    exit_code = profile_dataset.main()
    assert exit_code == 0
    assert (tmp_path / "reports" / "profile.json").exists()


def test_profile_dataset_cli_xlsx_success(sample_xlsx: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile_dataset.py",
            str(sample_xlsx),
            "--report-dir",
            str(tmp_path / "reports"),
        ],
    )
    exit_code = profile_dataset.main()
    assert exit_code == 0
