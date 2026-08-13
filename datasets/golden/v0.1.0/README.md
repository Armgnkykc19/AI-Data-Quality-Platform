# Golden Dataset Data Card

- Version: `0.1.0`
- Seed: `42`
- Canonical records: `10000`

## Source Variants

- Source A records: `10000`
- Source B records: `10000`
- Source C records: `10778`
- Hard positive records: `400`
- Hard negative records: `400`

## Ground Truth

- Duplicate groups: `778`
- Positive pairs: `978`
- Hard negative pairs: `200`
- Corruption events: `27694`

## Corruption Distribution

- `abbreviation`: `642`
- `case_change`: `3498`
- `duplicate`: `778`
- `email_corruption`: `518`
- `field_conflict`: `1929`
- `missing_value`: `12216`
- `phone_format`: `823`
- `punctuation`: `689`
- `typo`: `2218`
- `unicode_turkish`: `931`
- `whitespace`: `3452`

## Reproducibility

```bash
python scripts/build_golden_dataset.py --config configs/dataset.yaml
```

Ground truth is derived from canonical clean-base identities and is stored
separately from source CSV files.
