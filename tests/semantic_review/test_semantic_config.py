from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from semantic_review.config import (
    SUPPORTED_REASONING_EFFORTS,
    load_semantic_review_config,
)
from semantic_review.errors import SemanticReviewConfigurationError


def test_default_config_is_disabled() -> None:
    config = load_semantic_review_config()
    assert config.enabled is False
    assert config.provider == "openai"
    assert config.model == "gpt-5.6-luna"
    assert config.reasoning_effort == "low"
    assert config.max_live_calls == 25
    assert config.max_live_usd == 1.0


def test_minimal_reasoning_effort_is_rejected(tmp_path: Path) -> None:
    source = Path("configs/semantic_review.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(source)
    data["reasoning_effort"] = "minimal"
    path = tmp_path / "semantic_review.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(SemanticReviewConfigurationError, match="reasoning_effort"):
        load_semantic_review_config(path)


def test_temperature_is_rejected(tmp_path: Path) -> None:
    source = Path("configs/semantic_review.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(source)
    data["temperature"] = 0
    path = tmp_path / "semantic_review.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(SemanticReviewConfigurationError, match="temperature"):
        load_semantic_review_config(path)


def test_direct_construction_rejects_minimal_and_unknown_effort() -> None:
    config = load_semantic_review_config()
    with pytest.raises(SemanticReviewConfigurationError, match="reasoning_effort"):
        replace(config, reasoning_effort="minimal")
    with pytest.raises(SemanticReviewConfigurationError, match="reasoning_effort"):
        replace(config, reasoning_effort="not-an-effort")
    for effort in SUPPORTED_REASONING_EFFORTS:
        updated = replace(config, reasoning_effort=effort)
        assert updated.reasoning_effort == effort


def test_environment_does_not_override_reasoning_effort(monkeypatch) -> None:
    monkeypatch.setenv("SEMANTIC_REVIEW_REASONING_EFFORT", "minimal")
    monkeypatch.setenv("REASONING_EFFORT", "minimal")
    config = load_semantic_review_config()
    assert config.reasoning_effort == "low"


def test_cli_has_no_reasoning_effort_override() -> None:
    source = Path("scripts/suggest_human_review.py").read_text(encoding="utf-8")
    assert "--reasoning-effort" not in source
    assert "reasoning_effort" not in source


def test_default_yaml_has_no_temperature_keys() -> None:
    source = Path("configs/semantic_review.yaml").read_text(encoding="utf-8")
    assert "temperature" not in source
    assert "send_temperature" not in source
