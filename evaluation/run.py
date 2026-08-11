import argparse
from pathlib import Path

import yaml

from evaluation.evaluator.hard_gates import (
    all_hard_gates_pass,
    evaluate_hard_gates,
)
from evaluation.reporting import (
    build_report_data,
    write_json_report,
    write_markdown_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "evaluation.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the AI Data Quality Platform evaluation harness."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the evaluation YAML configuration file.",
    )

    return parser.parse_args()


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    with config_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def get_fixture_metrics() -> dict[str, float]:
    return {
        "auto_merge_precision": 0.995,
        "false_merge_rate": 0.003,
        "candidate_recall": 0.95,
        "schema_mapping_accuracy": 0.99,
        "normalization_accuracy": 0.999,
        "review_routing_recall": 0.97,
    }


def run_evaluation(config_path: Path) -> int:
    try:
        config = load_config(config_path)

        dataset = config["dataset"]
        metrics = get_fixture_metrics()

        gate_results = evaluate_hard_gates(
            metrics=metrics,
            gate_config=config["hard_gates"],
        )

        overall_passed = all_hard_gates_pass(gate_results)

        print("Evaluation Harness")
        print("------------------")
        print(f"Dataset: {dataset['name']}")
        print(f"Version: {dataset['version']}")
        print()

        print("Metrics")
        print("------------------")
        for metric_name, value in metrics.items():
            print(f"{metric_name}: {value:.4f}")

        print()
        print("Hard Gates")
        print("------------------")

        for result in gate_results:
            status = "PASS" if result.passed else "FAIL"
            print(
                f"{result.name}: {status} "
                f"(actual={result.actual:.4f}, "
                f"operator={result.operator}, "
                f"threshold={result.threshold:.4f})"
            )

        report_data = build_report_data(
            dataset_name=dataset["name"],
            dataset_version=dataset["version"],
            metrics=metrics,
            gate_results=gate_results,
            overall_passed=overall_passed,
        )

        output_directory = PROJECT_ROOT / config["reporting"]["output_directory"]

        if config["reporting"]["json"]:
            write_json_report(
                report_data,
                output_directory / "report.json",
            )

        if config["reporting"]["markdown"]:
            write_markdown_report(
                report_data,
                output_directory / "report.md",
            )

        print()
        print(f"Reports: {output_directory}")

        if overall_passed:
            print("Overall Status: PASS")
            return 0

        print("Overall Status: FAIL")
        return 1

    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"Evaluation infrastructure error: {exc}")
        return 2


def main() -> int:
    args = parse_args()

    return run_evaluation(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
