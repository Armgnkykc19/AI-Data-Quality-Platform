from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from semantic_review.errors import SemanticReviewConfigurationError
from semantic_review.schema import REQUEST_SCHEMA_VERSION, RESPONSE_SCHEMA_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEMANTIC_REVIEW_CONFIG = PROJECT_ROOT / "configs" / "semantic_review.yaml"
DEFAULT_PRICING_CONFIG = PROJECT_ROOT / "configs" / "semantic_review_pricing.yaml"

SUPPORTED_PROVIDERS = frozenset({"openai"})
SUPPORTED_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


@dataclass(frozen=True)
class ModelPricing:
    input_usd_per_million: float
    cached_input_usd_per_million: float
    output_usd_per_million: float


@dataclass(frozen=True)
class SemanticReviewPricing:
    version: str
    models: dict[str, ModelPricing]
    provider: str
    model: str
    currency: str
    unit: str
    pricing_type: str
    source_url: str
    verified_date: str


@dataclass(frozen=True)
class SemanticReviewConfig:
    enabled: bool
    provider: str
    model: str
    reasoning_effort: str
    timeout_seconds: float
    max_retries: int
    max_input_tokens: int
    max_output_tokens: int
    prompt_version: str
    request_schema_version: str
    response_schema_version: str
    max_field_chars: int
    max_live_calls: int
    max_live_usd: float
    report_output_directory: Path
    pricing: SemanticReviewPricing
    raw: dict[str, Any]

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise SemanticReviewConfigurationError(
                f"Unsupported provider '{self.provider}'. Sprint 09 implements only openai."
            )
        if self.reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
            raise SemanticReviewConfigurationError(
                f"Unsupported reasoning_effort '{self.reasoning_effort}'. "
                f"Allowed: {', '.join(sorted(SUPPORTED_REASONING_EFFORTS))}."
            )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return data


def load_semantic_review_pricing(path: Path = DEFAULT_PRICING_CONFIG) -> SemanticReviewPricing:
    data = _load_yaml(path)
    version = str(data.get("version") or "").strip()
    if not version:
        raise SemanticReviewConfigurationError("pricing.version is required.")
    if "assumed" in version.lower():
        raise SemanticReviewConfigurationError(
            "Official pricing must not be labelled assumed. Use a verified version date."
        )
    models_raw = data.get("models")
    if not isinstance(models_raw, dict) or not models_raw:
        raise SemanticReviewConfigurationError("pricing.models must be a non-empty mapping.")
    models: dict[str, ModelPricing] = {}
    for model_name, item in models_raw.items():
        if not isinstance(item, dict):
            raise SemanticReviewConfigurationError(
                f"pricing.models.{model_name} must be a mapping."
            )
        required = (
            "input_usd_per_million",
            "cached_input_usd_per_million",
            "output_usd_per_million",
        )
        missing = [key for key in required if key not in item]
        if missing:
            raise SemanticReviewConfigurationError(
                f"pricing.models.{model_name} missing {missing}."
            )
        models[str(model_name)] = ModelPricing(
            input_usd_per_million=float(item["input_usd_per_million"]),
            cached_input_usd_per_million=float(item["cached_input_usd_per_million"]),
            output_usd_per_million=float(item["output_usd_per_million"]),
        )
    metadata_keys = (
        "provider",
        "model",
        "currency",
        "unit",
        "pricing_type",
        "source_url",
        "verified_date",
    )
    metadata = {key: str(data.get(key) or "").strip() for key in metadata_keys}
    if any(not metadata[key] for key in metadata_keys):
        raise SemanticReviewConfigurationError(
            "Pricing metadata requires provider, model, currency, unit, "
            "pricing_type, source_url, and verified_date."
        )
    return SemanticReviewPricing(version=version, models=models, **metadata)


def load_semantic_review_config(
    path: Path = DEFAULT_SEMANTIC_REVIEW_CONFIG,
    *,
    pricing_path: Path = DEFAULT_PRICING_CONFIG,
) -> SemanticReviewConfig:
    data = _load_yaml(path)
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    reasoning_effort = str(data.get("reasoning_effort") or "").strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise SemanticReviewConfigurationError(
            f"Unsupported provider '{provider}'. Sprint 09 implements only openai."
        )
    if not model:
        raise SemanticReviewConfigurationError("model is required.")
    pricing = load_semantic_review_pricing(pricing_path)
    if model not in pricing.models:
        raise SemanticReviewConfigurationError(
            f"No pricing entry for configured model '{model}'. "
            "Unknown models must not inherit GPT-5.6 Luna rates."
        )
    if reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
        raise SemanticReviewConfigurationError(
            f"Unsupported reasoning_effort '{reasoning_effort}'. "
            f"Allowed: {', '.join(sorted(SUPPORTED_REASONING_EFFORTS))}."
        )
    if "temperature" in data or data.get("send_temperature"):
        raise SemanticReviewConfigurationError(
            "Sprint 09 does not send temperature. Remove temperature/send_temperature "
            "from semantic_review.yaml."
        )

    request_schema_version = str(data.get("request_schema_version") or REQUEST_SCHEMA_VERSION)
    response_schema_version = str(data.get("response_schema_version") or RESPONSE_SCHEMA_VERSION)
    if request_schema_version != REQUEST_SCHEMA_VERSION:
        raise SemanticReviewConfigurationError(
            f"Unsupported request_schema_version '{request_schema_version}'."
        )
    if response_schema_version != RESPONSE_SCHEMA_VERSION:
        raise SemanticReviewConfigurationError(
            f"Unsupported response_schema_version '{response_schema_version}'."
        )

    timeout_seconds = float(data.get("timeout_seconds", 15))
    max_retries = int(data.get("max_retries", 1))
    max_input_tokens = int(data.get("max_input_tokens", 10000))
    max_output_tokens = int(data.get("max_output_tokens", 600))
    max_field_chars = int(data.get("max_field_chars", 120))
    if timeout_seconds <= 0 or max_retries < 0 or max_input_tokens <= 0 or max_output_tokens <= 0:
        raise SemanticReviewConfigurationError("Timeout, retry, and token limits must be positive.")
    if max_field_chars <= 0:
        raise SemanticReviewConfigurationError("max_field_chars must be positive.")

    report_directory = Path(str(data.get("report_output_directory") or "semantic_review/reports"))
    if not report_directory.is_absolute():
        report_directory = PROJECT_ROOT / report_directory

    live = data.get("live", {})
    if not isinstance(live, dict):
        raise SemanticReviewConfigurationError("live must be a mapping.")
    max_live_calls = int(live.get("max_calls", 25))
    max_live_usd = float(live.get("max_usd", 1.0))
    if max_live_calls <= 0 or max_live_usd <= 0:
        raise SemanticReviewConfigurationError("live.max_calls and live.max_usd must be positive.")

    return SemanticReviewConfig(
        enabled=bool(data.get("enabled", False)),
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        prompt_version=str(data.get("prompt_version") or "sprint09-v1"),
        request_schema_version=request_schema_version,
        response_schema_version=response_schema_version,
        max_field_chars=max_field_chars,
        max_live_calls=max_live_calls,
        max_live_usd=max_live_usd,
        report_output_directory=report_directory,
        pricing=pricing,
        raw=data,
    )
