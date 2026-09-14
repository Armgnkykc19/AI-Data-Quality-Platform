"""Configuration for the review queue database.

Follows the loader shape the project already uses (``load_entity_resolution_config``,
``load_semantic_review_config``): a frozen dataclass, eager validation in
``__post_init__``, and a typed configuration error. Kept deliberately small --
this is a single-file SQLite queue, not a database tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from review_application.errors import ReviewPersistenceConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW_PERSISTENCE_CONFIG = PROJECT_ROOT / "configs" / "review_persistence.yaml"

# A journal mode cannot be a bound parameter -- PRAGMA takes a literal -- so the
# value is interpolated into SQL. This allow-list is what keeps that safe, and
# it is limited to the modes this project actually uses: WAL in production,
# DELETE as the SQLite default, MEMORY for in-memory test databases where WAL
# is unavailable.
SUPPORTED_JOURNAL_MODES = frozenset({"WAL", "DELETE", "MEMORY"})


@dataclass(frozen=True)
class ReviewPersistenceConfig:
    database_path: Path
    busy_timeout_ms: int
    journal_mode: str

    def __post_init__(self) -> None:
        if not isinstance(self.database_path, Path) or not str(self.database_path).strip():
            raise ReviewPersistenceConfigurationError("database_path must be a non-empty path.")
        if isinstance(self.busy_timeout_ms, bool) or not isinstance(self.busy_timeout_ms, int):
            raise ReviewPersistenceConfigurationError("busy_timeout_ms must be an integer.")
        if self.busy_timeout_ms <= 0:
            raise ReviewPersistenceConfigurationError(
                f"busy_timeout_ms must be positive; got {self.busy_timeout_ms}."
            )
        if self.journal_mode not in SUPPORTED_JOURNAL_MODES:
            allowed = ", ".join(sorted(SUPPORTED_JOURNAL_MODES))
            raise ReviewPersistenceConfigurationError(
                f"Unsupported journal_mode '{self.journal_mode}'. Allowed: {allowed}."
            )

    @property
    def busy_timeout_seconds(self) -> float:
        """sqlite3.connect takes seconds; the config is stated in milliseconds."""
        return self.busy_timeout_ms / 1000


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return data


def load_review_persistence_config(
    path: Path = DEFAULT_REVIEW_PERSISTENCE_CONFIG,
) -> ReviewPersistenceConfig:
    data = _load_yaml(path)

    raw_path = str(data.get("database_path") or "").strip()
    if not raw_path:
        raise ReviewPersistenceConfigurationError("database_path is required.")
    database_path = Path(raw_path)
    if not database_path.is_absolute():
        # Anchored to the project, not the process working directory, so the
        # queue does not move when a CLI is invoked from another folder.
        database_path = PROJECT_ROOT / database_path

    raw_timeout = data.get("busy_timeout_ms", 5000)
    try:
        busy_timeout_ms = int(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise ReviewPersistenceConfigurationError(
            f"busy_timeout_ms must be an integer; got {raw_timeout!r}."
        ) from exc

    journal_mode = str(data.get("journal_mode") or "WAL").strip().upper()

    return ReviewPersistenceConfig(
        database_path=database_path,
        busy_timeout_ms=busy_timeout_ms,
        journal_mode=journal_mode,
    )
