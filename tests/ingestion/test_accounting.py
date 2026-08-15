from ingestion.models import ParsedDataset, ParsedRow, RejectedRow, SourceMetadata


def test_row_accounting_invariant() -> None:
    dataset = ParsedDataset(
        metadata=SourceMetadata(path="x.csv", format="csv", size_bytes=1),
        headers=["a"],
        rows=[ParsedRow(row_number=2, values={"a": "1"})],
        rejected_rows=[
            RejectedRow(
                row_number=3,
                raw_values=("x",),
                reason_code="malformed",
                message="bad row",
            )
        ],
    )
    dataset.finalize_accounting()
    assert dataset.accounting is not None
    assert dataset.accounting.source_data_rows == 2
    assert dataset.accounting.accepted_rows == 1
    assert dataset.accounting.rejected_rows == 1


def test_finalize_recomputes_accounting() -> None:
    dataset = ParsedDataset(
        metadata=SourceMetadata(path="x.csv", format="csv", size_bytes=1),
        headers=["a"],
        rows=[ParsedRow(row_number=2, values={"a": "1"})],
        rejected_rows=[
            RejectedRow(
                row_number=3,
                raw_values=("bad",),
                reason_code="malformed",
                message="bad row",
            )
        ],
    )
    dataset.finalize_accounting()
    assert dataset.accounting is not None
    assert dataset.accounting.source_data_rows == 2
