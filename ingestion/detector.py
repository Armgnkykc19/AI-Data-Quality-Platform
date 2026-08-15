from __future__ import annotations

from pathlib import Path

from ingestion.config import IngestionConfig
from ingestion.errors import UnsupportedFileType


def detect_file_format(path: Path, config: IngestionConfig) -> str:
    suffix = path.suffix.lower()
    if suffix not in config.supported_extensions:
        raise UnsupportedFileType(
            code="unsupported_file_type",
            message=f"Unsupported file extension: {suffix or '(none)'}",
        )
    if suffix == ".csv":
        if not config.csv_enabled:
            raise UnsupportedFileType(
                code="csv_disabled",
                message="CSV ingestion is disabled by configuration.",
            )
        return "csv"
    if suffix == ".xlsx":
        if not config.excel_enabled:
            raise UnsupportedFileType(
                code="xlsx_disabled",
                message="XLSX ingestion is disabled by configuration.",
            )
        return "xlsx"
    raise UnsupportedFileType(
        code="unsupported_file_type",
        message=f"Unsupported file extension: {suffix}",
    )
