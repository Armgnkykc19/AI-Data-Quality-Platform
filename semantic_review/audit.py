from __future__ import annotations

import json
from pathlib import Path

from semantic_review.models import SemanticSuggestion

AUDIT_SCHEMA_VERSION = "1.0.0"


def suggestion_audit_payload(suggestion: SemanticSuggestion) -> dict:
    payload = suggestion.to_dict()
    payload["artifact_type"] = "semantic_review_suggestion_audit"
    payload["schema_version"] = AUDIT_SCHEMA_VERSION
    return payload


def write_suggestion_audit(
    suggestion: SemanticSuggestion,
    *,
    output_directory: Path,
) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"{suggestion.suggestion_id}.json"
    payload = suggestion_audit_payload(suggestion)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_suggestion_audit(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
