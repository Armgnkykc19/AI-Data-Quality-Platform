# Sprint 04 — Deterministic Validation & Normalization Engine

## Objective

Answer: which values are valid, which are invalid, and which can be safely transformed into a canonical deterministic representation without guessing.

## Architecture

```
ParsedDataset → ValidationEngine → NormalizationEngine → Revalidation
                     ↓                      ↓
              Validation reports    Transformation audit trail
```

Packages: `validation/`, `normalization/`, `record_quality/`

## Core Invariant

**Validation ≠ Transformation** — validation never mutates values; normalization produces explicit audit records.

## Config

- `configs/validation.yaml` — rules, severities, required fields, location/cross-field maps
- `configs/normalization.yaml` — TR phone E.164, aliases, suffix mappings, whitespace policies

## CLI

```bash
python scripts/validate_records.py path/to/file.csv
python scripts/normalize_records.py path/to/file.csv
```

## Evaluation

Three metric domains are kept distinct in the evaluation harness:

1. **Fixture smoke hard gates** — infrastructure-only placeholder metrics (`get_fixture_metrics()`).
2. **Real validation benchmark** — labeled deterministic cases in `evaluation/fixtures/validation_benchmark_cases.json`, reported with precision/recall/F1.
3. **Real normalization benchmark** — whitespace + phone_format corruptions from golden dataset corruption log when `--dataset` is passed.

Evaluation mode becomes `MIXED_DETERMINISTIC_NORMALIZATION` when any real benchmark runs successfully. Product quality is `PARTIALLY_AVAILABLE` in that case. Entity resolution and schema mapping remain `NOT_YET_AVAILABLE`.

## Normalization Eligibility Contract

Each `FieldValidationIssue` carries a typed `normalization_eligibility` value:

| Value | Meaning |
|---|---|
| `SAFE` | Deterministic normalization may repair this issue without guessing |
| `NOT_APPLICABLE` | No normalization action applies (e.g. missing required value semantics) |
| `AMBIGUOUS` | Repair would require uncertain interpretation; never auto-normalized |
| `UNSUPPORTED` | No supported safe rule exists in the current engine |

Central mapping lives in `validation/eligibility.py`. Phone E.164 issues override dynamically via `phone_tr_e164_eligibility()`. The normalization pipeline blocks fields with ERROR-level `AMBIGUOUS` or `UNSUPPORTED` eligibility.

Examples:

- `"  test@example.com  "` → `text.noncanonical_whitespace`, eligibility `SAFE`, normalizes to trimmed email
- `"test@@example"` → `email.syntax`, eligibility `AMBIGUOUS`, unchanged
- `"0532 123 45 67"` → `phone.tr_e164`, eligibility `SAFE`, normalizes to `+905321234567`
- incomplete phone `"0532123"` → `phone.tr_e164`, eligibility `AMBIGUOUS`, unchanged

## Normalization Benchmark Scope

| Corruption Type | Scope | Reason |
|---|---|---|
| `whitespace` | SUPPORTED_DETERMINISTIC | Trim/collapse is safe |
| `phone_format` | SUPPORTED_DETERMINISTIC | TR E.164 conversion when digits recoverable |
| `typo`, `email_corruption`, `unicode_turkish`, `missing_value`, `field_conflict`, `duplicate`, `case_change`, `punctuation`, `abbreviation` | INTENTIONALLY_NOT_NORMALIZED | Would require guessing, semantic repair, or later ER scope |

## Known Limitations

- Only whitespace and phone_format corruptions are benchmarked as normalizable
- case_change, typo, email_corruption, unicode_turkish are intentionally not auto-repaired
- Fixture hard-gate `normalization_accuracy: 0.999` is infrastructure smoke only; real benchmark accuracy is computed separately
- No schema mapping or entity resolution (Sprint 05+)
## Definition of Done

Validation/normalization engines, CLIs, tests, CI smoke, and real partial normalization metrics implemented.
