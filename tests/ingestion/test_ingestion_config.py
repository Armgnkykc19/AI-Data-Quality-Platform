from pathlib import Path

import pytest
import yaml

from ingestion.config import load_ingestion_config


def test_load_ingestion_config_reads_supported_extensions() -> None:
    config = load_ingestion_config()
    assert ".csv" in config.supported_extensions
    assert ".xlsx" in config.supported_extensions


def test_load_ingestion_config_rejects_invalid_limits(tmp_path: Path) -> None:
    path = tmp_path / "ingestion.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "supported_extensions": [".csv"],
                "limits": {"max_file_size_bytes": 0, "max_row_count": 0, "max_column_count": 0},
                "csv": {"supported_delimiters": [","], "supported_encodings": ["utf-8"]},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="limits"):
        load_ingestion_config(path)
