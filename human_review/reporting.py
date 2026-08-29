from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from entity_resolution.models import (
    EntityRecord,
    MatchDecision,
    MatchDecisionType,
    PairComparison,
    RecordPair,
    ResolutionResult,
    ResolutionSummary,
)
from human_review.errors import HumanReviewReportError
from human_review.models import (
    HumanReviewDecision,
    HumanReviewOutcome,
    ReviewAuditEntry,
    ReviewBlockingReason,
    ReviewCase,
    ReviewConflictEvidence,
    ReviewEvidence,
    ReviewResolution,
    ReviewStatus,
    ReviewWorkflowState,
)

REVIEW_REPORT_SCHEMA_VERSION = "1.0.0"
REVIEW_REPORT_ARTIFACT_TYPE = "human_review_outcome"
SUPPORTED_SCHEMA_VERSIONS = frozenset({REVIEW_REPORT_SCHEMA_VERSION})


@dataclass(frozen=True)
class LoadedHumanReviewReport:
    outcome: HumanReviewOutcome
    entity_records: tuple[EntityRecord, ...]
    resolution: ResolutionResult
    entity_resolution_config_path: str | None
    payload: dict[str, Any]


def workflow_state_to_dict(state: ReviewWorkflowState) -> dict[str, Any]:
    return {
        "cases": [case.to_dict() for case in state.cases],
        "audit_trail": [entry.to_dict() for entry in state.audit_trail],
        "next_resolution_sequence": state.next_resolution_sequence,
    }


def entity_records_to_dict(records: list[EntityRecord] | tuple[EntityRecord, ...]) -> list[dict]:
    return [
        {
            "record_id": record.record_id,
            "source_name": record.source_name,
            "field_values": dict(record.field_values),
        }
        for record in records
    ]


def resolution_snapshot(resolution: ResolutionResult) -> dict[str, Any]:
    return {
        "source_label": resolution.source_label,
        "auto_match_pairs": [
            [decision.pair.record_a_id, decision.pair.record_b_id]
            for decision in resolution.decisions
            if decision.decision == MatchDecisionType.AUTO_MATCH
        ],
    }


def _rebuild_resolution(
    records: tuple[EntityRecord, ...],
    snapshot: dict[str, Any],
) -> ResolutionResult:
    decisions: list[MatchDecision] = []
    auto_match_pairs = snapshot.get("auto_match_pairs")
    if not isinstance(auto_match_pairs, list):
        raise HumanReviewReportError("resolution_snapshot.auto_match_pairs must be a list.")
    for pair_ids in auto_match_pairs:
        if not isinstance(pair_ids, list) or len(pair_ids) != 2:
            raise HumanReviewReportError(
                "resolution_snapshot.auto_match_pairs entries must be pairs."
            )
        pair = RecordPair.ordered(str(pair_ids[0]), str(pair_ids[1]))
        comparison = PairComparison(
            pair=pair,
            candidate_reasons=(),
            evidence=(),
            conflicts=(),
            score=1.0,
        )
        decisions.append(
            MatchDecision(
                pair=pair,
                comparison=comparison,
                decision=MatchDecisionType.AUTO_MATCH,
                reason="Persisted AUTO_MATCH snapshot from review generation.",
            )
        )
    return ResolutionResult(
        source_label=str(snapshot.get("source_label") or "human-review-report"),
        records=records,
        candidates=(),
        decisions=tuple(decisions),
        review_queue=(),
        clusters=(),
        summary=ResolutionSummary(
            record_count=len(records),
            possible_pair_count=0,
            candidate_pair_count=0,
            candidate_reduction_ratio=0.0,
            auto_match_count=len(decisions),
            review_count=0,
            no_match_count=0,
            cluster_count=0,
            conflict_guarded_clusters=0,
        ),
    )


def write_review_reports(
    outcome: HumanReviewOutcome,
    *,
    output_directory: Path,
    entity_records: list[EntityRecord] | tuple[EntityRecord, ...] | None = None,
    resolution: ResolutionResult | None = None,
    entity_resolution_config_path: Path | str | None = None,
) -> Path:
    if entity_records is None or resolution is None:
        raise HumanReviewReportError(
            "Review reports must persist entity records and the generating resolution snapshot."
        )
    output_directory.mkdir(parents=True, exist_ok=True)
    report_path = output_directory / "human_review_report.json"
    payload = {
        "schema_version": REVIEW_REPORT_SCHEMA_VERSION,
        "artifact_type": REVIEW_REPORT_ARTIFACT_TYPE,
        "entity_resolution_config_path": (
            str(entity_resolution_config_path) if entity_resolution_config_path else None
        ),
        "entity_records": entity_records_to_dict(entity_records),
        "resolution_snapshot": resolution_snapshot(resolution),
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


def _require_mapping(payload: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HumanReviewReportError(f"Review report field '{field_name}' must be an object.")
    return payload


def _workflow_from_payload(payload: dict[str, Any]) -> ReviewWorkflowState:
    workflow_payload = _require_mapping(payload.get("workflow_state"), "workflow_state")
    cases_raw = workflow_payload.get("cases")
    if not isinstance(cases_raw, list):
        raise HumanReviewReportError("workflow_state.cases must be a list.")

    cases: list[ReviewCase] = []
    for item in cases_raw:
        case_payload = _require_mapping(item, "case")
        required = (
            "review_case_id",
            "record_a_id",
            "record_b_id",
            "machine_decision",
            "machine_score",
            "auto_match_threshold",
            "review_threshold",
            "machine_reason",
            "status",
        )
        missing = [name for name in required if name not in case_payload]
        if missing:
            raise HumanReviewReportError(
                "Review case is missing required fields: " + ", ".join(missing)
            )
        resolution = None
        if case_payload.get("resolution") is not None:
            resolution_payload = _require_mapping(case_payload["resolution"], "resolution")
            resolution = ReviewResolution(
                review_case_id=str(case_payload["review_case_id"]),
                human_decision=HumanReviewDecision(resolution_payload["human_decision"]),
                reviewer_id=resolution_payload.get("reviewer_id"),
                resolution_sequence=int(resolution_payload["resolution_sequence"]),
                machine_decision=MatchDecisionType(resolution_payload["machine_decision"]),
                machine_reason=str(resolution_payload["machine_reason"]),
                downstream_action=str(resolution_payload["downstream_action"]),
            )
        cases.append(
            ReviewCase(
                review_case_id=str(case_payload["review_case_id"]),
                pair=RecordPair.ordered(case_payload["record_a_id"], case_payload["record_b_id"]),
                record_ids=(str(case_payload["record_a_id"]), str(case_payload["record_b_id"])),
                machine_decision=MatchDecisionType(case_payload["machine_decision"]),
                machine_score=float(case_payload["machine_score"]),
                auto_match_threshold=float(case_payload["auto_match_threshold"]),
                review_threshold=float(case_payload["review_threshold"]),
                machine_reason=str(case_payload["machine_reason"]),
                blocking_reasons=tuple(
                    ReviewBlockingReason(**reason)
                    for reason in case_payload.get("blocking_reasons", [])
                ),
                supporting_evidence=tuple(
                    ReviewEvidence(**evidence)
                    for evidence in case_payload.get("supporting_evidence", [])
                ),
                conflicting_evidence=tuple(
                    ReviewConflictEvidence(**conflict)
                    for conflict in case_payload.get("conflicting_evidence", [])
                ),
                missing_evidence_notes=tuple(case_payload.get("missing_evidence_notes", [])),
                machine_readable_reasons=tuple(case_payload.get("machine_readable_reasons", [])),
                human_summary=str(case_payload.get("human_summary", "")),
                status=ReviewStatus(case_payload["status"]),
                resolution=resolution,
            )
        )
    audit_raw = workflow_payload.get("audit_trail", [])
    if not isinstance(audit_raw, list):
        raise HumanReviewReportError("workflow_state.audit_trail must be a list.")
    audit_trail = tuple(ReviewAuditEntry(**entry) for entry in audit_raw)
    return ReviewWorkflowState(
        cases=tuple(cases),
        audit_trail=audit_trail,
        next_resolution_sequence=int(workflow_payload.get("next_resolution_sequence", 1)),
    )


def load_human_review_report(report_path: Path) -> LoadedHumanReviewReport:
    if not report_path.exists():
        raise HumanReviewReportError(f"Review report not found: {report_path}")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HumanReviewReportError(f"Review report is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HumanReviewReportError("Review report root must be an object.")

    schema_version = str(payload.get("schema_version") or "")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise HumanReviewReportError(
            f"Unsupported review report schema_version '{schema_version}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_SCHEMA_VERSIONS))}."
        )
    artifact_type = payload.get("artifact_type")
    if artifact_type != REVIEW_REPORT_ARTIFACT_TYPE:
        raise HumanReviewReportError(
            f"Review report artifact_type must be '{REVIEW_REPORT_ARTIFACT_TYPE}'."
        )

    records_raw = payload.get("entity_records")
    if not isinstance(records_raw, list) or not records_raw:
        raise HumanReviewReportError("Review report must include a non-empty entity_records list.")
    records: list[EntityRecord] = []
    for item in records_raw:
        record_payload = _require_mapping(item, "entity_record")
        if "record_id" not in record_payload:
            raise HumanReviewReportError("entity_records entries must include record_id.")
        records.append(
            EntityRecord(
                record_id=str(record_payload["record_id"]),
                source_name=str(record_payload.get("source_name") or "unknown"),
                field_values=dict(record_payload.get("field_values") or {}),
            )
        )

    snapshot = payload.get("resolution_snapshot")
    if not isinstance(snapshot, dict):
        raise HumanReviewReportError("Review report must include resolution_snapshot.")

    record_tuple = tuple(records)
    outcome = HumanReviewOutcome(workflow_state=_workflow_from_payload(payload))
    return LoadedHumanReviewReport(
        outcome=outcome,
        entity_records=record_tuple,
        resolution=_rebuild_resolution(record_tuple, snapshot),
        entity_resolution_config_path=(
            str(payload["entity_resolution_config_path"])
            if payload.get("entity_resolution_config_path")
            else None
        ),
        payload=payload,
    )
