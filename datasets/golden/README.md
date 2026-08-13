# Golden Dataset Artifacts

Large generated CSV outputs are intentionally excluded from version control.

Reproduce the default golden dataset locally:

```bash
python scripts/build_golden_dataset.py --config configs/dataset.yaml
python scripts/validate_dataset.py --dataset datasets/golden/v0.1.0
```

Version-controlled artifacts include:

- configuration under `configs/`
- generation and validation code under `dataset/` and `scripts/`
- malformed fixtures (small, documented)
- manifest, schema snapshots, and ground-truth summaries when checked in after generation

Ground truth is stored separately from source CSV files and must never leak into model input files.
