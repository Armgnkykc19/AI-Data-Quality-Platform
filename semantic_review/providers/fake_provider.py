from __future__ import annotations

from semantic_review.cost import TokenUsage, suggestion_cost_fields
from semantic_review.errors import (
    SemanticReviewIdentityMismatchError,
    SemanticReviewInvalidOutputError,
    SemanticReviewRateLimitError,
    SemanticReviewTimeoutError,
)
from semantic_review.ids import stable_suggestion_id
from semantic_review.models import (
    SemanticReviewRequest,
    SemanticSuggestion,
    SemanticSuggestionType,
)
from semantic_review.schema import parse_provider_payload


class FixedSuggestionProvider:
    provider_name = "fake"
    live = False

    def __init__(
        self,
        *,
        suggestion: SemanticSuggestionType,
        reason_codes: tuple[str, ...] = ("FAKE_EVIDENCE",),
        explanation: str = "Offline test double.",
        pricing,
        requested_model: str,
        latency_ms: int = 1,
        input_token_count: int = 10,
        output_token_count: int = 5,
        fail_times: int = 0,
        failure: Exception | None = None,
        mismatch_case_id: str | None = None,
        mismatch_record_a_id: str | None = None,
        mismatch_record_b_id: str | None = None,
        raw_payload: dict | str | None = None,
        live: bool = False,
    ) -> None:
        self._suggestion = suggestion
        self._reason_codes = reason_codes
        self._explanation = explanation
        self._pricing = pricing
        self._requested_model = requested_model
        self._latency_ms = latency_ms
        self._input_token_count = input_token_count
        self._output_token_count = output_token_count
        self._fail_times = fail_times
        self._failure = failure
        self._mismatch_case_id = mismatch_case_id
        self._mismatch_record_a_id = mismatch_record_a_id
        self._mismatch_record_b_id = mismatch_record_b_id
        self._raw_payload = raw_payload
        self.live = live
        self.calls = 0

    def suggest(self, request: SemanticReviewRequest) -> SemanticSuggestion:
        self.calls += 1
        if self._fail_times > 0:
            self._fail_times -= 1
            if self._failure is None:
                raise SemanticReviewTimeoutError("Simulated provider timeout.")
            raise self._failure

        case_id = self._mismatch_case_id or request.review_case_id
        record_a_id = self._mismatch_record_a_id or request.record_a_id
        record_b_id = self._mismatch_record_b_id or request.record_b_id
        if self._raw_payload is not None:
            parsed = parse_provider_payload(
                self._raw_payload,
                expected_review_case_id=request.review_case_id,
                expected_record_a_id=request.record_a_id,
                expected_record_b_id=request.record_b_id,
            )
            suggestion = SemanticSuggestionType(parsed["suggestion"])
            reason_codes = parsed["reason_codes"]
            explanation = parsed["explanation"]
            case_id = parsed["review_case_id"]
            record_a_id = parsed["record_a_id"]
            record_b_id = parsed["record_b_id"]
        else:
            suggestion = self._suggestion
            reason_codes = self._reason_codes
            explanation = self._explanation

        if case_id != request.review_case_id:
            raise SemanticReviewIdentityMismatchError(
                f"Provider review_case_id '{case_id}' does not match request."
            )
        if record_a_id != request.record_a_id or record_b_id != request.record_b_id:
            raise SemanticReviewIdentityMismatchError(
                "Provider record IDs do not match the requested pair order."
            )

        return SemanticSuggestion(
            suggestion_id=stable_suggestion_id(
                request_id=request.request_id,
                suggestion=suggestion.value,
                attempt_count=1,
                fingerprint=request.request_fingerprint,
            ),
            request_id=request.request_id,
            review_case_id=request.review_case_id,
            record_a_id=request.record_a_id,
            record_b_id=request.record_b_id,
            suggestion=suggestion,
            reason_codes=reason_codes,
            explanation=explanation,
            provider=self.provider_name,
            requested_model=self._requested_model,
            returned_model=self._requested_model,
            model_snapshot=self._requested_model,
            provider_request_id=f"fake-{request.request_id}",
            prompt_version=request.prompt_version,
            prompt_template_hash=request.prompt_template_hash,
            request_schema_version=request.request_schema_version,
            response_schema_version=request.response_schema_version,
            request_fingerprint=request.request_fingerprint,
            attempt_count=1,
            latency_ms=self._latency_ms,
            **suggestion_cost_fields(
                model=self._requested_model,
                pricing=self._pricing,
                live=self.live,
                usage=TokenUsage(
                    input_tokens=self._input_token_count,
                    cached_input_tokens=0,
                    output_tokens=self._output_token_count,
                    reasoning_tokens=0,
                    available=True,
                ),
            ),
            pricing_version=self._pricing.version,
            failure_code=None,
            created_at_utc=request.created_at_utc,
            live=self.live,
            result_source="offline_fake_provider" if not self.live else "live_fake_provider",
        )


def match_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.SUGGEST_MATCH,
        reason_codes=("EMAIL_EXACT",),
        explanation="Offline match suggestion.",
        pricing=pricing,
        requested_model=model,
    )


def no_match_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.SUGGEST_NO_MATCH,
        reason_codes=("EMAIL_CONFLICT",),
        explanation="Offline no-match suggestion.",
        pricing=pricing,
        requested_model=model,
    )


def insufficient_evidence_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        reason_codes=("WEAK_EVIDENCE",),
        explanation="Offline abstention.",
        pricing=pricing,
        requested_model=model,
    )


def invalid_schema_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=pricing,
        requested_model=model,
        raw_payload={"unexpected": True},
    )


def timeout_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=pricing,
        requested_model=model,
        fail_times=99,
        failure=SemanticReviewTimeoutError("Simulated timeout."),
    )


def rate_limit_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=pricing,
        requested_model=model,
        fail_times=99,
        failure=SemanticReviewRateLimitError("Simulated rate limit."),
    )


def generic_failure_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=pricing,
        requested_model=model,
        fail_times=99,
        failure=SemanticReviewInvalidOutputError("Simulated invalid output."),
    )


def mismatched_case_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.SUGGEST_MATCH,
        pricing=pricing,
        requested_model=model,
        mismatch_case_id="RC-mismatched",
    )


def mismatched_record_a_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.SUGGEST_MATCH,
        pricing=pricing,
        requested_model=model,
        mismatch_record_a_id="rec-wrong-a",
    )


def mismatched_record_b_provider(pricing, model: str) -> FixedSuggestionProvider:
    return FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.SUGGEST_MATCH,
        pricing=pricing,
        requested_model=model,
        mismatch_record_b_id="rec-wrong-b",
    )
