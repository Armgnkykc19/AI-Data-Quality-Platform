from evaluation.schema_mapping_benchmark import run_schema_mapping_benchmark


def test_schema_mapping_benchmark_passes() -> None:
    result = run_schema_mapping_benchmark()
    assert result.ran_successfully
    assert result.passed
    assert result.mapping_accuracy >= 0.90
    assert result.auto_map_precision == 1.0
