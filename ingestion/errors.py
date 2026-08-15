from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IngestionError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass
class UnsupportedFileType(IngestionError):
    pass


@dataclass
class FileTooLarge(IngestionError):
    size_bytes: int
    limit_bytes: int


@dataclass
class EmptyFile(IngestionError):
    pass


@dataclass
class MissingHeader(IngestionError):
    pass


@dataclass
class DuplicateHeader(IngestionError):
    duplicate_columns: tuple[str, ...]


@dataclass
class BlankHeaderCell(IngestionError):
    column_index: int


@dataclass
class EncodingError(IngestionError):
    encoding: str


@dataclass
class DelimiterDetectionError(IngestionError):
    pass


@dataclass
class RowLimitExceeded(IngestionError):
    row_count: int
    limit: int


@dataclass
class ColumnLimitExceeded(IngestionError):
    column_count: int
    limit: int


@dataclass
class WorkbookError(IngestionError):
    pass


@dataclass
class WorksheetNotFound(IngestionError):
    worksheet: str
