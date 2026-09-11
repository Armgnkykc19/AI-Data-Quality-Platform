from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from semantic_review.config import SemanticReviewPricing
from semantic_review.errors import SemanticReviewConfigurationError
from semantic_review.models import CostMode


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    available: bool


@dataclass(frozen=True)
class CostEstimate:
    uncached_input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    uncached_input_usd: float | None
    cached_input_usd: float | None
    output_usd: float | None
    hypothetical_model_cost_usd: float | None
    actual_provider_cost_usd: float | None
    cost_mode: CostMode
    usage_available: bool


def parse_provider_usage(usage: Any | None) -> TokenUsage:
    if usage is None:
        return TokenUsage(None, None, None, None, available=False)
    payload = usage if isinstance(usage, dict) else None

    def _attr(name: str) -> Any:
        if payload is not None:
            return payload.get(name)
        return getattr(usage, name, None)

    input_tokens = _attr("input_tokens")
    output_tokens = _attr("output_tokens")
    input_details = _attr("input_tokens_details")
    output_details = _attr("output_tokens_details")
    cached = None
    reasoning = None
    if input_details is not None:
        cached = (
            input_details.get("cached_tokens")
            if isinstance(input_details, dict)
            else getattr(input_details, "cached_tokens", None)
        )
    if output_details is not None:
        reasoning = (
            output_details.get("reasoning_tokens")
            if isinstance(output_details, dict)
            else getattr(output_details, "reasoning_tokens", None)
        )
    if input_tokens is None and output_tokens is None and reasoning is None:
        return TokenUsage(None, None, None, None, available=False)
    return TokenUsage(
        input_tokens=None if input_tokens is None else int(input_tokens),
        cached_input_tokens=None if cached is None else int(cached),
        output_tokens=None if output_tokens is None else int(output_tokens),
        reasoning_tokens=None if reasoning is None else int(reasoning),
        available=True,
    )


def _require_model_rates(model: str, pricing: SemanticReviewPricing):
    rates = pricing.models.get(model)
    if rates is None:
        raise SemanticReviewConfigurationError(
            f"No pricing entry for model '{model}' in pricing version {pricing.version}. "
            "Unknown models must not inherit GPT-5.6 Luna rates."
        )
    return rates


def estimate_cost(
    *,
    model: str,
    pricing: SemanticReviewPricing,
    live: bool,
    usage: TokenUsage,
) -> CostEstimate:
    """Estimate USD cost from usage. Unknown models raise; they never use Luna rates.

    Assumption: when the provider reports output_tokens, those tokens already include
    billable reasoning tokens. Reasoning is recorded for observability and is not
    added again on top of output.
    """
    rates = _require_model_rates(model, pricing)
    cost_mode = CostMode.LIVE if live else CostMode.SIMULATED
    if not usage.available:
        return CostEstimate(
            uncached_input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            uncached_input_usd=None,
            cached_input_usd=None,
            output_usd=None,
            hypothetical_model_cost_usd=None,
            actual_provider_cost_usd=0.0 if not live else None,
            cost_mode=cost_mode,
            usage_available=False,
        )

    input_tokens = max(0, usage.input_tokens or 0)
    cached = max(0, usage.cached_input_tokens or 0)
    if cached > input_tokens:
        cached = input_tokens
    uncached = input_tokens - cached
    if usage.output_tokens is not None:
        billed_output = max(0, usage.output_tokens)
    elif usage.reasoning_tokens is not None:
        billed_output = max(0, usage.reasoning_tokens)
    else:
        billed_output = 0
    reasoning = None if usage.reasoning_tokens is None else max(0, usage.reasoning_tokens)

    uncached_usd = (uncached / 1_000_000) * rates.input_usd_per_million
    cached_usd = (cached / 1_000_000) * rates.cached_input_usd_per_million
    output_usd = (billed_output / 1_000_000) * rates.output_usd_per_million
    total = round(uncached_usd + cached_usd + output_usd, 8)
    return CostEstimate(
        uncached_input_tokens=uncached,
        cached_input_tokens=cached,
        output_tokens=billed_output,
        reasoning_tokens=reasoning,
        uncached_input_usd=round(uncached_usd, 8),
        cached_input_usd=round(cached_usd, 8),
        output_usd=round(output_usd, 8),
        hypothetical_model_cost_usd=total,
        actual_provider_cost_usd=total if live else 0.0,
        cost_mode=cost_mode,
        usage_available=True,
    )


def estimate_cost_usd(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: SemanticReviewPricing,
    cached_input_tokens: int = 0,
    reasoning_tokens: int | None = None,
) -> float:
    estimate = estimate_cost(
        model=model,
        pricing=pricing,
        live=False,
        usage=TokenUsage(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            available=True,
        ),
    )
    if estimate.hypothetical_model_cost_usd is None:
        raise SemanticReviewConfigurationError("Cost estimate is unavailable.")
    return estimate.hypothetical_model_cost_usd


def conservative_call_cost_usd(
    model: str,
    pricing: SemanticReviewPricing,
    *,
    max_input_tokens: int,
    max_output_tokens: int,
) -> float:
    return estimate_cost_usd(
        model=model,
        input_tokens=max_input_tokens,
        output_tokens=max_output_tokens,
        pricing=pricing,
    )


def suggestion_cost_fields(
    *,
    model: str,
    pricing: SemanticReviewPricing,
    live: bool,
    usage: TokenUsage,
) -> dict[str, Any]:
    estimate = estimate_cost(model=model, pricing=pricing, live=live, usage=usage)
    return {
        "input_token_count": 0 if usage.input_tokens is None else usage.input_tokens,
        "cached_input_token_count": (
            0 if usage.cached_input_tokens is None else usage.cached_input_tokens
        ),
        "output_token_count": 0 if usage.output_tokens is None else usage.output_tokens,
        "reasoning_token_count": (0 if usage.reasoning_tokens is None else usage.reasoning_tokens),
        "estimated_cost_usd": estimate.actual_provider_cost_usd,
        "actual_provider_cost_usd": estimate.actual_provider_cost_usd,
        "hypothetical_model_cost_usd": estimate.hypothetical_model_cost_usd,
        "cost_mode": estimate.cost_mode,
        "usage_available": estimate.usage_available,
    }
