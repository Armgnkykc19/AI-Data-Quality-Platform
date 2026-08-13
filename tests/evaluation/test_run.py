from pathlib import Path

from evaluation.run import (
    get_fixture_metrics,
    load_config,
    run_evaluation,
)


def test_load_config_reads_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "evaluation.yaml"
    config_file.write_text(
        """
dataset:
  name: test-dataset
  version: "1.0.0"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config["dataset"]["name"] == "test-dataset"
    assert config["dataset"]["version"] == "1.0.0"


def test_fixture_metrics_include_required_hard_gate_metrics() -> None:
    metrics = get_fixture_metrics()

    assert "auto_merge_precision" in metrics
    assert "false_merge_rate" in metrics
    assert "candidate_recall" in metrics
    assert "schema_mapping_accuracy" in metrics
    assert "normalization_accuracy" in metrics
    assert "review_routing_recall" in metrics


def test_run_evaluation_returns_zero_when_all_gates_pass(
    tmp_path: Path,
    capsys,
) -> None:
    config_file = tmp_path / "evaluation.yaml"
    report_directory = tmp_path / "reports"

    config_file.write_text(
        f"""
dataset:
  name: fixture
  version: "0.1.0"

hard_gates:
  auto_merge_precision:
    operator: gte
    threshold: 0.99
  false_merge_rate:
    operator: lte
    threshold: 0.005
  candidate_recall:
    operator: gte
    threshold: 0.94
  schema_mapping_accuracy:
    operator: gte
    threshold: 0.98
  normalization_accuracy:
    operator: gte
    threshold: 0.995
  review_routing_recall:
    operator: gte
    threshold: 0.95

reporting:
  output_directory: "{report_directory.as_posix()}"
  json: false
  markdown: false
""".strip(),
        encoding="utf-8",
    )

    exit_code = run_evaluation(config_file)

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Evaluation Mode: FIXTURE_SMOKE" in captured.out
    assert "Product Quality Evaluation: NOT_YET_AVAILABLE" in captured.out
    assert "Hard Gate Status: PASS" in captured.out
    assert "Fixture Smoke Metrics (Infrastructure Only)" in captured.out


def test_run_evaluation_returns_one_when_hard_gate_fails(
    tmp_path: Path,
    capsys,
) -> None:
    config_file = tmp_path / "evaluation.yaml"
    report_directory = tmp_path / "reports"

    config_file.write_text(
        f"""
dataset:
  name: fixture
  version: "0.1.0"

hard_gates:
  auto_merge_precision:
    operator: gte
    threshold: 1.0
  false_merge_rate:
    operator: lte
    threshold: 0.005
  candidate_recall:
    operator: gte
    threshold: 0.94
  schema_mapping_accuracy:
    operator: gte
    threshold: 0.98
  normalization_accuracy:
    operator: gte
    threshold: 0.995
  review_routing_recall:
    operator: gte
    threshold: 0.95

reporting:
  output_directory: "{report_directory.as_posix()}"
  json: false
  markdown: false
""".strip(),
        encoding="utf-8",
    )

    exit_code = run_evaluation(config_file)

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Evaluation Mode: FIXTURE_SMOKE" in captured.out
    assert "Hard Gate Status: FAIL" in captured.out
    assert "Product Quality Evaluation: NOT_YET_AVAILABLE" in captured.out


def test_run_evaluation_returns_two_for_missing_config(
    tmp_path: Path,
    capsys,
) -> None:
    missing_config = tmp_path / "missing.yaml"

    exit_code = run_evaluation(missing_config)

    captured = capsys.readouterr()

    assert exit_code == 2
    assert "Evaluation infrastructure error" in captured.out
