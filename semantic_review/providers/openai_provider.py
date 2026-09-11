"""OpenAI Responses API adapter. Production ER must not import this module."""

from __future__ import annotations

import os
from typing import Any

from semantic_review.config import SemanticReviewConfig
from semantic_review.cost import parse_provider_usage, suggestion_cost_fields
from semantic_review.errors import (
    SemanticReviewClientError,
    SemanticReviewConfigurationError,
    SemanticReviewError,
    SemanticReviewInvalidOutputError,
    SemanticReviewProviderError,
    SemanticReviewRateLimitError,
    SemanticReviewTimeoutError,
)
from semantic_review.ids import stable_suggestion_id
from semantic_review.models import (
    SemanticReviewRequest,
    SemanticSuggestion,
    SemanticSuggestionType,
)
from semantic_review.prompt import build_provider_input
from semantic_review.schema import parse_provider_payload, responses_text_format

API_KEY_ENV = "OPENAI_API_KEY"


def create_openai_sdk_client(*, api_key: str, timeout: float) -> Any:
    """Isolated SDK construction. Default pytest patches this to forbid live clients."""
    from openai import OpenAI

    return OpenAI(api_key=api_key, timeout=timeout, max_retries=0)


def _redact(message: str) -> str:
    key = os.environ.get(API_KEY_ENV, "")
    if key and key in message:
        return message.replace(key, "[redacted]")
    return message


class OpenAIProvider:
    provider_name = "openai"
    live = True

    def __init__(self, config: SemanticReviewConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client
        self.calls = 0

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise SemanticReviewConfigurationError(
                "OPENAI_API_KEY is missing. Free/chat subscriptions are not API credentials."
            )
        try:
            self._client = create_openai_sdk_client(
                api_key=api_key,
                timeout=self._config.timeout_seconds,
            )
        except ImportError as exc:
            raise SemanticReviewConfigurationError(
                "The openai package is required for live OpenAI calls. "
                'Install the optional extra: pip install -e ".[llm-openai]"'
            ) from exc
        return self._client

    def suggest(self, request: SemanticReviewRequest) -> SemanticSuggestion:
        self.calls += 1
        client = self._client_or_create()
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "input": build_provider_input(request),
            "max_output_tokens": self._config.max_output_tokens,
            "reasoning": {"effort": self._config.reasoning_effort},
            "text": responses_text_format(),
            "store": False,
            "tools": [],
        }
        try:
            response = client.responses.create(**kwargs)
        except Exception as exc:
            raise _map_openai_error(exc) from exc

        raw_text = getattr(response, "output_text", None)
        if not raw_text:
            raise SemanticReviewInvalidOutputError("Provider returned empty structured output.")
        parsed = parse_provider_payload(
            raw_text,
            expected_review_case_id=request.review_case_id,
            expected_record_a_id=request.record_a_id,
            expected_record_b_id=request.record_b_id,
        )
        usage = parse_provider_usage(getattr(response, "usage", None))
        returned_model = getattr(response, "model", None)
        provider_request_id = getattr(response, "id", None)
        return SemanticSuggestion(
            suggestion_id=stable_suggestion_id(
                request_id=request.request_id,
                suggestion=parsed["suggestion"],
                attempt_count=1,
                fingerprint=request.request_fingerprint,
            ),
            request_id=request.request_id,
            review_case_id=request.review_case_id,
            record_a_id=request.record_a_id,
            record_b_id=request.record_b_id,
            suggestion=SemanticSuggestionType(parsed["suggestion"]),
            reason_codes=parsed["reason_codes"],
            explanation=parsed["explanation"],
            provider=self.provider_name,
            requested_model=self._config.model,
            returned_model=returned_model,
            model_snapshot=returned_model,
            provider_request_id=provider_request_id,
            prompt_version=request.prompt_version,
            prompt_template_hash=request.prompt_template_hash,
            request_schema_version=request.request_schema_version,
            response_schema_version=request.response_schema_version,
            request_fingerprint=request.request_fingerprint,
            attempt_count=1,
            latency_ms=0,
            **suggestion_cost_fields(
                model=self._config.model,
                pricing=self._config.pricing,
                live=True,
                usage=usage,
            ),
            pricing_version=self._config.pricing.version,
            failure_code=None,
            created_at_utc=request.created_at_utc,
            live=True,
            result_source="live_openai",
        )


def _map_openai_error(exc: Exception) -> SemanticReviewError:
    message = _redact(str(exc))
    type_name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if isinstance(exc, TimeoutError) or "Timeout" in type_name:
        return SemanticReviewTimeoutError(message)
    if isinstance(exc, ConnectionError) or "Connection" in type_name:
        return SemanticReviewProviderError(message)
    if "RateLimit" in type_name or status == 429:
        return SemanticReviewRateLimitError(message)
    if "Authentication" in type_name or "Permission" in type_name or status in {401, 403}:
        return SemanticReviewConfigurationError(message)
    if status is not None and 400 <= int(status) < 500:
        return SemanticReviewClientError(message)
    if status is not None and int(status) >= 500:
        return SemanticReviewProviderError(message)
    module_name = type(exc).__module__
    if module_name.startswith("openai") or "APIStatus" in type_name or "APIConnection" in type_name:
        return SemanticReviewProviderError(message)
    if "API" in type_name and status is None:
        return SemanticReviewProviderError(message)
    return SemanticReviewClientError(message)
