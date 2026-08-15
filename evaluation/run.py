import argparse
from pathlib import Path

import yaml

from dataset.validation import validate_dataset
from evaluation.evaluator.hard_gates import (
    all_hard_gates_pass,
    evaluate_hard_gates,
)
from evaluation.ingestion_checks import run_ingestion_smoke_checks
from evaluation.reporting import (
    EVALUATION_MODE_FIXTURE_SMOKE,
    PRODUCT_QUALITY_NOT_YET_AVAILABLE,
    build_report_data,
    write_json_report,
    write_markdown_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "evaluation.yaml"
DEFAULT_MALFORMED_DIR = PROJECT_ROOT / "datasets" / "golden" / "v0.1.0" / "malformed"


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
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help=(
            "Optional generated golden dataset path for oracle/sanity-check validation. "
            "Does not report oracle results as product hard-gate success."
        ),
    )

    parser.add_argument(
        "--malformed-fixtures",
        type=Path,
        default=None,
        help="Optional malformed fixture directory for real ingestion smoke checks.",
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


def run_dataset_sanity_checks(dataset_path: Path) -> tuple[bool, list[str]]:
    messages: list[str] = []
    validation = validate_dataset(dataset_path)
    for issue in validation.issues:
        messages.append(f"{issue.severity}:{issue.code}:{issue.message}")

    if not validation.passed:
        messages.append("dataset_contract:FAIL")
        return False, messages

    messages.append("dataset_contract:PASS")
    messages.append("oracle_sanity_check:PASS")
    return True, messages


def run_evaluation(
    config_path: Path,
    dataset_path: Path | None = None,
    malformed_fixtures_path: Path | None = None,
) -> int:
    try:
        config = load_config(config_path)

        dataset = config["dataset"]
        metrics = get_fixture_metrics()

        gate_results = evaluate_hard_gates(
            metrics=metrics,
            gate_config=config["hard_gates"],
        )

        overall_passed = all_hard_gates_pass(gate_results)

        if dataset_path is not None:
            sanity_passed, sanity_messages = run_dataset_sanity_checks(dataset_path)
            print("Dataset Sanity Checks")
            print("------------------")
            for message in sanity_messages:
                print(message)
            print()
            if not sanity_passed:
                print("Dataset Sanity Status: FAIL")
                return 1
            print("Dataset Sanity Status: PASS (oracle/sanity-check only)")
            print()

        malformed_dir = malformed_fixtures_path
        if malformed_dir is not None and malformed_dir.exists():
            ingestion_passed, ingestion_messages = run_ingestion_smoke_checks(
                malformed_dir=malformed_dir
            )
            print("Ingestion Smoke Checks (Real Sprint 03 Signals)")
            print("------------------")
            for message in ingestion_messages:
                print(message)
            print()
            if not ingestion_passed:
                print("Ingestion Smoke Status: FAIL")
                return 1
            print("Ingestion Smoke Status: PASS")
            print()

        print("Evaluation Harness")
        print("------------------")
        print(f"Evaluation Mode: {EVALUATION_MODE_FIXTURE_SMOKE}")
        print(f"Product Quality Evaluation: {PRODUCT_QUALITY_NOT_YET_AVAILABLE}")
        print(f"Dataset: {dataset['name']}")
        print(f"Version: {dataset['version']}")
        print()

        print("Fixture Smoke Metrics (Infrastructure Only)")
        print("------------------")
        for metric_name, value in metrics.items():
            print(f"{metric_name}: {value:.4f}")

        print()
        print("Hard Gates (Fixture Smoke)")
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
        print()

        hard_gate_status = "PASS" if overall_passed else "FAIL"
        print(f"Hard Gate Status: {hard_gate_status}")
        print(f"Overall Infrastructure Status: {hard_gate_status}")
        print(f"Product Quality Evaluation: {PRODUCT_QUALITY_NOT_YET_AVAILABLE}")

        if overall_passed:
            return 0

        return 1

    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"Evaluation infrastructure error: {exc}")
        return 2


def main() -> int:
    args = parse_args()

    return run_evaluation(args.config, args.dataset, args.malformed_fixtures)


if __name__ == "__main__":
    raise SystemExit(main())
