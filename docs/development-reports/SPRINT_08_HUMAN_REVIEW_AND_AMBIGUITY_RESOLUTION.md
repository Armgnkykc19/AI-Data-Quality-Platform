# Sprint 08 — Human Review & Ambiguity Resolution

Sprint 08 turns entity-resolution **REVIEW** from a terminal classification into a deterministic, explainable, auditable human-review workflow. It is **domain logic only** — no UI, API, database, or LLM integration.

## Goals

1. Represent ambiguous ER pairs as stable `ReviewCase` objects.
2. Provide deterministic evidence and human-readable explanations (no LLM).
3. Support explicit human outcomes: `MATCH`, `NO_MATCH`, `DEFER`.
4. Preserve original machine decisions and audit every resolution.
5. Integrate human-confirmed `MATCH` into canonical entity construction without weakening REVIEW safety.
6. Detect transitive `MATCH` / `NO_MATCH` contradictions and fail closed.
7. Measure review usefulness and safety via a real benchmark (evaluation only).

## Non-goals

- LLMs, embeddings, vector databases, semantic models, external AI APIs.
- FastAPI, React/UI, PostgreSQL or other persistence layers.
- Authentication, RBAC, multi-tenancy, CRM integrations.
- Automatic reviewer simulation in production.
- Sprint 09 (LLM integration) or Sprint 08B (only after final Sprint 08 audit if gaps remain).

## Why REVIEW is not failure

REVIEW exists because ambiguous pairs must **not** be force-merged. Sprint 08 adds a workflow for humans to resolve ambiguity safely — it does not treat REVIEW volume as a defect to minimize by lowering thresholds or bypassing safety guards.

## Machine vs human decisions

| Layer | Decision names | Meaning |
|---|---|---|
| Entity resolution | `AUTO_MATCH`, `REVIEW`, `NO_MATCH` | Machine scoring + safety guards |
| Human review | `MATCH`, `NO_MATCH`, `DEFER` | Explicit human resolution |

Machine decisions remain preserved on each `ReviewCase`. Human decisions are recorded separately in `ReviewResolution` and `ReviewAuditEntry`. Canonical entities created from human-confirmed merges use `HR-*` cluster IDs and `HumanReviewProvenance`.

## State machine

```
PENDING → MATCH
PENDING → NO_MATCH
PENDING → DEFERRED
```

Resolved cases cannot be re-resolved (fail closed). Invalid transitions raise `InvalidReviewTransitionError`.

## Package layout

| Module | Role |
|---|---|
| `human_review/models.py` | Domain types, workflow state, outcome helpers |
| `human_review/ids.py` | Stable `RC-{a}--{b}` review case IDs |
| `human_review/cases.py` | REVIEW queue → `ReviewCase` generation |
| `human_review/explanation.py` | Deterministic summaries from ER evidence |
| `human_review/workflow.py` | Resolution state machine + audit trail |
| `human_review/constraints.py` | Transitive MATCH/NO_MATCH contradiction checks |
| `human_review/integration.py` | Review-aware clusters + exclusion logic |
| `human_review/reporting.py` | JSON artifacts (gitignored runtime dir) |
| `scripts/manage_human_review.py` | CLI: generate, list, inspect, resolve |
| `evaluation/review_benchmark.py` | Oracle-simulated benchmark (evaluation only) |

## Downstream integration

- **Unresolved** (`PENDING` / `DEFERRED`): records remain excluded from canonical merging (Sprint 07 safety preserved).
- **Human MATCH**: eligible for `HR-*` clusters via `build_review_aware_clusters()`; provenance attached on `CanonicalEntity`.
- **Human NO_MATCH**: recorded as explicit constraint; blocks transitive human MATCH that would connect the pair.
- **Machine AUTO_MATCH**: unchanged; not overwritten by human workflow.

Pass `human_review_outcome` to `build_canonical_entities()` to enable review-aware behavior. Without it, Sprint 07 behavior is unchanged.

## Contradiction handling

Before accepting a human `MATCH`, the workflow checks whether the pair (or its transitive component) would violate an existing human `NO_MATCH`. Conflicts raise `HumanReviewContradictionError` rather than silently merging.

## Determinism guarantees

- Stable review case IDs from ordered record pairs.
- Sorted case generation and audit output.
- No wall-clock timestamps in domain equality paths.
- No golden/oracle data in production modules.

## Known limitations

- Single-resolution workflow only (no reopen/re-appeal in Sprint 08).
- CLI persistence uses explicit JSON artifacts under `human_review/reports/` (gitignored).
- Review benchmark uses oracle-simulated decisions for measurement only.
- Mixed AUTO_MATCH + human MATCH components share `C-*` cluster IDs when both edge types appear in one component.

## Evaluation

Run with the standard harness:

```bash
python -m evaluation.run --dataset datasets/golden/v0.1.0 --malformed-fixtures datasets/golden/v0.1.0/malformed
```

Review metrics are reported separately. Safety invariants (`duplicate_membership_violations = 0`, `unresolved_unsafe_merge_violations = 0`) are checked in the benchmark; they are **not** added to existing product hard gates until thresholds are justified.

## Sprint 09 reserved

LLM-assisted explanation, suggestion, or decision support is explicitly deferred to Sprint 09.
