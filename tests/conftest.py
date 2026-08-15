from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.models import ParsedDataset, ParsedRow, SourceMetadata
from normalization.config import load_normalization_config
from normalization.engine import NormalizationEngine
from validation.config import load_validation_config
from validation.engine import ValidationEngine

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def validation_config():
    return load_validation_config()


@pytest.fixture
def normalization_config():
    return load_normalization_config()


@pytest.fixture
def validation_engine(validation_config):
    return ValidationEngine(validation_config)


@pytest.fixture
def normalization_engine(normalization_config):
    return NormalizationEngine(normalization_config)


def make_valid_record(**overrides: str | None) -> dict[str, str | None]:
    record: dict[str, str | None] = {
        "first_name": "Ali",
        "last_name": "Yılmaz",
        "email": "ali@example.com",
        "phone": "+905321234567",
        "company": "Acme A.Ş.",
        "city": "İstanbul",
        "district": "Kadıköy",
        "address": "Bağdat Caddesi No:1",
    }
    record.update(overrides)
    return record


@pytest.fixture
def valid_record() -> dict[str, str | None]:
    return make_valid_record()


@pytest.fixture
def small_parsed_dataset() -> ParsedDataset:
    headers = [
        "first_name",
        "last_name",
        "email",
        "phone",
        "company",
        "city",
        "district",
        "address",
    ]
    rows = [
        ParsedRow(
            row_number=1,
            values={
                "first_name": "  Ali  ",
                "last_name": "Yılmaz",
                "email": " ALI@EXAMPLE.COM ",
                "phone": "05321234567",
                "company": "Acme A.S.",
                "city": "istanbul",
                "district": "kadikoy",
                "address": "Bağdat  Caddesi",
            },
        ),
        ParsedRow(
            row_number=2,
            values={
                "first_name": "Ayşe",
                "last_name": "Demir",
                "email": "ayse@example.com",
                "phone": "+905559876543",
                "company": "Beta Ltd.",
                "city": "Ankara",
                "district": "Çankaya",
                "address": "Atatürk Bulvarı 10",
            },
        ),
        ParsedRow(
            row_number=3,
            values={
                "first_name": "",
                "last_name": "Kaya",
                "email": "broken@@example.com",
                "phone": "abc-phone",
                "company": "Gamma",
                "city": "İstanbul",
                "district": "Çankaya",
                "address": "Test Sokak",
            },
        ),
    ]
    return ParsedDataset(
        metadata=SourceMetadata(
            path="tests/fixtures/small_quality.csv",
            format="csv",
            size_bytes=512,
            encoding="utf-8",
        ),
        headers=headers,
        rows=rows,
    )
