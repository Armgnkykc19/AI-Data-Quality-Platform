import argparse
from pathlib import Path
from typing import Any

import yaml

from dataset.validation import validate_dataset
from evaluation.entity_resolution_benchmark import run_entity_resolution_benchmark
from evaluation.evaluator.hard_gates import (
    all_hard_gates_pass,
    evaluate_hard_gates,
)
from evaluation.ingestion_checks import run_ingestion_smoke_checks
from evaluation.normalization_benchmark import run_normalization_benchmark
from evaluation.product_metrics import (
    collect_product_metrics,
    evaluate_product_gates,
    product_metrics_summary,
)
from evaluation.reporting import (
    ENTITY_RESOLUTION_QUALITY_AVAILABLE,
    ENTITY_RESOLUTION_QUALITY_NOT_YET_AVAILABLE,
    EVALUATION_MODE_FIXTURE_SMOKE,
    EVALUATION_MODE_MIXED,
    HUMAN_REVIEW_QUALITY_AVAILABLE,
    HUMAN_REVIEW_QUALITY_NOT_YET_AVAILABLE,
    PRODUCT_QUALITY_NOT_YET_AVAILABLE,
    PRODUCT_QUALITY_PARTIALLY_AVAILABLE,
    SCHEMA_MAPPING_QUALITY_AVAILABLE,
    SCHEMA_MAPPING_QUALITY_NOT_YET_AVAILABLE,
    SURVIVORSHIP_QUALITY_AVAILABLE,
    SURVIVORSHIP_QUALITY_NOT_YET_AVAILABLE,
    build_report_data,
    write_json_report,
    write_markdown_report,
)
from evaluation.review_benchmark import run_review_benchmark
from evaluation.row_accounting import run_row_accounting_audit
from evaluation.schema_mapping_benchmark import run_schema_mapping_benchmark
from evaluation.source_b_mapping_benchmark import run_source_b_mapping_benchmark
from evaluation.survivorship_benchmark import run_survivorship_benchmark
from evaluation.threshold_sweep import run_threshold_sweep, threshold_sweep_to_dict
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
        "critical_field_total": result.critical_field_total,
        "critical_field_correct": result.critical_field_correct,
        "critical_field_recall": result.critical_field_recall,
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


def _entity_resolution_benchmark_to_dict(result) -> dict[str, Any]:
    return {
        "split_name": result.split_name,
        "record_count": result.record_count,
        "possible_pair_count": result.possible_pair_count,
        "candidate_pair_count": result.candidate_pair_count,
        "candidate_reduction_ratio": result.candidate_reduction_ratio,
        "labeled_positive_pairs": result.labeled_positive_pairs,
        "labeled_negative_pairs": result.labeled_negative_pairs,
        "candidate_recall": result.candidate_recall,
        "true_positives": result.true_positives,
        "false_positives": result.false_positives,
        "false_negatives": result.false_negatives,
        "true_negatives": result.true_negatives,
        "precision": result.precision,
        "recall": result.recall,
        "f1": result.f1,
        "auto_match_total": result.auto_match_total,
        "auto_match_correct": result.auto_match_correct,
        "auto_match_incorrect": result.auto_match_incorrect,
        "auto_match_precision": result.auto_match_precision,
        "auto_match_coverage": result.auto_match_coverage,
        "false_match_rate": result.false_match_rate,
        "review_count": result.review_count,
        "no_match_count": result.no_match_count,
        "hard_positive_total": result.hard_positive_total,
        "hard_positive_auto_match": result.hard_positive_auto_match,
        "hard_positive_review": result.hard_positive_review,
        "hard_positive_missed": result.hard_positive_missed,
        "hard_negative_total": result.hard_negative_total,
        "hard_negative_false_auto_match": result.hard_negative_false_auto_match,
        "hard_negative_correct_no_match": result.hard_negative_correct_no_match,
        "candidate_miss_count": result.candidate_miss_count,
        "cluster_count": result.cluster_count,
        "failures_by_kind": result.failures_by_kind,
        "passed": result.passed,
    }


def _survivorship_benchmark_to_dict(result) -> dict[str, Any]:
    return {
        "split_name": result.split_name,
        "record_count": result.record_count,
        "canonical_entity_count": result.canonical_entity_count,
        "merged_entity_count": result.merged_entity_count,
        "singleton_entity_count": result.singleton_entity_count,
        "review_excluded_record_count": result.review_excluded_record_count,
        "preserved_conflict_count": result.preserved_conflict_count,
        "merge_coherence_total": result.merge_coherence_total,
        "merge_coherence_correct": result.merge_coherence_correct,
        "merge_coherence_rate": result.merge_coherence_rate,
        "cluster_person_purity_total": result.cluster_person_purity_total,
        "cluster_person_purity_correct": result.cluster_person_purity_correct,
        "cluster_person_purity_rate": result.cluster_person_purity_rate,
        "field_comparisons": result.field_comparisons,
        "field_matches": result.field_matches,
        "field_match_rate": result.field_match_rate,
        "conflict_cases": result.conflict_cases,
        "conflict_preserved": result.conflict_preserved,
        "conflict_preservation_rate": result.conflict_preservation_rate,
        "failures_by_kind": result.failures_by_kind,
        "passed": result.passed,
    }


def _review_benchmark_to_dict(result) -> dict[str, Any]:
    return {
        "split_name": result.split_name,
        "review_case_count": result.review_case_count,
        "pending_case_count": result.pending_case_count,
        "deferred_case_count": result.deferred_case_count,
        "unique_records_in_review": result.unique_records_in_review,
        "review_cases_per_1000_records": result.review_cases_per_1000_records,
        "records_in_multiple_review_cases": result.records_in_multiple_review_cases,
        "max_review_cases_for_single_record": result.max_review_cases_for_single_record,
        "largest_review_case_component": result.largest_review_case_component,
        "labeled_review_pairs": result.labeled_review_pairs,
        "oracle_match_decisions": result.oracle_match_decisions,
        "oracle_no_match_decisions": result.oracle_no_match_decisions,
        "oracle_defer_decisions": result.oracle_defer_decisions,
        "oracle_simulated_resolution_application_accuracy": (
            result.oracle_simulated_resolution_application_accuracy
        ),
        "oracle_simulated_match_application_safety_rate": (
            result.oracle_simulated_match_application_safety_rate
        ),
        "duplicate_membership_violations": result.duplicate_membership_violations,
        "unresolved_unsafe_merge_violations": result.unresolved_unsafe_merge_violations,
        "no_match_transitive_merge_violations": result.no_match_transitive_merge_violations,
        "unauthorized_severe_conflict_merges": result.unauthorized_severe_conflict_merges,
        "human_match_without_provenance_violations": (
            result.human_match_without_provenance_violations
        ),
        "contradiction_count": result.contradiction_count,
        "authorization_blocked_oracle_matches": result.authorization_blocked_oracle_matches,
        "oracle_applied_labeled_pairs": result.oracle_applied_labeled_pairs,
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


def _load_gate_config(config: dict) -> dict:
    if "infrastructure_gates" in config:
        return config["infrastructure_gates"]
    return config["hard_gates"]


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
            gate_config=_load_gate_config(config),
        )
        infrastructure_passed = all_hard_gates_pass(gate_results)
        product_gate_results = []
        product_metrics: dict[str, float] = {}
        product_availability = []
        product_gate_status = "SKIPPED"
        product_passed = True
        product_acceptance = bool(config.get("product_acceptance", True))
        acceptance_mode = str(config.get("acceptance_mode") or "product")
        row_accounting_audit = None
        threshold_sweep = None

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
            print(f"validation_benchmark: {'PASS' if validation_benchmark.passed else 'FAIL'}")
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
            print(f"expected_transformations: {normalization_benchmark.expected_transformations}")
            print(f"correct_transformations: {normalization_benchmark.correct_transformations}")
            print(f"incorrect_transformations: {normalization_benchmark.incorrect_transformations}")
            print(f"missed_transformations: {normalization_benchmark.missed_transformations}")
            print(
                f"normalization_accuracy: "
                f"{normalization_benchmark.normalization_accuracy:.4f} "
                "(real benchmark — whitespace/phone_format only)"
            )
            print(
                f"normalization_benchmark: {'PASS' if normalization_benchmark.passed else 'FAIL'}"
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
            print(f"auto_map_precision: {schema_mapping_benchmark.auto_map_precision:.4f}")
            print(f"review_routing_recall: {schema_mapping_benchmark.review_routing_recall:.4f}")
            print(
                f"schema_mapping_benchmark: {'PASS' if schema_mapping_benchmark.passed else 'FAIL'}"
            )
            print()
        elif schema_mapping_benchmark.error_message:
            print("Real Schema Mapping Benchmark")
            print("------------------")
            print(f"schema_mapping_benchmark: ERROR ({schema_mapping_benchmark.error_message})")
            print()

        source_b_mapping_benchmark = run_source_b_mapping_benchmark()
        if source_b_mapping_benchmark.ran_successfully:
            print("Real Source B Schema Mapping Benchmark")
            print("------------------")
            print(f"layout_count: {source_b_mapping_benchmark.layout_count}")
            print(f"labeled_column_count: {source_b_mapping_benchmark.labeled_column_count}")
            print(f"correct_mappings: {source_b_mapping_benchmark.correct_mappings}")
            print(f"mapping_accuracy: {source_b_mapping_benchmark.mapping_accuracy:.4f}")
            print(f"auto_map_precision: {source_b_mapping_benchmark.auto_map_precision:.4f}")
            print(
                "source_b_mapping_benchmark: "
                f"{'PASS' if source_b_mapping_benchmark.passed else 'FAIL'}"
            )
            print()
        elif source_b_mapping_benchmark.error_message:
            print("Real Source B Schema Mapping Benchmark")
            print("------------------")
            print(f"source_b_mapping_benchmark: ERROR ({source_b_mapping_benchmark.error_message})")
            print()

        entity_resolution_benchmark = None
        entity_resolution_available = False
        if dataset_path is not None:
            entity_resolution_benchmark = run_entity_resolution_benchmark(
                dataset_path=dataset_path,
                split_name="test",
            )
            entity_resolution_available = entity_resolution_benchmark.ran_successfully
            if entity_resolution_available:
                print("Real Entity Resolution Benchmark")
                print("------------------")
                print(f"split: {entity_resolution_benchmark.split_name}")
                print(f"record_count: {entity_resolution_benchmark.record_count}")
                print(f"candidate_pair_count: {entity_resolution_benchmark.candidate_pair_count}")
                print(f"candidate_recall: {entity_resolution_benchmark.candidate_recall:.4f}")
                print(f"precision: {entity_resolution_benchmark.precision:.4f}")
                print(f"recall: {entity_resolution_benchmark.recall:.4f}")
                print(f"f1: {entity_resolution_benchmark.f1:.4f}")
                print(
                    f"auto_match_precision: {entity_resolution_benchmark.auto_match_precision:.4f}"
                )
                print(f"false_match_rate: {entity_resolution_benchmark.false_match_rate:.4f}")
                print(
                    "entity_resolution_benchmark: "
                    f"{'PASS' if entity_resolution_benchmark.passed else 'FAIL'}"
                )
                print()
            elif entity_resolution_benchmark.error_message:
                print("Real Entity Resolution Benchmark")
                print("------------------")
                print(
                    "entity_resolution_benchmark: ERROR "
                    f"({entity_resolution_benchmark.error_message})"
                )
                print()

        survivorship_benchmark = None
        survivorship_available = False
        if dataset_path is not None:
            survivorship_benchmark = run_survivorship_benchmark(
                dataset_path=dataset_path,
                split_name="test",
            )
            survivorship_available = survivorship_benchmark.ran_successfully
            if survivorship_available:
                print("Real Survivorship Benchmark")
                print("------------------")
                print(f"split: {survivorship_benchmark.split_name}")
                print(f"record_count: {survivorship_benchmark.record_count}")
                print(f"canonical_entity_count: {survivorship_benchmark.canonical_entity_count}")
                print(f"merge_coherence_rate: {survivorship_benchmark.merge_coherence_rate:.4f}")
                print(f"field_match_rate: {survivorship_benchmark.field_match_rate:.4f}")
                print(
                    "conflict_preservation_rate: "
                    f"{survivorship_benchmark.conflict_preservation_rate:.4f}"
                )
                print(
                    f"survivorship_benchmark: {'PASS' if survivorship_benchmark.passed else 'FAIL'}"
                )
                print()
            elif survivorship_benchmark.error_message:
                print("Real Survivorship Benchmark")
                print("------------------")
                print(f"survivorship_benchmark: ERROR ({survivorship_benchmark.error_message})")
                print()

        review_benchmark = None
        review_available = False
        if dataset_path is not None:
            print("Real Human Review Benchmark")
            print("------------------")
            print("running oracle-simulated review (authorization + survivorship)...")
            review_benchmark = run_review_benchmark(
                dataset_path=dataset_path,
                split_name="test",
            )
            review_available = review_benchmark.ran_successfully
            if review_available:
                print(f"split: {review_benchmark.split_name}")
                print(f"review_case_count: {review_benchmark.review_case_count}")
                print(f"unique_records_in_review: {review_benchmark.unique_records_in_review}")
                print(
                    "review_cases_per_1000_records: "
                    f"{review_benchmark.review_cases_per_1000_records:.2f}"
                )
                print(f"labeled_review_pairs: {review_benchmark.labeled_review_pairs}")
                print(
                    "oracle_applied_labeled_pairs: "
                    f"{review_benchmark.oracle_applied_labeled_pairs} "
                    "(labeled pairs where production policy allowed the oracle decision)"
                )
                print(
                    "authorization_blocked_oracle_matches: "
                    f"{review_benchmark.authorization_blocked_oracle_matches} "
                    "(safety abstention; not an application-accuracy error; not a product gate)"
                )
                print(
                    "oracle_simulated_resolution_application_accuracy: "
                    f"{review_benchmark.oracle_simulated_resolution_application_accuracy:.4f} "
                    "(applied oracle decisions only; blocked MATCH excluded from denominator; "
                    "NOT real human accuracy)"
                )
                print(
                    "oracle_simulated_match_application_safety_rate: "
                    f"{review_benchmark.oracle_simulated_match_application_safety_rate:.4f} "
                    "(workflow safety under applied oracle MATCH; NOT real human accuracy)"
                )
                print(
                    "duplicate_membership_violations: "
                    f"{review_benchmark.duplicate_membership_violations}"
                )
                print(
                    "unresolved_unsafe_merge_violations: "
                    f"{review_benchmark.unresolved_unsafe_merge_violations} "
                    "(hard safety invariant; must be 0)"
                )
                print(
                    "no_match_transitive_merge_violations: "
                    f"{review_benchmark.no_match_transitive_merge_violations} "
                    "(hard safety invariant; must be 0)"
                )
                print(
                    "unauthorized_severe_conflict_merges: "
                    f"{review_benchmark.unauthorized_severe_conflict_merges} "
                    "(hard safety invariant; must be 0)"
                )
                print(
                    "human_match_without_provenance_violations: "
                    f"{review_benchmark.human_match_without_provenance_violations} "
                    "(hard safety invariant; must be 0)"
                )
                print(f"review_safety_invariants: {'PASS' if review_benchmark.passed else 'FAIL'}")
                print()
            elif review_benchmark.error_message:
                print(f"review_benchmark: ERROR ({review_benchmark.error_message})")
                print()

        if dataset_path is not None:
            row_accounting_audit = run_row_accounting_audit(dataset_path)
            print("Row Accounting Audit")
            print("------------------")
            print(f"discovered_rows: {row_accounting_audit.discovered_rows}")
            print(f"accepted_rows: {row_accounting_audit.accepted_rows}")
            print(f"rejected_rows: {row_accounting_audit.rejected_rows}")
            print(f"unaccounted_rows: {row_accounting_audit.unaccounted_rows}")
            print(f"silent_row_loss_rate: {row_accounting_audit.silent_row_loss_rate:.4f}")
            print(f"row_accounting: {'PASS' if row_accounting_audit.passed else 'FAIL'}")
            print()

            threshold_sweep = run_threshold_sweep(
                dataset_path=dataset_path,
                split_name="validation",
            )
            print("Threshold Sweep (validation only — recommendation only)")
            print("------------------")
            if threshold_sweep.ran_successfully:
                print(f"current_threshold: {threshold_sweep.current_threshold:.2f}")
                print(f"recommended_threshold: {threshold_sweep.recommended_threshold}")
                print(f"recommendation: {threshold_sweep.recommendation_reason}")
            else:
                print(f"threshold_sweep: ERROR ({threshold_sweep.error_message})")
            print()

        product_gate_config = config.get("product_gates", {})
        if product_gate_config.get("enabled", False):
            if dataset_path is None and product_gate_config.get("require_dataset", True):
                product_gate_status = "SKIPPED"
                product_passed = True
            elif dataset_path is not None:
                product_metrics, product_availability = collect_product_metrics(
                    entity_resolution_benchmark=entity_resolution_benchmark,
                    schema_mapping_benchmark=schema_mapping_benchmark,
                    source_b_mapping_benchmark=source_b_mapping_benchmark,
                    normalization_benchmark=normalization_benchmark,
                    survivorship_benchmark=survivorship_benchmark,
                    row_accounting_audit=row_accounting_audit,
                    review_benchmark=review_benchmark,
                )
                try:
                    product_gate_results, _ = evaluate_product_gates(
                        metrics=product_metrics,
                        gate_config=product_gate_config.get("gates", {}),
                    )
                    product_passed = all_hard_gates_pass(product_gate_results)
                    product_gate_status = "PASS" if product_passed else "FAIL"
                except KeyError as exc:
                    product_gate_results = []
                    product_passed = False
                    product_gate_status = "FAIL"
                    print("Product Gate Fail-Closed")
                    print("------------------")
                    print(str(exc))
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
            print(f"critical_field_recall: {schema_mapping_benchmark.critical_field_recall:.4f}")
            print(f"precision: {schema_mapping_benchmark.precision:.4f}")
            print(f"recall: {schema_mapping_benchmark.recall:.4f}")
            print(f"f1: {schema_mapping_benchmark.f1:.4f}")
            print(f"auto_map_precision: {schema_mapping_benchmark.auto_map_precision:.4f}")
            print(
                "review_routing_recall: "
                f"{schema_mapping_benchmark.review_routing_recall:.4f} "
                "(source: labeled mapping benchmark cases)"
            )

        if entity_resolution_available and entity_resolution_benchmark is not None:
            print()
            print("Real Entity Resolution Benchmark Metrics")
            print("------------------")
            print(
                "candidate_recall: "
                f"{entity_resolution_benchmark.candidate_recall:.4f} "
                "(source: golden_dataset_ground_truth/test_split)"
            )
            print(
                "auto_match_recall_on_labeled_positives: "
                f"{entity_resolution_benchmark.auto_match_recall_on_labeled_positives:.4f} "
                "(AUTO_MATCH on labeled positive pairs / all labeled positive pairs)"
            )
            print(
                "false_match_rate: "
                f"{entity_resolution_benchmark.false_match_rate:.4f} "
                "(incorrect AUTO_MATCH / all AUTO_MATCH; NOT fixture false_merge_rate)"
            )

        if survivorship_available and survivorship_benchmark is not None:
            print()
            print("Real Survivorship Benchmark Metrics")
            print("------------------")
            print(
                "merge_coherence_rate: "
                f"{survivorship_benchmark.merge_coherence_rate:.4f} "
                "(source: golden_dataset_ground_truth/test_split)"
            )
            print(
                "field_match_rate: "
                f"{survivorship_benchmark.field_match_rate:.4f} "
                "(normalized survivorship values vs clean/canonical.csv oracle)"
            )
            print(
                "conflict_preservation_rate: "
                f"{survivorship_benchmark.conflict_preservation_rate:.4f} "
                "(distinct member values preserved in conflict metadata)"
            )

        if review_available and review_benchmark is not None:
            print()
            print("Real Human Review Benchmark Metrics")
            print("------------------")
            print(
                "review_case_count: "
                f"{review_benchmark.review_case_count} "
                "(source: entity_resolution REVIEW queue / test split)"
            )
            print(
                "review_cases_per_1000_records: "
                f"{review_benchmark.review_cases_per_1000_records:.2f} "
                "(workload statistic only)"
            )
            print(
                "oracle_applied_labeled_pairs: "
                f"{review_benchmark.oracle_applied_labeled_pairs} "
                "(authorization-accepted oracle decisions only)"
            )
            print(
                "authorization_blocked_oracle_matches: "
                f"{review_benchmark.authorization_blocked_oracle_matches} "
                "(safety abstention; not an application-accuracy error; not a product gate)"
            )
            print(
                "oracle_simulated_resolution_application_accuracy: "
                f"{review_benchmark.oracle_simulated_resolution_application_accuracy:.4f} "
                "(applied oracle decisions only; blocked MATCH excluded from denominator; "
                "NOT real human accuracy)"
            )
            print(
                "oracle_simulated_match_application_safety_rate: "
                f"{review_benchmark.oracle_simulated_match_application_safety_rate:.4f} "
                "(workflow safety under applied oracle MATCH; NOT real human accuracy)"
            )
            print(
                "duplicate_membership_violations: "
                f"{review_benchmark.duplicate_membership_violations} "
                "(hard safety invariant; must be 0)"
            )
            print(
                "unresolved_unsafe_merge_violations: "
                f"{review_benchmark.unresolved_unsafe_merge_violations} "
                "(hard safety invariant; must be 0)"
            )
            print(
                "no_match_transitive_merge_violations: "
                f"{review_benchmark.no_match_transitive_merge_violations} "
                "(hard safety invariant; must be 0)"
            )
            print(
                "unauthorized_severe_conflict_merges: "
                f"{review_benchmark.unauthorized_severe_conflict_merges} "
                "(hard safety invariant; must be 0)"
            )
            print(
                "human_match_without_provenance_violations: "
                f"{review_benchmark.human_match_without_provenance_violations} "
                "(hard safety invariant; must be 0)"
            )

        print()
        print("Infrastructure Hard Gates (Fixture Smoke)")
        print("------------------")
        for result in gate_results:
            status = "PASS" if result.passed else "FAIL"
            print(
                f"{result.name}: {status} "
                f"(actual={result.actual:.4f}, "
                f"operator={result.operator}, "
                f"threshold={result.threshold:.4f})"
            )

        if product_gate_config.get("enabled", False) and product_gate_results:
            print()
            if product_acceptance:
                print("Product Hard Gates (Real Benchmark Metrics)")
            else:
                print("Infrastructure Smoke Gates (not product acceptance)")
            print("------------------")
            for result in product_gate_results:
                status = "PASS" if result.passed else "FAIL"
                print(
                    f"{result.name}: {status} "
                    f"(actual={result.actual:.4f}, "
                    f"operator={result.operator}, "
                    f"threshold={result.threshold:.4f})"
                )

        overall_passed = infrastructure_passed and product_passed

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
            real_entity_resolution_benchmark=(
                _entity_resolution_benchmark_to_dict(entity_resolution_benchmark)
                if entity_resolution_available and entity_resolution_benchmark is not None
                else None
            ),
            real_survivorship_benchmark=(
                _survivorship_benchmark_to_dict(survivorship_benchmark)
                if survivorship_available and survivorship_benchmark is not None
                else None
            ),
            real_review_benchmark=(
                _review_benchmark_to_dict(review_benchmark)
                if review_available and review_benchmark is not None
                else None
            ),
            schema_mapping_quality=(
                SCHEMA_MAPPING_QUALITY_AVAILABLE
                if schema_mapping_available
                else SCHEMA_MAPPING_QUALITY_NOT_YET_AVAILABLE
            ),
            entity_resolution_quality=(
                ENTITY_RESOLUTION_QUALITY_AVAILABLE
                if entity_resolution_available
                else ENTITY_RESOLUTION_QUALITY_NOT_YET_AVAILABLE
            ),
            survivorship_quality=(
                SURVIVORSHIP_QUALITY_AVAILABLE
                if survivorship_available
                else SURVIVORSHIP_QUALITY_NOT_YET_AVAILABLE
            ),
            human_review_quality=(
                HUMAN_REVIEW_QUALITY_AVAILABLE
                if review_available
                else HUMAN_REVIEW_QUALITY_NOT_YET_AVAILABLE
            ),
        )
        report_data["infrastructure_gate_status"] = "PASS" if infrastructure_passed else "FAIL"
        report_data["product_gate_status"] = product_gate_status
        report_data["product_acceptance"] = product_acceptance
        report_data["acceptance_mode"] = acceptance_mode
        report_data["product_metrics"] = product_metrics_summary(
            product_metrics,
            product_availability,
        )
        report_data["product_gates"] = [
            {
                "name": result.name,
                "actual": result.actual,
                "threshold": result.threshold,
                "operator": result.operator,
                "passed": result.passed,
            }
            for result in product_gate_results
        ]
        if row_accounting_audit is not None:
            report_data["row_accounting_audit"] = {
                "discovered_rows": row_accounting_audit.discovered_rows,
                "accepted_rows": row_accounting_audit.accepted_rows,
                "rejected_rows": row_accounting_audit.rejected_rows,
                "unaccounted_rows": row_accounting_audit.unaccounted_rows,
                "silent_row_loss_rate": row_accounting_audit.silent_row_loss_rate,
                "passed": row_accounting_audit.passed,
            }
        if threshold_sweep is not None:
            report_data["threshold_sweep"] = threshold_sweep_to_dict(threshold_sweep)

        output_directory = PROJECT_ROOT / config["reporting"]["output_directory"]
        if config["reporting"]["json"]:
            write_json_report(report_data, output_directory / "report.json")
        if config["reporting"]["markdown"]:
            write_markdown_report(report_data, output_directory / "report.md")

        print()
        print(f"Reports: {output_directory}")
        print()

        hard_gate_status = "PASS" if infrastructure_passed else "FAIL"
        print(f"Infrastructure Hard Gates: {hard_gate_status}")
        print(f"Overall Infrastructure Status: {hard_gate_status}")
        print(f"Acceptance mode: {acceptance_mode}")
        if product_acceptance:
            print(f"Product Hard Gates: {product_gate_status}")
            print(f"Overall Acceptance Status: {'PASS' if overall_passed else 'FAIL'}")
        else:
            print("Product Hard Gates: NOT CLAIMED (infrastructure smoke config)")
            print(f"Infrastructure smoke gates: {product_gate_status}")
            print("Overall Acceptance Status: NOT PRODUCT ACCEPTANCE")
            print(
                "ci-smoke / evaluation.ci.yaml is infrastructure smoke only. "
                "Product candidate-recall >= 0.94 is enforced by configs/evaluation.yaml "
                "on datasets/golden/v0.1.0."
            )
        print(f"Product Quality Evaluation: {product_quality}")
        entity_quality = (
            ENTITY_RESOLUTION_QUALITY_AVAILABLE
            if entity_resolution_available
            else ENTITY_RESOLUTION_QUALITY_NOT_YET_AVAILABLE
        )
        print(f"Entity Resolution Quality: {entity_quality}")
        survivorship_quality = (
            SURVIVORSHIP_QUALITY_AVAILABLE
            if survivorship_available
            else SURVIVORSHIP_QUALITY_NOT_YET_AVAILABLE
        )
        print(f"Survivorship Quality: {survivorship_quality}")
        human_review_quality = (
            HUMAN_REVIEW_QUALITY_AVAILABLE
            if review_available
            else HUMAN_REVIEW_QUALITY_NOT_YET_AVAILABLE
        )
        print(f"Human Review Quality: {human_review_quality}")
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
