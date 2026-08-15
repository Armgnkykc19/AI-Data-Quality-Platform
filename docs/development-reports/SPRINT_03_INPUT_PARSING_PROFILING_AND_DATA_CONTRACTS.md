# Sprint 03 — Input Parsing, Profiling & Data Contracts

## Objective

Establish a trustworthy ingestion boundary for supported CSV and XLSX files with deterministic parsing, structured errors, zero silent row loss, and dataset/column profiling.

## Architecture

```
configs/ingestion.yaml
ingestion/          # format detection, parsing, row accounting, errors
profiling/          # column/dataset profiling, type inference, patterns
scripts/profile_dataset.py
evaluation/ingestion_checks.py
```

## Ingestion Contract

- **Formats:** `.csv`, `.xlsx`
- **Limits:** 50 MB file, 200k rows, 256 columns (configurable)
- **CSV encodings:** utf-8, utf-8-sig, cp1254, iso-8859-9
- **CSV delimiters:** `,` and `;` with deterministic sample-based detection; single-column files default to comma
- **Excel worksheet policy:** `first` — first worksheet is selected by default; metadata records `worksheet`, `worksheet_selection_policy`, and `available_worksheets`; explicit selection via CLI `--worksheet`
- **Malformed rows:** rejected and accounted (`malformed_row: reject`)
- **Duplicate headers:** fatal error
- **Empty file:** fatal error

## Row Accounting Invariant

`source_data_rows = accepted_rows + rejected_rows`

Header rows are excluded from accounting. Every post-header data row is either accepted or rejected.

## Error Taxonomy

Structured `IngestionError` hierarchy with `code` and `message` fields for API mapping.

## Profiling

Separate from parsing. Column metrics: null/blank/non-null counts, completeness, uniqueness, samples, type inference, pattern evidence.

## CLI

```bash
python scripts/profile_dataset.py path/to/file.csv
python scripts/profile_dataset.py path/to/file.xlsx --worksheet SheetName
```

Exit codes: `0` success, `1` ingestion error, `2` infrastructure error.

## Tests & CI

71 pytest tests. CI adds CSV profile smoke, XLSX profile smoke, and ingestion checks via `--malformed-fixtures`.

## Known Limitations

See final audit report. Notable: `broken_quotes.csv` may parse without rejection; Excel truncates extra columns silently; openpyxl is dev-only dependency.

## Definition of Done

All Sprint 03 ingestion/profiling gates implemented and validated locally. Product-quality entity-resolution gates remain `NOT_YET_AVAILABLE`.

## Next Sprint

Schema mapping, normalization, and entity resolution can build on `ParsedDataset` and profiling evidence.
