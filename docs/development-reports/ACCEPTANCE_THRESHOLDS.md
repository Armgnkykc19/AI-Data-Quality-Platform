# Acceptance Thresholds

This document records the certified Sprint 7B / Sprint 08 product-quality bars.

These values match our merged baseline (`64924441c5d618f9d4e6154f5992ede6d5250a3f`). They were **not** raised or lowered during Sprint 08 production closeout.

| Measurement | Certified bar |
|---|---|
| ER candidate recall (product) | ≥ 0.94 |
| Schema mapping accuracy (product) | ≥ 0.95 |
| Critical-field mapping recall | ≥ 0.95 |
| AUTO_MATCH precision | ≥ 0.99 |
| False AUTO_MATCH rate | ≤ 0 |
| Normalization accuracy | ≥ 0.995 |
| Survivorship field-match | ≥ 0.94 |
| Conflict preservation | ≥ 1.0 |
| Silent row loss | ≤ 0 |
| AUTO_MATCH threshold | **0.88** |
| Train / validation / test / final_holdout | 0.60 / 0.15 / 0.15 / 0.10 |
| Hard-negative minima (val / test / holdout) | 5 / 5 / 5 |

## Review safety gates added in Sprint 08 closeout

These counts must be 0 when `--dataset` is supplied:

- unresolved REVIEW records must not appear in merged canonical entities
- human `NO_MATCH` must not be violated by a transitive merge
- severe identity-conflict components must not be merged
- human `MATCH` members in a merged entity must carry review provenance

Authorization-blocked oracle MATCH decisions are **safety abstentions**. They are counted separately (`authorization_blocked_oracle_matches`) and are excluded from oracle application-accuracy denominators. They are not product gates.

## CI smoke vs product acceptance

GitHub CI cannot run the full golden dataset (source CSVs are gitignored). It builds `datasets/generated/ci-smoke` and evaluates with `configs/evaluation.ci.yaml`.

That file is labelled `acceptance_mode: infrastructure_smoke` and `product_acceptance: false`. CI must not print `Overall Acceptance Status: PASS` as a product claim.

CI still enforces the certified 0.94 candidate-recall bar (and the other 7B/08 product bars) on the smoke dataset, plus review-safety zeros. The pytest assertion `candidate_recall >= 0.94` on ci-smoke is retained.

Product acceptance remains:

```bash
python -m evaluation.run --dataset datasets/golden/v0.1.0 --malformed-fixtures datasets/golden/v0.1.0/malformed
```

Do not tune production behavior from `final_holdout`. Do not lower a gate to obtain a green exit code.
