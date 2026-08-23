# Sprint 07 — Survivorship & Canonical Entity Construction

## Summary

Sprint 07 builds deterministic canonical entities on top of Sprint 06 entity resolution. AUTO_MATCH clusters are merged non-destructively using quality-first field survivorship, provenance is recorded per field, and conflicting source values are preserved instead of silently hidden.

## Architecture

```
ingest → profile → map → validate → normalize → entity_resolution → survivorship
                                                                          ↓
                                                            CanonicalEntity + lineage
```

Package layout:

- `survivorship/models.py` — `CanonicalEntity`, `FieldProvenance`, `PreservedFieldConflict`
- `survivorship/config.py` — YAML policy loader
- `survivorship/candidate_quality.py` — deterministic field candidate quality model
- `survivorship/rules.py` — quality-first field selection orchestration
- `survivorship/engine.py` — orchestration over `ResolutionResult`
- `survivorship/lineage.py` — lineage helpers for reporting
- `survivorship/failure_analysis.py` — benchmark failure taxonomy
- `survivorship/reporting.py` — JSON/Markdown reports

## Quality-First Survivorship Policy

Production selection uses Sprint 04 validation evidence only (no oracle):

1. Nonblank / present value
2. Fewer validation errors
3. Fewer validation warnings
4. Lower structural corruption penalty (e.g. `(Merged)` suffix, hyphen spam)
5. Better normalization eligibility
6. Field-specific identity bonus (E.164 phone, known city/district, valid email syntax)
7. Information length (late tie-breaker only)
8. Source priority (late tie-breaker only)
9. Stable `record_id`

### Strategies

| Strategy | Fields | Behavior |
|---|---|---|
| `quality_identity` | email, phone | Quality-first among member values; preserve identity conflicts |
| `quality_first` | text fields | Quality-first selection with conflict preservation |

Legacy `completeness_longest` remains available for compatibility but is not the default.

### Source Priority Semantics

Source priority (`source_a` > `source_b` > `source_c` > hard cases) is a **late tie-breaker only**. It does **not** override clearly higher-quality member values (valid vs invalid, known city vs corrupted suffix, etc.).

## REVIEW Exclusion Contract

- Records participating in **any** REVIEW pair are excluded from canonical output.
- Clusters containing **any** REVIEW-associated record are **not** merged.
- Non-review members of skipped clusters may still become singleton entities when eligible.
- REVIEW records are intentionally omitted — not represented as separate canonical entities.

## Cluster Policy

- Only AUTO_MATCH clusters with 2+ members become merged canonical entities (subject to REVIEW exclusion above)
- Singleton entities are built for unmatched non-review records when enabled
- Field conflicts are preserved in `preserved_conflicts` metadata

## Ground Truth Isolation

Golden `person_id`, `clean/canonical.csv`, and duplicate groups are used only in `evaluation/ground_truth.py` and benchmarks. Engine inputs reject forbidden fields such as `person_id`.

## Measured Results (Golden Dataset, held-out test split)

Measured locally on `datasets/golden/v0.1.0` test split (4,707 source records):

| Metric | Before quality-first | After quality-first |
|---|---:|---:|
| Overall field match rate | 0.9450 | **0.9581** |
| Survivorship-caused mismatches | 330 | **181** |
| Company accuracy | 0.8995 | **0.9192** |
| Address accuracy | 0.9143 | **0.9375** |
| City accuracy | 0.9649 | **0.9838** |
| District accuracy | 0.9719 | **0.9888** |
| Cluster person purity | 1.0000 | 1.0000 |
| Merge coherence | 0.9607 | 0.9607 |
| Conflict preservation | 1.0000 | 1.0000 |
| Provenance accuracy | 1.0000 | 1.0000 |
| False merged entities | 0 | 0 |
| Review-excluded records | 646 | 646 |
| Oracle-recoverable upper bound | — | **0.9740** |

149 of 330 survivorship-caused mismatches were eliminated (45% reduction) using generalizable production rules.

## No-Invention Guarantee

Every selected canonical field value equals a raw member source value (or remains null when no member value exists). No oracle injection. No synthesized values.

## Known Limitations

- 181 residual survivorship-caused mismatches where oracle-equivalent values exist but deterministic quality evidence cannot safely distinguish competing valid forms
- ~2.6% of oracle fields are not recoverable from any member record (upstream corruption)
- No LLM-assisted survivorship
- No human review UI
- No database persistence
- REVIEW pairs remain unresolved by design

## Next Sprint

Future work may add review workflows, API surfaces, or persistence — not in Sprint 07 scope.
