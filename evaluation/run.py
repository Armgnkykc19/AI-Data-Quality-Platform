import argparse
from pathlib import Path
from typing import Any

import yaml

from dataset.validation import validate_dataset
from evaluation.evaluator.hard_gates import (
    all_hard_gates_pass,
    evaluate_hard_gates,
)
from evaluation.ingestion_checks import run_ingestion_smoke_checks
from evaluation.normalization_benchmark import run_normalization_benchmark
from evaluation.reporting import (
    ENTITY_RESOLUTION_QUALITY_NOT_YET_AVAILABLE,
    EVALUATION_MODE_FIXTURE_SMOKE,
    EVALUATION_MODE_MIXED,
    PRODUCT_QUALITY_NOT_YET_AVAILABLE,
    PRODUCT_QUALITY_PARTIALLY_AVAILABLE,
    SCHEMA_MAPPING_QUALITY_AVAILABLE,
    SCHEMA_MAPPING_QUALITY_NOT_YET_AVAILABLE,
    build_report_data,
    write_json_report,
    write_markdown_report,
)
from evaluation.schema_mapping_benchmark import run_schema_mapping_benchmark
from evaluation.source_b_mapping_benchmark import run_source_b_mapping_benchmark
from evaluation.validation_benchmark import (
    failures_to_dict,
    run_validation_benchmark,
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


def _validation_benchmark_to_dict(result) -> dict[str, Any]:
    return {
        "positive_class_definition": result.positive_class_definition,
        "labeled_case_count": result.labeled_case_count,
        "true_positives": result.true_positives,
        "false_positives": result.false_positives,
        "false_negatives": result.false_negatives,
        "true_negatives": result.true_negatives,
        "precision": result.precision,
        "recall": result.recall,
        "f1": result.f1,
        "accuracy": result.accuracy,
        "passed": result.passed,
        "failures": failures_to_dict(result.failures),
        "case_results": result.case_results,
    }


def _normalization_benchmark_to_dict(result) -> dict[str, Any]:
    return {
        "expected_transformations": result.expected_transformations,
        "correct_transformations": result.correct_transformations,
        "incorrect_transformations": result.incorrect_transformations,
        "missed_transformations": result.missed_transformations,
        "unnecessary_transformations": result.unnecessary_transformations,
        "skipped_non_normalizable": result.skipped_non_normalizable,
        "normalization_accuracy": result.normalization_accuracy,
        "passed": result.passed,
    }


def _schema_mapping_benchmark_to_dict(result) -> dict[str, Any]:
    return {
        "labeled_case_count": result.labeled_case_count,
        "labeled_column_count": result.labeled_column_count,
        "correct_mappings": result.correct_mappings,
        "incorrect_mappings": result.incorrect_mappings,
        "missed_mappings": result.missed_mappings,
        "precision": result.precision,
        "recall": result.recall,
        "f1": result.f1,
        "mapping_accuracy": result.mapping_accuracy,
        "auto_map_total": result.auto_map_total,
        "auto_map_correct": result.auto_map_correct,
        "auto_map_incorrect": result.auto_map_incorrect,
        "auto_map_precision": result.auto_map_precision,
        "expected_review_count": result.expected_review_count,
        "correct_review_routing": result.correct_review_routing,
        "review_routing_recall": result.review_routing_recall,
        "expected_unmapped_count": result.expected_unmapped_count,
        "correct_unmapped": result.correct_unmapped,
        "failures_by_category": result.failures_by_category,
        "passed": result.passed,
    }


def _source_b_mapping_benchmark_to_dict(result) -> dict[str, Any]:
    return {
        "layout_count": result.layout_count,
        "labeled_column_count": result.labeled_column_count,
        "correct_mappings": result.correct_mappings,
        "incorrect_mappings": result.incorrect_mappings,
        "missed_mappings": result.missed_mappings,
        "precision": result.precision,
        "recall": result.recall,
        "f1": result.f1,
        "mapping_accuracy": result.mapping_accuracy,
        "auto_map_total": result.auto_map_total,
        "auto_map_correct": result.auto_map_correct,
        "auto_map_incorrect": result.auto_map_incorrect,
        "auto_map_precision": result.auto_map_precision,
        "expected_unmapped_count": result.expected_unmapped_count,
        "correct_unmapped": result.correct_unmapped,
        "passed": result.passed,
    }


def _resolve_evaluation_labels(
    *,
    validation_available: bool,
    normalization_available: bool,
    schema_mapping_available: bool,
) -> tuple[str, str]:
    if validation_available or normalization_available or schema_mapping_available:
        return EVALUATION_MODE_MIXED, PRODUCT_QUALITY_PARTIALLY_AVAILABLE
    return EVALUATION_MODE_FIXTURE_SMOKE, PRODUCT_QUALITY_NOT_YET_AVAILABLE


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

        validation_benchmark = run_validation_benchmark()
        validation_available = validation_benchmark.ran_successfully
        if validation_available:
            print("Real Validation Benchmark")
            print("------------------")
            print(f"labeled_case_count: {validation_benchmark.labeled_case_count}")
            print(f"true_positives: {validation_benchmark.true_positives}")
            print(f"false_positives: {validation_benchmark.false_positives}")
            print(f"false_negatives: {validation_benchmark.false_negatives}")
            print(f"precision: {validation_benchmark.precision:.4f}")
            print(f"recall: {validation_benchmark.recall:.4f}")
            print(f"f1: {validation_benchmark.f1:.4f}")
            print(
                "validation_benchmark: "
                f"{'PASS' if validation_benchmark.passed else 'FAIL'}"
            )
            print()
        elif validation_benchmark.error_message:
            print("Real Validation Benchmark")
            print("------------------")
            print(f"validation_benchmark: ERROR ({validation_benchmark.error_message})")
            print()

        normalization_benchmark = None
        normalization_available = False
        if dataset_path is not None:
            normalization_benchmark = run_normalization_benchmark(dataset_path=dataset_path)
            normalization_available = True
            print("Real Deterministic Normalization Benchmark")
            print("------------------")
            print(
                "expected_transformations: "
                f"{normalization_benchmark.expected_transformations}"
            )
            print(
                "correct_transformations: "
                f"{normalization_benchmark.correct_transformations}"
            )
            print(
                "incorrect_transformations: "
                f"{normalization_benchmark.incorrect_transformations}"
            )
            print(
                "missed_transformations: "
                f"{normalization_benchmark.missed_transformations}"
            )
            print(
                f"normalization_accuracy: "
                f"{normalization_benchmark.normalization_accuracy:.4f} "
                "(real benchmark — whitespace/phone_format only)"
            )
            print(
                "normalization_benchmark: "
                f"{'PASS' if normalization_benchmark.passed else 'FAIL'}"
            )
            print()

        schema_mapping_benchmark = run_schema_mapping_benchmark()
        schema_mapping_available = schema_mapping_benchmark.ran_successfully
        if schema_mapping_available:
            print("Real Schema Mapping Benchmark")
            print("------------------")
            print(f"labeled_case_count: {schema_mapping_benchmark.labeled_case_count}")
            print(f"labeled_column_count: {schema_mapping_benchmark.labeled_column_count}")
            print(f"correct_mappings: {schema_mapping_benchmark.correct_mappings}")
            print(f"incorrect_mappings: {schema_mapping_benchmark.incorrect_mappings}")
            print(f"missed_mappings: {schema_mapping_benchmark.missed_mappings}")
            print(f"precision: {schema_mapping_benchmark.precision:.4f}")
            print(f"recall: {schema_mapping_benchmark.recall:.4f}")
            print(f"f1: {schema_mapping_benchmark.f1:.4f}")
            print(f"mapping_accuracy: {schema_mapping_benchmark.mapping_accuracy:.4f}")
            print(
                f"auto_map_precision: {schema_mapping_benchmark.auto_map_precision:.4f}"
            )
            print(
                f"review_routing_recall: "
                f"{schema_mapping_benchmark.review_routing_recall:.4f}"
            )
            print(
                "schema_mapping_benchmark: "
                f"{'PASS' if schema_mapping_benchmark.passed else 'FAIL'}"
            )
            print()
        elif schema_mapping_benchmark.error_message:
            print("Real Schema Mapping Benchmark")
            print("------------------")
            print(
                f"schema_mapping_benchmark: ERROR ({schema_mapping_benchmark.error_message})"
            )
            print()

        source_b_mapping_benchmark = run_source_b_mapping_benchmark()
        if source_b_mapping_benchmark.ran_successfully:
            print("Real Source B Schema Mapping Benchmark")
            print("------------------")
            print(f"layout_count: {source_b_mapping_benchmark.layout_count}")
            print(f"labeled_column_count: {source_b_mapping_benchmark.labeled_column_count}")
            print(f"correct_mappings: {source_b_mapping_benchmark.correct_mappings}")
            print(f"mapping_accuracy: {source_b_mapping_benchmark.mapping_accuracy:.4f}")
            print(
                f"auto_map_precision: {source_b_mapping_benchmark.auto_map_precision:.4f}"
            )
            print(
                "source_b_mapping_benchmark: "
                f"{'PASS' if source_b_mapping_benchmark.passed else 'FAIL'}"
            )
            print()
        elif source_b_mapping_benchmark.error_message:
            print("Real Source B Schema Mapping Benchmark")
            print("------------------")
            print(
                "source_b_mapping_benchmark: ERROR "
                f"({source_b_mapping_benchmark.error_message})"
            )
            print()

        evaluation_mode, product_quality = _resolve_evaluation_labels(
            validation_available=validation_available,
            normalization_available=normalization_available,
            schema_mapping_available=schema_mapping_available,
        )

        print("Evaluation Harness")
        print("------------------")
        print(f"Evaluation Mode: {evaluation_mode}")
        print(f"Dataset: {dataset['name']}")
        print(f"Version: {dataset['version']}")
        print()

        print("Fixture Smoke Metrics (Infrastructure Only)")
        print("------------------")
        for metric_name, value in metrics.items():
            print(f"{metric_name}: {value:.4f}")

        if validation_available:
            print()
            print("Real Validation Benchmark Metrics")
            print("------------------")
            print(f"precision: {validation_benchmark.precision:.4f}")
            print(f"recall: {validation_benchmark.recall:.4f}")
            print(f"f1: {validation_benchmark.f1:.4f}")

        if normalization_available and normalization_benchmark is not None:
            print()
            print("Real Deterministic Normalization Benchmark Metrics")
            print("------------------")
            print(
                "normalization_accuracy: "
                f"{normalization_benchmark.normalization_accuracy:.4f} "
                "(source: golden_dataset_corruption_log)"
            )

        if schema_mapping_available:
            print()
            print("Real Schema Mapping Benchmark Metrics")
            print("------------------")
            print(f"mapping_accuracy: {schema_mapping_benchmark.mapping_accuracy:.4f}")
            print(f"precision: {schema_mapping_benchmark.precision:.4f}")
            print(f"recall: {schema_mapping_benchmark.recall:.4f}")
            print(f"f1: {schema_mapping_benchmark.f1:.4f}")
            print(f"auto_map_precision: {schema_mapping_benchmark.auto_map_precision:.4f}")
            print(
                "review_routing_recall: "
                f"{schema_mapping_benchmark.review_routing_recall:.4f} "
                "(source: labeled mapping benchmark cases)"
            )

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
            evaluation_mode=evaluation_mode,
            product_quality_evaluation=product_quality,
            real_validation_benchmark=(
                _validation_benchmark_to_dict(validation_benchmark)
                if validation_available
                else None
            ),
            real_normalization_benchmark=(
                _normalization_benchmark_to_dict(normalization_benchmark)
                if normalization_available and normalization_benchmark is not None
                else None
            ),
            real_schema_mapping_benchmark=(
                _schema_mapping_benchmark_to_dict(schema_mapping_benchmark)
                if schema_mapping_available
                else None
            ),
            real_source_b_mapping_benchmark=(
                _source_b_mapping_benchmark_to_dict(source_b_mapping_benchmark)
                if source_b_mapping_benchmark.ran_successfully
                else None
            ),
            schema_mapping_quality=(
                SCHEMA_MAPPING_QUALITY_AVAILABLE
                if schema_mapping_available
                else SCHEMA_MAPPING_QUALITY_NOT_YET_AVAILABLE
            ),
        )

        output_directory = PROJECT_ROOT / config["reporting"]["output_directory"]
        if config["reporting"]["json"]:
            write_json_report(report_data, output_directory / "report.json")
        if config["reporting"]["markdown"]:
            write_markdown_report(report_data, output_directory / "report.md")

        print()
        print(f"Reports: {output_directory}")
        print()

        hard_gate_status = "PASS" if overall_passed else "FAIL"
        print(f"Infrastructure Hard Gates: {hard_gate_status}")
        print(f"Overall Infrastructure Status: {hard_gate_status}")
        print(f"Product Quality Evaluation: {product_quality}")
        print(f"Entity Resolution Quality: {ENTITY_RESOLUTION_QUALITY_NOT_YET_AVAILABLE}")
        schema_quality = (
            SCHEMA_MAPPING_QUALITY_AVAILABLE
            if schema_mapping_available
            else SCHEMA_MAPPING_QUALITY_NOT_YET_AVAILABLE
        )
        print(f"Schema Mapping Quality: {schema_quality}")

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
