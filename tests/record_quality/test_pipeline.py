from __future__ import annotations

from ingestion.models import ParsedDataset
from record_quality.pipeline import run_quality_pipeline


def test_run_quality_pipeline_on_small_dataset(small_parsed_dataset: ParsedDataset) -> None:
    result = run_quality_pipeline(small_parsed_dataset)

    assert result.source_path == small_parsed_dataset.metadata.path
    assert len(result.records) == 3
    assert result.pre_validation_summary.total_records == 3
    assert result.post_validation_summary.total_records == 3


def test_original_values_are_preserved_in_quality_records(
    small_parsed_dataset: ParsedDataset,
) -> None:
    result = run_quality_pipeline(small_parsed_dataset)

    first = result.records[0]
    assert first.original_values["first_name"] == "  Ali  "
    assert first.original_values["city"] == "istanbul"
    assert first.original_values["phone"] == "05321234567"


def test_normalization_and_revalidation_occur_for_eligible_fields(
    small_parsed_dataset: ParsedDataset,
) -> None:
    result = run_quality_pipeline(small_parsed_dataset)

    first = result.records[0]
    assert first.normalized_values["first_name"] == "Ali"
    assert first.normalized_values["city"] == "İstanbul"
    assert first.normalized_values["phone"] == "+905321234567"
    assert first.pre_validation is not None
    assert first.post_validation is not None
    assert len(first.transformations) > 0


def test_missing_required_field_blocks_normalization_for_that_field(
    small_parsed_dataset: ParsedDataset,
) -> None:
    result = run_quality_pipeline(small_parsed_dataset)

    broken = result.records[2]
    assert broken.original_values["first_name"] == ""
    assert broken.normalized_values["first_name"] == ""
    assert broken.pre_validation is not None
    assert broken.pre_validation.is_valid is False
