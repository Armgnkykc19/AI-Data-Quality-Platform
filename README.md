# AI Data Quality Platform

AI Data Quality Platform is an AI-assisted data quality and transformation system focused initially on CRM migration and customer data cleanup.

The platform is designed to transform inconsistent Excel and CSV customer data into a canonical, traceable, and import-ready dataset through deterministic validation, normalization, entity resolution, confidence-based decision making, and human review.

## Current Development Stage

The project is currently in its evaluation-first engineering phase.

Before building the production API, frontend, automation workflows, or CRM integrations, the project establishes a reproducible benchmark infrastructure for measuring:

- entity resolution quality,
- candidate recall,
- automatic merge precision,
- false merge rate,
- schema mapping accuracy,
- normalization accuracy,
- review routing quality,
- hard-gate acceptance criteria.

## Engineering Principles

- Evaluation before productization
- Deterministic rules before probabilistic AI
- No irreversible LLM-only merge decisions
- Source data preservation
- Explainable confidence and evidence
- Human review for ambiguous decisions
- Reproducible benchmarks
- Regression-tested development

## Sprint 01

Sprint 01 establishes the initial engineering and evaluation foundation.

Current capabilities include:

- centralized evaluation configuration,
- reusable classification metrics,
- configurable hard gates,
- PASS / FAIL evaluation decisions,
- JSON and Markdown report generation,
- CLI-based evaluation execution,
- deterministic process exit codes,
- automated unit tests,
- Ruff static analysis,
- GitHub Actions continuous integration.

Run the evaluation harness (fixture smoke — not product quality):

```bash
python -m evaluation.run
```

The harness runs in `FIXTURE_SMOKE` mode until real engine metrics exist. Hard-gate PASS validates infrastructure wiring only; product quality evaluation is `NOT_YET_AVAILABLE`.

## Sprint 02

Sprint 02 adds the golden dataset and controlled corruption engine.

Current capabilities include:

- deterministic canonical clean-base generation,
- modular corruption families with auditable history,
- Source A / B / C variants,
- hard positives and hard negatives,
- malformed input fixtures,
- isolated ground truth and person-level splits,
- dataset manifest with SHA-256 hashes,
- dataset build/validate CLI commands.

Build and validate the golden dataset:

```bash
python scripts/build_golden_dataset.py --config configs/dataset.yaml
python scripts/validate_dataset.py --dataset datasets/golden/v0.1.0
python -m evaluation.run --dataset datasets/golden/v0.1.0
```

Run the test suite:

```bash
pytest
```

Run static analysis:

```bash
ruff check .
```

## Project Status

Early development — Sprint 03 (Input Parsing, Profiling & Data Contracts).

## Sprint 03

Sprint 03 adds CSV/XLSX ingestion, row accounting, and deterministic profiling.

Current capabilities include:

- centralized ingestion configuration (`configs/ingestion.yaml`),
- structured ingestion error taxonomy,
- CSV parsing with delimiter/encoding detection,
- XLSX parsing via openpyxl with explicit worksheet metadata,
- zero silent row loss via row accounting invariant,
- column and dataset profiling (completeness, uniqueness, type inference, patterns),
- JSON/Markdown profiling reports,
- CLI dataset profiling,
- real ingestion smoke checks in the evaluation harness (opt-in via `--malformed-fixtures`).

Profile an input file:

```bash
python scripts/profile_dataset.py datasets/golden/v0.1.0/malformed/utf8_turkish.csv
python scripts/profile_dataset.py path/to/file.xlsx --worksheet SheetName
```

Run evaluation with dataset sanity and ingestion smoke:

```bash
python -m evaluation.run --dataset datasets/generated/ci-smoke/v0.1.0 --malformed-fixtures datasets/golden/v0.1.0/malformed
```

The harness still runs in `FIXTURE_SMOKE` mode for hard gates. Ingestion smoke checks are real Sprint 03 signals but are not product-quality entity-resolution metrics.
