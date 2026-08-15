# Malformed Input Fixtures

Small, documented malformed CSV fixtures for parser and ingestion tests.

- `broken_quotes.csv` (broken_quotes): Unclosed double quote before EOF; the affected logical row is rejected (`unclosed_quote`) rather than silently accepted.
- `duplicate_header.csv` (duplicate_header): CSV file with repeated header column names.
- `empty_file.csv` (empty_file): Completely empty CSV file with no header and no rows.
- `extra_column_row.csv` (extra_column): Row with more columns than the header declares.
- `header_only.csv` (header_only): CSV file containing only a header row.
- `latin5_turkish.csv` (alternate_encoding): Turkish content encoded with ISO-8859-9 (latin5).
- `missing_column_row.csv` (missing_column): Row with fewer columns than the header declares.
- `semicolon_delimiter.csv` (alternate_delimiter): Semicolon-delimited file instead of comma-delimited CSV.
- `utf8_turkish.csv` (utf8_encoding): Valid UTF-8 content with Turkish characters.
