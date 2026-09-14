from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from review_application.errors import ReviewPersistenceConfigurationError
from review_persistence.config import (
    DEFAULT_REVIEW_PERSISTENCE_CONFIG,
    PROJECT_ROOT,
    SUPPORTED_JOURNAL_MODES,
    ReviewPersistenceConfig,
    load_review_persistence_config,
)


def _write_config(tmp_path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "database_path": "storage/review_queue.db",
        "busy_timeout_ms": 5000,
        "journal_mode": "WAL",
    }
    payload.update(overrides)
    path = tmp_path / "review_persistence.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_shipped_config_matches_the_documented_defaults() -> None:
    config = load_review_persistence_config()

    assert config.database_path == PROJECT_ROOT / "storage" / "review_queue.db"
    assert config.busy_timeout_ms == 5000
    assert config.journal_mode == "WAL"


def test_relative_path_is_anchored_to_the_project_not_the_cwd(tmp_path: Path) -> None:
    # A CLI run from another directory must not create a second queue.
    config = load_review_persistence_config(_write_config(tmp_path))
    assert config.database_path.is_absolute()
    assert config.database_path == PROJECT_ROOT / "storage" / "review_queue.db"


def test_absolute_path_is_left_alone(tmp_path: Path) -> None:
    absolute = tmp_path / "elsewhere" / "queue.db"
    config = load_review_persistence_config(_write_config(tmp_path, database_path=str(absolute)))
    assert config.database_path == absolute


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_empty_database_path_is_rejected(tmp_path: Path, empty: object) -> None:
    with pytest.raises(ReviewPersistenceConfigurationError, match="database_path"):
        load_review_persistence_config(_write_config(tmp_path, database_path=empty))


@pytest.mark.parametrize("value", [0, -1, -5000])
def test_non_positive_busy_timeout_is_rejected(tmp_path: Path, value: int) -> None:
    with pytest.raises(ReviewPersistenceConfigurationError, match="busy_timeout_ms"):
        load_review_persistence_config(_write_config(tmp_path, busy_timeout_ms=value))


def test_non_integer_busy_timeout_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReviewPersistenceConfigurationError, match="busy_timeout_ms"):
        load_review_persistence_config(_write_config(tmp_path, busy_timeout_ms="soon"))


@pytest.mark.parametrize("mode", ["OFF", "WAL2", "DROP TABLE review_cases", "wal;"])
def test_unsupported_journal_mode_is_rejected(tmp_path: Path, mode: str) -> None:
    # journal_mode reaches SQL by interpolation, so the allow-list is a
    # security boundary as much as a correctness one.
    with pytest.raises(ReviewPersistenceConfigurationError, match="journal_mode"):
        load_review_persistence_config(_write_config(tmp_path, journal_mode=mode))


@pytest.mark.parametrize("mode", sorted(SUPPORTED_JOURNAL_MODES))
def test_supported_journal_modes_load(tmp_path: Path, mode: str) -> None:
    config = load_review_persistence_config(_write_config(tmp_path, journal_mode=mode))
    assert config.journal_mode == mode


def test_journal_mode_is_normalized_to_upper_case(tmp_path: Path) -> None:
    config = load_review_persistence_config(_write_config(tmp_path, journal_mode="wal"))
    assert config.journal_mode == "WAL"


def test_validation_is_eager_on_direct_construction(tmp_path: Path) -> None:
    with pytest.raises(ReviewPersistenceConfigurationError):
        ReviewPersistenceConfig(
            database_path=tmp_path / "q.db",
            busy_timeout_ms=0,
            journal_mode="WAL",
        )


def test_busy_timeout_seconds_conversion(tmp_path: Path) -> None:
    config = ReviewPersistenceConfig(
        database_path=tmp_path / "q.db",
        busy_timeout_ms=2500,
        journal_mode="WAL",
    )
    assert config.busy_timeout_seconds == 2.5


def test_missing_config_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_review_persistence_config(tmp_path / "absent.yaml")


def test_config_declares_no_evaluation_or_tuning_material() -> None:
    source = DEFAULT_REVIEW_PERSISTENCE_CONFIG.read_text(encoding="utf-8").lower()
    for token in ("person_id", "oracle", "ground_truth", "final_holdout", "auto_match"):
        assert token not in source
