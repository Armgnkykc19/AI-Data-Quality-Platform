from __future__ import annotations

import csv
import io
from pathlib import Path

from ingestion.config import IngestionConfig
from ingestion.errors import (
    BlankHeaderCell,
    ColumnLimitExceeded,
    DelimiterDetectionError,
    DuplicateHeader,
    EmptyFile,
    EncodingError,
    FileTooLarge,
    MissingHeader,
    RowLimitExceeded,
)
from ingestion.models import (
    ParsedDataset,
    ParsedRow,
    ParseIssue,
    RejectedRow,
    SourceMetadata,
)


def _read_bytes(path: Path, config: IngestionConfig) -> bytes:
    size = path.stat().st_size
    if size == 0:
        if config.empty_file_policy == "error":
            raise EmptyFile(code="empty_file", message="Input file is empty.")
        return b""
    if size > config.max_file_size_bytes:
        raise FileTooLarge(
            code="file_too_large",
            message=f"File size {size} exceeds limit {config.max_file_size_bytes}.",
            size_bytes=size,
            limit_bytes=config.max_file_size_bytes,
        )
    return path.read_bytes()


def _decode_text(raw: bytes, config: IngestionConfig) -> tuple[str, str]:
    if not raw:
        return "", config.csv_default_encoding
    last_error: Exception | None = None
    for encoding in config.csv_encodings:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    raise EncodingError(
        code="encoding_error",
        message=f"Unable to decode file with supported encodings: {config.csv_encodings}. "
        f"Last error: {last_error}",
        encoding=config.csv_default_encoding,
    )


def _count_delimiter(line: str, delimiter: str) -> int:
    in_quotes = False
    count = 0
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            if in_quotes and index + 1 < len(line) and line[index + 1] == '"':
                index += 2
                continue
            in_quotes = not in_quotes
        elif char == delimiter and not in_quotes:
            count += 1
        index += 1
    return count


def detect_delimiter(text: str, config: IngestionConfig) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise DelimiterDetectionError(
            code="delimiter_detection_error",
            message="Cannot detect delimiter from empty content.",
        )
    sample_lines = lines[: config.csv_delimiter_sample_lines]
    scores: dict[str, int] = {}
    for delimiter in config.csv_delimiters:
        counts = [_count_delimiter(line, delimiter) for line in sample_lines]
        if not counts or max(counts) == 0:
            continue
        if min(counts) == max(counts) and counts[0] > 0:
            scores[delimiter] = counts[0]
    if not scores:
        # Single-column files have no delimiter characters; default to comma.
        return config.csv_delimiters[0] if config.csv_delimiters else ","
    best = max(scores.items(), key=lambda item: (item[1], item[0] == ","))
    ambiguous = [delimiter for delimiter, score in scores.items() if score == best[1]]
    if len(ambiguous) > 1:
        raise DelimiterDetectionError(
            code="delimiter_detection_error",
            message=f"Ambiguous delimiter detection among: {ambiguous}",
        )
    return best[0]


def _validate_headers(headers: list[str], config: IngestionConfig) -> None:
    if not headers:
        raise MissingHeader(code="missing_header", message="CSV file is missing a header row.")
    for index, header in enumerate(headers):
        if header is None or str(header).strip() == "":
            if config.blank_header_cell_policy == "error":
                raise BlankHeaderCell(
                    code="blank_header_cell",
                    message=f"Blank header cell at column index {index}.",
                    column_index=index,
                )
    normalized = [str(header).strip() for header in headers]
    duplicates = sorted({name for name in normalized if normalized.count(name) > 1})
    if duplicates and config.duplicate_header_policy == "error":
        raise DuplicateHeader(
            code="duplicate_header",
            message=f"Duplicate header columns detected: {', '.join(duplicates)}",
            duplicate_columns=tuple(duplicates),
        )
    if len(headers) > config.max_column_count:
        raise ColumnLimitExceeded(
            code="column_limit_exceeded",
            message=f"Column count {len(headers)} exceeds limit {config.max_column_count}.",
            column_count=len(headers),
            limit=config.max_column_count,
        )


def _is_entirely_blank(values: list[str | None]) -> bool:
    return all(value is None or str(value).strip() == "" for value in values)


def parse_csv_file(path: Path, config: IngestionConfig) -> ParsedDataset:
    raw = _read_bytes(path, config)
    text, encoding = _decode_text(raw, config)
    metadata = SourceMetadata(
        path=str(path),
        format="csv",
        size_bytes=path.stat().st_size,
        encoding=encoding,
    )
    dataset = ParsedDataset(metadata=metadata, headers=[])

    if not text.strip():
        dataset.finalize_accounting()
        return dataset

    delimiter = detect_delimiter(text, config)
    metadata = SourceMetadata(
        path=str(path),
        format="csv",
        size_bytes=path.stat().st_size,
        encoding=encoding,
        delimiter=delimiter,
    )
    dataset.metadata = metadata

    reader = csv.reader(io.StringIO(text), delimiter=delimiter, quotechar='"')
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise DelimiterDetectionError(
            code="csv_parse_error",
            message=f"CSV parsing failed: {exc}",
        ) from exc

    if not rows:
        dataset.finalize_accounting()
        return dataset

    headers = [str(value).strip() if value is not None else "" for value in rows[0]]
    _validate_headers(headers, config)
    dataset.headers = headers

    data_rows = rows[1:]
    if len(data_rows) > config.max_row_count:
        raise RowLimitExceeded(
            code="row_limit_exceeded",
            message=f"Row count {len(data_rows)} exceeds limit {config.max_row_count}.",
            row_count=len(data_rows),
            limit=config.max_row_count,
        )

    for index, raw_row in enumerate(data_rows, start=2):
        if _is_entirely_blank(raw_row):
            if config.entirely_blank_row_policy == "reject":
                dataset.rejected_rows.append(
                    RejectedRow(
                        row_number=index,
                        raw_values=tuple(raw_row),
                        reason_code="entirely_blank_row",
                        message="Row contains only blank cells.",
                    )
                )
                continue
        if len(raw_row) != len(headers):
            if config.malformed_row_policy == "reject":
                dataset.rejected_rows.append(
                    RejectedRow(
                        row_number=index,
                        raw_values=tuple(raw_row),
                        reason_code="inconsistent_column_count",
                        message=(
                            f"Expected {len(headers)} columns, found {len(raw_row)}."
                        ),
                    )
                )
                dataset.issues.append(
                    ParseIssue(
                        code="inconsistent_column_count",
                        message=(
                            f"Row {index} has {len(raw_row)} columns; expected {len(headers)}."
                        ),
                        severity="warning",
                        row_number=index,
                    )
                )
                continue
            raise DelimiterDetectionError(
                code="malformed_row",
                message=f"Malformed row {index}: inconsistent column count.",
            )
        values = {
            headers[col_index]: (raw_row[col_index] if raw_row[col_index] != "" else None)
            for col_index in range(len(headers))
        }
        dataset.rows.append(ParsedRow(row_number=index, values=values))

    dataset.finalize_accounting()
    return dataset
