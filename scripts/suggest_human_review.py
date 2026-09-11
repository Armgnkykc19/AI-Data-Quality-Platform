#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from entity_resolution.config import load_entity_resolution_config  # noqa: E402
from evaluation.semantic_review_benchmark import (  # noqa: E402
    FORBIDDEN_SEMANTIC_SPLITS,
    run_semantic_review_benchmark,
    write_semantic_benchmark_reports,
)
from evaluation.semantic_review_scripted import run_scripted_offline_benchmark  # noqa: E402
from human_review.errors import HumanReviewError, HumanReviewReportError  # noqa: E402
from human_review.reporting import load_human_review_report  # noqa: E402
from human_review.workflow import ReviewWorkflow  # noqa: E402
from semantic_review.audit import load_suggestion_audit  # noqa: E402
from semantic_review.budget import LiveBudget  # noqa: E402
from semantic_review.config import load_semantic_review_config  # noqa: E402
from semantic_review.errors import (  # noqa: E402
    SemanticReviewBudgetError,
    SemanticReviewConfigurationError,
    SemanticReviewError,
    SemanticReviewRoutingError,
)
from semantic_review.providers.fake_provider import insufficient_evidence_provider  # noqa: E402
from semantic_review.service import SemanticReviewService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Advisory LLM suggestions for unresolved human-review cases. "
            "Never resolves cases or creates canonical entities."
        ),
        epilog=(
            "Exit codes: 0 success, 1 usage, 3 config/report/IO, "
            "4 ineligible/policy, 5 live-mode refusal."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    suggest = subparsers.add_parser("suggest", help="Suggest for one review case.")
    suggest.add_argument("--report", type=Path, required=True)
    suggest.add_argument("--case-id", required=True)
    suggest.add_argument("--fake", action="store_true", help="Use the offline fake provider.")
    suggest.add_argument("--live", action="store_true", help="Call the configured OpenAI model.")
    suggest.add_argument("--output-dir", type=Path, default=None)

    inspect = subparsers.add_parser("inspect", help="Inspect a saved suggestion audit record.")
    inspect.add_argument("--suggestion-id", required=True)
    inspect.add_argument("--report-dir", type=Path, default=None)

    benchmark = subparsers.add_parser(
        "benchmark",
        help="Offline or budget-limited live suggestion benchmark (validation split only).",
    )
    benchmark.add_argument("--dataset", type=Path, required=False)
    benchmark.add_argument("--split", default="validation")
    benchmark.add_argument("--fake", action="store_true")
    benchmark.add_argument(
        "--scripted",
        action="store_true",
        help="Run deterministic OFFLINE_SCRIPTED formula/safety scenarios.",
    )
    benchmark.add_argument("--live", action="store_true")
    benchmark.add_argument("--max-cases", type=int, default=25)
    benchmark.add_argument("--output-dir", type=Path, default=None)

    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "semantic_review.yaml",
    )
    parser.add_argument(
        "--entity-resolution-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "entity_resolution.yaml",
    )
    return parser.parse_args()


def _print_suggestion(suggestion) -> None:
    print(f"result_source: {suggestion.result_source}")
    print(f"live: {suggestion.live}")
    print(f"suggestion_id: {suggestion.suggestion_id}")
    print(f"review_case_id: {suggestion.review_case_id}")
    print(f"record_a_id: {suggestion.record_a_id}")
    print(f"record_b_id: {suggestion.record_b_id}")
    print(f"suggestion: {suggestion.suggestion.value}")
    print(f"reason_codes: {','.join(suggestion.reason_codes)}")
    print(f"provider: {suggestion.provider}")
    print(f"requested_model: {suggestion.requested_model}")
    print(f"returned_model: {suggestion.returned_model}")
    print(f"prompt_version: {suggestion.prompt_version}")
    print(f"request_fingerprint: {suggestion.request_fingerprint}")
    print(f"attempt_count: {suggestion.attempt_count}")
    print(f"latency_ms: {suggestion.latency_ms}")
    print(f"input_token_count: {suggestion.input_token_count}")
    print(f"cached_input_token_count: {suggestion.cached_input_token_count}")
    print(f"output_token_count: {suggestion.output_token_count}")
    print(f"reasoning_token_count: {suggestion.reasoning_token_count}")
    print(f"cost_mode: {suggestion.cost_mode.value}")
    print(f"actual_provider_cost_usd: {suggestion.actual_provider_cost_usd}")
    print(f"hypothetical_model_cost_usd: {suggestion.hypothetical_model_cost_usd}")
    print(f"usage_available: {suggestion.usage_available}")
    print(f"failure_code: {suggestion.failure_code}")
    if suggestion.explanation:
        print(f"explanation: {suggestion.explanation}")
    print("ReviewCase was not resolved. Suggestion is advisory only.")


def _select_provider(args, config):
    live = bool(getattr(args, "live", False))
    fake = bool(getattr(args, "fake", False))
    scripted = bool(getattr(args, "scripted", False))
    modes = [
        name
        for name, enabled in (("fake", fake), ("scripted", scripted), ("live", live))
        if enabled
    ]
    if len(modes) > 1:
        print("Choose only one of --fake, --scripted, or --live.")
        return None, None, 1
    if scripted:
        print(
            "Using OFFLINE_SCRIPTED providers. Results test formulas and safety, "
            "not GPT-5.6 Luna quality."
        )
        return "scripted", False, 0
    if not live:
        print("Using offline fake provider fail-safe smoke. Results are not GPT-5.6 Luna quality.")
        return insufficient_evidence_provider(config.pricing, config.model), False, 0
    if not config.enabled:
        print("Live calls are refused because semantic review is disabled.")
        return None, None, 5
    if config.provider != "openai":
        print("Live calls require provider=openai.")
        return None, None, 5
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "Live calls are refused because OPENAI_API_KEY is missing. "
            "Free/chat subscriptions are not API credentials."
        )
        return None, None, 5
    from semantic_review.providers.openai_provider import OpenAIProvider

    print("Using live OpenAI. This consumes API tokens and is advisory only.")
    return OpenAIProvider(config), True, 0


def main() -> int:
    args = parse_args()
    try:
        config = load_semantic_review_config(args.config)
        output_dir = getattr(args, "output_dir", None)
        if output_dir is not None:
            config = replace(config, report_output_directory=output_dir)

        if args.command == "inspect":
            report_dir = args.report_dir or config.report_output_directory
            path = Path(report_dir) / f"{args.suggestion_id}.json"
            payload = load_suggestion_audit(path)
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        provider, live, status = _select_provider(args, config)
        if status != 0:
            return status
        require_enabled = bool(live)

        if args.command == "suggest":
            if provider == "scripted":
                print("Use benchmark --scripted for scripted scenarios.")
                return 1
            loaded = load_human_review_report(args.report)
            workflow = ReviewWorkflow(loaded.outcome.workflow_state)
            case = workflow.state.case_by_id(args.case_id)
            records_by_id = {record.record_id: record for record in loaded.entity_records}
            config_path = (
                Path(loaded.entity_resolution_config_path)
                if loaded.entity_resolution_config_path
                else args.entity_resolution_config
            )
            resolution_config = load_entity_resolution_config(config_path)
            budget = LiveBudget(config) if live else None
            service = SemanticReviewService(
                config,
                provider,
                require_enabled=require_enabled,
                budget=budget,
            )
            routing, suggestion = service.suggest_for_case(
                case,
                records_by_id=records_by_id,
                outcome=loaded.outcome,
                resolution=loaded.resolution,
                entity_resolution_config=resolution_config,
            )
            if suggestion is None:
                reason = routing.reason.value if routing.reason else "ineligible"
                print(f"Skipped: {reason}")
                print(routing.detail)
                return 4
            _print_suggestion(suggestion)
            return 0

        if args.command == "benchmark":
            if getattr(args, "scripted", False) or provider == "scripted":
                result = run_scripted_offline_benchmark(config=config)
                print(result.to_text())
                write_semantic_benchmark_reports(result, config.report_output_directory)
                return 0 if result.ran_successfully else 3
            if args.dataset is None:
                print("benchmark --dataset is required unless --scripted is set.")
                return 1
            split = str(args.split)
            if split in FORBIDDEN_SEMANTIC_SPLITS:
                print(
                    f"Split '{split}' is forbidden for Sprint 09 benchmark/tuning. "
                    "Use validation only."
                )
                return 4
            result = run_semantic_review_benchmark(
                dataset_path=args.dataset,
                split_name=split,
                config=config,
                provider=provider,
                require_enabled=require_enabled,
                max_cases=args.max_cases,
                stratify_labelled=bool(live),
            )
            print(result.to_text())
            write_semantic_benchmark_reports(result, config.report_output_directory)
            if result.stopped_reason == "budget_limit":
                return 5
            return 0 if result.ran_successfully else 3

        return 1
    except SemanticReviewBudgetError as exc:
        print(f"Live semantic-review budget refused: {exc}")
        return 5
    except SemanticReviewConfigurationError as exc:
        print(f"Semantic review configuration error: {exc}")
        return 3
    except SemanticReviewRoutingError as exc:
        print(f"Semantic review ineligible: {exc}")
        return 4
    except HumanReviewReportError as exc:
        print(f"Review report error: {exc}")
        return 3
    except HumanReviewError as exc:
        print(f"Human review error: {exc}")
        return 4
    except SemanticReviewError as exc:
        print(f"Semantic review error: {exc}")
        return 3
    except (OSError, ValueError, KeyError) as exc:
        print(f"Semantic review command failed: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
