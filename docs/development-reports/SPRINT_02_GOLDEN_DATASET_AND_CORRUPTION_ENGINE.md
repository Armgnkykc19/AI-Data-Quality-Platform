# Sprint 02 — Golden Dataset & Corruption Engine

## Objective

Create a synthetic, versioned, deterministic, reproducible golden dataset with controlled corruptions and ground truth for future parser, normalization, schema mapping, and entity-resolution evaluation.

## Scope Delivered

- Canonical customer schema configuration (`configs/canonical_schema.yaml`)
- Dataset and corruption configuration (`configs/dataset.yaml`, `configs/corruptions.yaml`)
- Deterministic clean-base generator (10,000 canonical persons by default)
- Modular corruption engine with auditable corruption history
- Source variants:
  - **Source A** — formatting noise (case, unicode, whitespace, phone/company formats)
  - **Source B** — schema variation (column aliases/order, missing values, extra columns)
  - **Source C** — semantic noise (typos, duplicates, field conflicts, stacked corruptions)
- Hard positives and hard negatives
- Malformed CSV fixtures with documented error categories
- Ground truth stored separately from source CSVs
- Person-level split metadata with leakage checks
- Dataset manifest with SHA-256 hashes and corruption counts
- CLI commands for generation, corruption application, validation, and one-command build
- Evaluation harness integration for oracle/sanity-check dataset contract validation
- Unit and integration tests

## Out of Scope (Preserved)

- React UI, FastAPI product endpoints
- Authentication, organizations, RBAC, multi-tenancy
- Validation engine, normalization engine, schema mapping engine
- Entity-resolution engine, confidence scoring, LLM merge logic

## Generated Dataset Summary (seed=42, v0.1.0)

| Artifact | Count |
|---|---:|
| Canonical records | 10,000 |
| Source A records | 10,000 |
| Source B records | 10,000 |
| Source C records | 10,778 |
| Hard positive records | 400 |
| Hard negative records | 400 |
| Duplicate groups | 778 |
| Positive pairs | 978 |
| Hard-negative pairs | 200 |
| Corruption events | 27,694 |

### Corruption Distribution

| Corruption Type | Count |
|---|---:|
| missing_value | 12,216 |
| case_change | 3,498 |
| whitespace | 3,452 |
| unicode_turkish | 931 |
| phone_format | 823 |
| duplicate | 778 |
| punctuation | 689 |
| abbreviation | 642 |
| typo | 2,218 |
| field_conflict | 1,929 |
| email_corruption | 518 |

## Commands

```bash
python scripts/build_golden_dataset.py --config configs/dataset.yaml
python scripts/generate_dataset.py --config configs/dataset.yaml
python scripts/generate_corruptions.py --config configs/corruptions.yaml
python scripts/validate_dataset.py --dataset datasets/golden/v0.1.0
python -m evaluation.run --dataset datasets/golden/v0.1.0
```

## Validation Results

| Check | Result |
|---|---|
| Ruff | PASS |
| Pytest | 42 passed |
| Dataset validation CLI | PASS |
| Evaluation sanity checks | PASS (oracle/sanity-check only) |
| Evaluation mode | FIXTURE_SMOKE (fixture metrics only) |
| Sprint 01 evaluation harness | PASS (infrastructure smoke) |

## Key Design Decisions

1. **Ground truth isolation** — `person_id` mappings and pair labels live under `ground_truth/` and are not written into source CSV inputs.
2. **Deterministic generation** — All randomness uses configured seeds via `random.Random(seed)`; same seed/config yields identical file hashes.
3. **Atomic build** — Dataset generation writes to a temporary directory and replaces the final output only on success.
4. **Git strategy** — Large CSV outputs and full ground-truth logs are gitignored; manifest, schema/config snapshots, malformed fixtures, and regeneration commands remain version-controlled.
5. **Evaluation integration** — `--dataset` runs contract/sanity checks only; hard gates evaluate fixture smoke metrics under `FIXTURE_SMOKE` mode and do not represent product engine quality.
6. **CI smoke** — GitHub Actions builds and validates a ~200-record golden dataset via `configs/dataset.ci.yaml` using the real CLI path; artifacts land in gitignored `datasets/generated/ci-smoke/`.

## Known Limitations

- Hard-negative generation relies on shared city/attribute similarity heuristics; future sprints may add curated adversarial clusters.
- Source B column layout is selected once per build from a small set of alias templates.
- Malformed fixtures are small static files; parser behavior against them will be evaluated in a later sprint.
- Full golden CSV outputs must be regenerated locally (not committed) due to size and PII-safe synthetic data policy.

## Files Added/Modified

**New packages and scripts**

- `dataset/` — generation, corruption, manifest, validation, build orchestration
- `scripts/build_golden_dataset.py`
- `scripts/generate_dataset.py`
- `scripts/generate_corruptions.py`
- `scripts/validate_dataset.py`

**Configuration**

- `configs/canonical_schema.yaml`
- `configs/dataset.yaml`
- `configs/corruptions.yaml`

**Tests**

- `tests/dataset/` — reproducibility, ground truth integrity, validation, malformed fixtures

**Other**

- `evaluation/run.py` — optional `--dataset` sanity-check mode
- `.gitignore` — golden dataset artifact rules
- `datasets/golden/README.md`

## Definition of Done Status

| Criterion | Status |
|---|---|
| Clean base + Source A/B/C generation | DONE |
| Reproducible golden dataset (hash-level) | DONE |
| Ground truth, duplicates, hard +/- cases | DONE |
| Malformed fixtures | DONE |
| Manifest + data card | DONE |
| Corruption traceability to canonical source | DONE |
| Dataset validation command | DONE |
| All tests pass | DONE |
| Ruff passes | DONE |
| Sprint 01 evaluation preserved | DONE |
| Out-of-scope product layers not added | DONE |
| Sprint report | DONE |
