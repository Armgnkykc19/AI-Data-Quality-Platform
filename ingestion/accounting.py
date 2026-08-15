from __future__ import annotations

from ingestion.models import ParsedDataset, RowAccounting


def finalize_dataset_accounting(dataset: ParsedDataset) -> RowAccounting:
    dataset.finalize_accounting()
    assert dataset.accounting is not None
    return dataset.accounting


def validate_row_accounting(dataset: ParsedDataset) -> RowAccounting:
    return finalize_dataset_accounting(dataset)
