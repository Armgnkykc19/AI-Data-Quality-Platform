from __future__ import annotations

from pathlib import Path

from ingestion.config import IngestionConfig, load_ingestion_config
from ingestion.csv_parser import parse_csv_file
from ingestion.detector import detect_file_format
from ingestion.errors import IngestionError
from ingestion.excel_parser import parse_xlsx_file
from ingestion.models import ParsedDataset


def parse_file(
    path: Path,
    *,
    config: IngestionConfig | None = None,
    worksheet_name: str | None = None,
) -> ParsedDataset:
    ingestion_config = config or load_ingestion_config()
    if not path.exists():
        raise IngestionError(
            code="file_not_found",
            message=f"Input file not found: {path}",
        )
    file_format = detect_file_format(path, ingestion_config)
    if file_format == "csv":
        return parse_csv_file(path, ingestion_config)
    if file_format == "xlsx":
        return parse_xlsx_file(path, ingestion_config, worksheet_name=worksheet_name)
    raise IngestionError(
        code="unsupported_file_type",
        message=f"Unsupported format: {file_format}",
    )
