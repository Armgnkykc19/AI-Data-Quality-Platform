from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from human_review.models import HumanReviewOutcome, ReviewWorkflowState


def workflow_state_to_dict(state: ReviewWorkflowState) -> dict[str, Any]:
    return {
        "cases": [case.to_dict() for case in state.cases],
        "audit_trail": [entry.to_dict() for entry in state.audit_trail],
        "next_resolution_sequence": state.next_resolution_sequence,
    }


def write_review_reports(
    outcome: HumanReviewOutcome,
    *,
    output_directory: Path,
) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    report_path = output_directory / "human_review_report.json"
    payload = {
        "summary": {
            "review_case_count": len(outcome.cases),
            "pending_count": len(outcome.workflow_state.pending_cases()),
            "deferred_count": len(outcome.workflow_state.deferred_cases()),
            "resolved_match_count": len(outcome.resolved_match_pairs()),
            "resolved_no_match_count": len(outcome.resolved_no_match_pairs()),
        },
        "workflow_state": workflow_state_to_dict(outcome.workflow_state),
    }
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path
