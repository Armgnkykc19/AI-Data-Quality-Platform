from evaluation.source_b_mapping_benchmark import run_source_b_mapping_benchmark


def test_source_b_mapping_benchmark_passes() -> None:
    result = run_source_b_mapping_benchmark()
    assert result.ran_successfully, result.error_message
    assert result.passed, result.messages
    assert result.layout_count == 3
    assert result.mapping_accuracy >= 0.95
    assert result.auto_map_precision == 1.0
