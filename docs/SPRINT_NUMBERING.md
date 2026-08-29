# Sprint Numbering

| Sprint | Scope | Status |
|---|---|---|
| Sprint 01–03 | Ingestion, contracts, profiling | Merged |
| Sprint 04 | Deterministic validation & normalization | Merged |
| Sprint 05 | Schema mapping intelligence | Merged |
| Sprint 06 | Entity resolution | Merged |
| Sprint 07 | Survivorship & canonical entity construction | Merged |
| Sprint 7B | Reliability, evaluation, acceptance hardening | Merged |
| Sprint 08 | Human review & ambiguity resolution | Complete |
| Sprint 09 | LLM integration (reserved) | Not started |

Sprint 08 is human review production closeout. Sprint 09 (LLM) has not started.

## Dataset split roles

| Split | Purpose |
|---|---|
| `train` | Development-only if needed |
| `validation` | Threshold analysis / calibration (recommendation only) |
| `test` | Product-quality measurement |
| `final_holdout` | Locked until planned final evaluation (Sprint 15 target). **Do not tune production behavior from this split.** |

## Metric classes

- **Infrastructure / fixture smoke metrics** — parser/CLI/harness health checks only.
- **Real product benchmark metrics** — acceptance gates when `--dataset` is provided.

Fixture PASS must never hide real product FAIL.

Acceptance thresholds and CI vs product labeling are documented in `docs/development-reports/ACCEPTANCE_THRESHOLDS.md`.
