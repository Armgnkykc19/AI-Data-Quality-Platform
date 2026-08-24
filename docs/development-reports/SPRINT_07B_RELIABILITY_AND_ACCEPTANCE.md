# Sprint 7B — Reliability & Acceptance Hardening

Sprint 7B is **not** a new product-feature sprint. It hardens evaluation, acceptance gates, dataset discipline, and benchmark independence across Sprints 01–07.

## Goals

1. Measure real product quality, not fixture smoke alone.
2. Keep benchmarks independent from the code they evaluate.
3. Enforce zero silent row loss (`discovered = accepted + rejected`).
4. Calibrate entity-resolution thresholds on **validation only** (recommendation, no auto-write).
5. Introduce locked `final_holdout` split (not used for tuning).
6. Assign hard-negative pairs atomically across splits.
7. Improve candidate recall via general index-based blocking only.
8. Report critical-field mapping recall separately.
9. Evaluate normalization on semantically valid denominator only.
10. Fail CI when real product gates fail.

## Non-goals

- No fuzzy spelling repair.
- No Golden-specific production rules.
- No oracle-assisted survivorship.
- No automatic production threshold writes from sweep.
- No Sprint 08 work.

## Key modules

| Module | Role |
|---|---|
| `evaluation/product_metrics.py` | Real metric aggregation + product gates |
| `evaluation/row_accounting.py` | Silent row-loss audit |
| `evaluation/threshold_sweep.py` | Validation-only threshold recommendation |
| `evaluation/fixtures/source_b_expected_mappings.json` | Independent Source B ground truth |
| `dataset/splits.py` | 4-way splits + hard-negative atomicity |

## Acceptance

When `evaluation.run --dataset <path>` is used, exit status reflects **both** infrastructure gates and product gates. Missing real metrics fail closed.

## Deferred

- Normalization comma-suffix debt (Sprint 04 micro-improvements) — report only, not in 7B scope.
- Final holdout evaluation — Sprint 15 plan.
