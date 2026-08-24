from __future__ import annotations

from pathlib import Path

MALFORMED_FIXTURES: dict[str, dict[str, object]] = {
    "empty_file.csv": {
        "category": "empty_file",
        "description": "Completely empty CSV file with no header and no rows.",
        "content": "",
        "encoding": "utf-8",
    },
    "header_only.csv": {
        "category": "header_only",
        "description": "CSV file containing only a header row.",
        "content": "source_record_id,source_name,first_name,last_name,email\n",
        "encoding": "utf-8",
    },
    "duplicate_header.csv": {
        "category": "duplicate_header",
        "description": "CSV file with repeated header column names.",
        "content": (
            "source_record_id,source_name,first_name,first_name,email\n"
            "hp-000001,hard_positive,Ali,Ali,ali@example.test\n"
        ),
        "encoding": "utf-8",
    },
    "missing_column_row.csv": {
        "category": "missing_column",
        "description": "Row with fewer columns than the header declares.",
        "content": (
            "source_record_id,source_name,first_name,last_name,email\nhp-000001,hard_positive,Ali\n"
        ),
        "encoding": "utf-8",
    },
    "extra_column_row.csv": {
        "category": "extra_column",
        "description": "Row with more columns than the header declares.",
        "content": (
            "source_record_id,source_name,first_name,last_name,email\n"
            "hp-000001,hard_positive,Ali,Yilmaz,ali@example.test,unexpected\n"
        ),
        "encoding": "utf-8",
    },
    "broken_quotes.csv": {
        "category": "broken_quotes",
        "description": "Unbalanced double quotes inside a quoted field.",
        "content": (
            "source_record_id,source_name,company\n"
            'hp-000001,hard_positive,"Anadolu Teknoloji A.S.\n'
        ),
        "encoding": "utf-8",
    },
    "semicolon_delimiter.csv": {
        "category": "alternate_delimiter",
        "description": "Semicolon-delimited file instead of comma-delimited CSV.",
        "content": (
            "source_record_id;source_name;first_name;last_name;email\n"
            "hp-000001;hard_positive;Ali;Yilmaz;ali@example.test\n"
        ),
        "encoding": "utf-8",
    },
    "utf8_turkish.csv": {
        "category": "utf8_encoding",
        "description": "Valid UTF-8 content with Turkish characters.",
        "content": (
            "source_record_id,source_name,first_name,last_name,city\n"
            "hp-000001,hard_positive,Öğuz,Şahin,İstanbul\n"
        ),
        "encoding": "utf-8",
    },
    "latin5_turkish.csv": {
        "category": "alternate_encoding",
        "description": "Turkish content encoded with ISO-8859-9 (latin5).",
        "content": (
            "source_record_id,source_name,first_name,last_name,city\n"
            "hp-000001,hard_positive,Oguz,Sahin,Istanbul\n"
        ),
        "encoding": "latin5",
    },
}


def generate_malformed_fixtures(output_dir: Path) -> dict[str, dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}

    for filename, spec in MALFORMED_FIXTURES.items():
        path = output_dir / filename
        encoding = str(spec["encoding"])
        content = str(spec["content"])
        path.write_text(content, encoding=encoding)
        manifest[filename] = {
            "category": spec["category"],
            "description": spec["description"],
            "encoding": encoding,
            "path": str(path.name),
        }

    readme = output_dir / "README.md"
    lines = [
        "# Malformed Input Fixtures",
        "",
        "Small, documented malformed CSV fixtures for parser and ingestion tests.",
        "",
    ]
    for filename, spec in sorted(MALFORMED_FIXTURES.items()):
        lines.append(f"- `{filename}` ({spec['category']}): {spec['description']}")
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return manifest
