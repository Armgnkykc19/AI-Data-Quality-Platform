from __future__ import annotations

from dataset.manifest import generation_fingerprint
from evaluation.ci_dataset import current_ci_config_fingerprint


def _fingerprint_kwargs(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": "0.1.0",
        "seed": 42,
        "record_count": 200,
        "splits": {
            "train_ratio": 0.60,
            "validation_ratio": 0.15,
            "test_ratio": 0.15,
            "final_holdout_ratio": 0.10,
        },
        "sources": {
            "source_b": {"enabled": True, "corruption_profile": "schema_variation"},
            "source_a": {"enabled": True, "corruption_profile": "formatting_noise"},
        },
        "hard_cases": {"hard_positives_count": 10, "hard_negatives_count": 10},
        "canonical_fields": ["person_id", "first_name", "email"],
    }
    payload.update(overrides)
    return payload


def test_generation_fingerprint_is_deterministic() -> None:
    kwargs = _fingerprint_kwargs()
    assert generation_fingerprint(**kwargs) == generation_fingerprint(**kwargs)


def test_generation_fingerprint_is_stable_across_mapping_order() -> None:
    first = generation_fingerprint(**_fingerprint_kwargs())
    second = generation_fingerprint(
        **_fingerprint_kwargs(
            splits={
                "final_holdout_ratio": 0.10,
                "test_ratio": 0.15,
                "train_ratio": 0.60,
                "validation_ratio": 0.15,
            }
        )
    )
    assert first == second


def test_generation_fingerprint_changes_when_seed_changes() -> None:
    original = generation_fingerprint(**_fingerprint_kwargs())
    changed = generation_fingerprint(**_fingerprint_kwargs(seed=43))
    assert original != changed


def test_generation_fingerprint_changes_when_split_changes() -> None:
    original = generation_fingerprint(**_fingerprint_kwargs())
    changed = generation_fingerprint(
        **_fingerprint_kwargs(
            splits={
                "train_ratio": 0.61,
                "validation_ratio": 0.15,
                "test_ratio": 0.14,
                "final_holdout_ratio": 0.10,
            }
        )
    )
    assert original != changed


def test_current_ci_config_fingerprint_is_deterministic() -> None:
    assert current_ci_config_fingerprint() == current_ci_config_fingerprint()


def test_generation_fingerprint_does_not_embed_filesystem_paths() -> None:
    digest = generation_fingerprint(**_fingerprint_kwargs())
    assert ":" not in digest
    assert "\\" not in digest
    assert "/" not in digest
    assert len(digest) == 64
