# Sprint 05 — Schema Mapping Intelligence

## Objective

Deterministic, explainable source-to-canonical column mapping for supported CSV/XLSX datasets.

## Architecture

```
ParsedDataset
    ↓ profile_dataset()
Column Profiles
    ↓ normalize_header()
Header Preprocessing
    ↓ generate_candidates()
Candidate Generation (all mappable canonical fields)
    ↓ collect_evidence()
Evidence Collection (alias, lexical, type, pattern, profile)
    ↓ score_candidate()
Evidence-Based Scoring
    ↓ decide_column_mapping() + detect_collisions()
Decision Engine (AUTO_MAP / REVIEW / UNMAPPED / CONFLICT)
    ↓ MappingPlan
    ↓ apply_mapping_plan()  [AUTO_MAP only by default]
Canonical-shaped Records
    ↓ ValidationEngine / NormalizationEngine (Sprint 04)
```

Package: `schema_mapping/`
Config: `configs/schema_mapping.yaml`
Canonical source of truth: `configs/canonical_schema.yaml`

## Mapping Decisions

| Decision | Meaning |
|---|---|
| `AUTO_MAP` | Strong deterministic evidence, no blocking ambiguity/collision |
| `REVIEW` | Plausible mapping but unsafe for automatic application |
| `UNMAPPED` | Insufficient evidence |
| `CONFLICT` | One-to-one canonical collision |

Safety priority: prefer `REVIEW`/`UNMAPPED` over wrong `AUTO_MAP`.

## Evidence Types

- `EXACT_ALIAS` — strongest header evidence from config aliases
- `LEXICAL_SIMILARITY` — deterministic `difflib` similarity (cannot alone authorize AUTO_MAP)
- `TYPE_COMPATIBILITY` / `TYPE_INCOMPATIBILITY` — Sprint 03 type inference
- `PATTERN_EMAIL` / `PATTERN_PHONE` / `PATTERN_NUMERIC` — Sprint 03 pattern profiling
- `COMPLETENESS` / `UNIQUENESS` — weak profile support

## Thresholds (config-driven)

- `auto_map: 0.90`
- `review: 0.60`
- `ambiguity_margin: 0.08`
- Pattern-dominant AUTO_MAP requires ≥2 rows and ≥0.90 pattern ratio
- **Ambiguity margin is never bypassed by pattern evidence** (margin check is unconditional)

## Sprint 03 Excel Debt — Closed

Extra XLSX cells beyond header width are **rejected** (`inconsistent_column_count`) instead of silently truncated. Trailing blank header cells are trimmed before validation.

## Integration

- `validation/pipeline.py` — builds/applies mapping plan before validation
- `normalization/pipeline.py` — canonical records via mapping plan
- `record_quality/pipeline.py` — full validate → map → normalize → revalidate
- `scripts/map_schema.py` — mapping CLI with optional `--apply`
- `evaluation/schema_mapping_benchmark.py` — real labeled benchmark

## Real Metrics vs Fixture Metrics

| Signal | Source |
|---|---|
| `schema_mapping_accuracy` (fixture 0.99) | Sprint 01 infrastructure smoke only |
| Real mapping accuracy / AUTO_MAP precision / review routing | `schema_mapping_benchmark` |

Evaluation mode: `MIXED` when real benchmarks run. Schema mapping quality: `AVAILABLE`.

## Benchmark

- Labeled fixture benchmark: 13 cases / 50 columns in `evaluation/fixtures/schema_mapping_benchmark_cases.json`
- **Source B benchmark:** `evaluation/source_b_mapping_benchmark.py` — ground truth from `dataset/generator/sources.py` (`SOURCE_B_FIELD_MAP`, `SOURCE_B_COLUMN_SETS`, `source_b_expected_mapping()`), 3 layouts / 34 columns, real CSV ingestion path

Current labeled benchmark: mapping accuracy 1.0, AUTO_MAP precision 1.0, review routing recall 1.0.
Current Source B benchmark: mapping accuracy 1.0, AUTO_MAP precision 1.0.

## Application Safety

`apply_mapping_plan()` applies **AUTO_MAP only**. REVIEW mappings are never applied by default. No public `include_review` bypass exists.

## CLI

```bash
python scripts/map_schema.py input.csv
python scripts/map_schema.py input.csv --apply --output-path datasets/generated/schema-mapping/out.csv
```

## Tests

- `tests/schema_mapping/` — header, alias, evidence, decisions, application, determinism, config validation
- `tests/evaluation/test_schema_mapping_benchmark.py`
- `tests/ingestion/test_excel_parser.py::test_extra_column_row_is_rejected`

## Explicitly Deferred

- LLM semantic mapping
- Entity resolution / duplicate merge
- Human review UI
- CRM/API adapters

## Definition of Done

All Sprint 05 DoD items implemented and validated locally. No commit performed in this sprint pass.

## Next Sprint Direction

Entity resolution on canonical-mapped, validated records with preserved source lineage.
