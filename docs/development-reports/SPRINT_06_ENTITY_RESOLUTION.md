# Sprint 06 — Entity Resolution

## Summary

Sprint 06 introduces deterministic, explainable entity resolution after canonical schema mapping, validation, and normalization. The engine generates blocked candidate pairs, scores evidence, routes AUTO_MATCH / REVIEW / NO_MATCH decisions, and optionally clusters AUTO_MATCH pairs with a transitive conflict guard.

## Architecture

```
ingest → profile → map → validate → normalize → entity_resolution
                                                      ↓
                                            candidates → evidence → decision
                                                      ↓
                                            clusters (conflict-guarded)
```

Package layout:

- `entity_resolution/models.py` — typed contracts
- `entity_resolution/config.py` — YAML policy loader
- `entity_resolution/blocking.py` — indexed multi-block candidate generation
- `entity_resolution/evidence.py` — exact, fuzzy, and conflict evidence
- `entity_resolution/similarity.py` — deterministic stdlib fuzzy similarity
- `entity_resolution/scoring.py` — weighted evidence scoring
- `entity_resolution/decisions.py` — AUTO_MATCH safety policy
- `entity_resolution/clustering.py` — conflict-guarded components
- `entity_resolution/engine.py` — orchestration
- `entity_resolution/records.py` — quality-pipeline record adapter
- `entity_resolution/reporting.py` — JSON/Markdown reports

## Blocking Strategies

| Strategy | Key |
|---|---|
| EMAIL_EXACT_BLOCK | normalized email |
| PHONE_EXACT_BLOCK | normalized phone digits |
| NAME_CITY_BLOCK | last_name + city |
| SURNAME_COMPANY_BLOCK | last_name + company |
| COMPANY_CITY_BLOCK | company + city |

Candidates are unioned and deduplicated with stable `(min_id, max_id)` ordering.

## Decision Semantics

- **AUTO_MATCH** — strong identity evidence (email or phone exact), no severe conflict, not weak-only
- **REVIEW** — plausible but ambiguous or conflicting evidence
- **NO_MATCH** — insufficient compatible evidence

MATCH DECISION != DATA MERGE. No survivorship or destructive merge is performed.

## Ground Truth Isolation

Ground truth from Sprint 02 is used only in `evaluation/ground_truth.py` and benchmarks. Production engine inputs reject forbidden fields such as `person_id`.

## Measured Results (CI Smoke Dataset, test split)

Measured locally on `datasets/generated/ci-smoke/v0.1.0`:

| Metric | Value |
|---|---:|
| Records | 112 |
| Candidate pairs | 237 |
| Candidate reduction ratio | 0.9619 |
| Candidate recall | 1.0000 |
| AUTO_MATCH precision | 1.0000 |
| False AUTO_MATCH | 0 |
| Hard negative false AUTO_MATCH | 0 |

## Known Limitations

- No LLM or embedding matching
- No human review UI
- No destructive merge / survivorship
- `map_schema.py --apply` redundant pipeline removed in Sprint 06 pre-work

## Next Sprint

Sprint 07 — safe merge/survivorship policies on top of REVIEW/AUTO_MATCH decisions.
