# Sprint 09 — Controlled LLM Semantic Intelligence

Sprint 09 adds provider-independent, **advisory** LLM assistance for unresolved human-review cases. It does not replace deterministic entity resolution or human authorization.

Sprint 08 remains Human Review. Sprint 09 is Controlled LLM Semantic Intelligence.

## Role of the LLM

The LLM may only see pairs the deterministic engine left as `REVIEW`. Allowed outputs:

- `SUGGEST_MATCH`
- `SUGGEST_NO_MATCH`
- `INSUFFICIENT_EVIDENCE`
- `PROVIDER_FAILURE` (platform-generated only)

These are not human `MATCH` / `NO_MATCH` / `DEFER` decisions. A suggestion must never be interpreted as an authorized merge, must never create `HR-*` clusters, and must never call survivorship with a human outcome.

GPT-5.6 Luna is the default demo model, not a permanent core dependency. The model name comes from `configs/semantic_review.yaml`. Replacing the provider later means implementing `SemanticReviewProvider` and pointing config at it. Production entity resolution must not import the OpenAI SDK.

## Safety boundaries

- Only unresolved `PENDING` `REVIEW` cases are eligible.
- `AUTO_MATCH` and `NO_MATCH` pairs are never sent.
- Invalid structured output, identity mismatch, timeout, rate limit, and network errors fail closed. The `ReviewCase` stays `PENDING`.
- Schema and identity mismatches are not retried.
- Ground truth, oracle labels, expected decisions, holdout metadata, and ER thresholds are never included in requests.
- Trusted deterministic evidence and untrusted source values are separate prompt sections.
- LLM assistance is **disabled by default**. Disabling it leaves the deterministic product path unchanged.
- Tests and CI must not call a real provider.

## Configuration

`configs/semantic_review.yaml` defaults:

```yaml
enabled: false
provider: openai
model: gpt-5.6-luna
reasoning_effort: low
timeout_seconds: 15
max_retries: 1
max_input_tokens: 10000
max_output_tokens: 600
```

Official GPT-5.6 reasoning efforts: `none`, `low`, `medium`, `high`, `xhigh`, `max`. Sprint 09 default is `low`. `minimal` is rejected. Temperature is not sent.

Live authentication uses only `OPENAI_API_KEY`. Never log or serialize the key.

Estimated cost uses `configs/semantic_review_pricing.yaml` (official short-context rates verified 2026-08-28). Unknown models fail closed instead of inheriting Luna rates. Cache-write and >272K long-context surcharge are documented but not modeled; Sprint 09 requests stay far below that cap.

The OpenAI SDK is an optional extra, not a mandatory dependency:

```bash
pip install -e ".[llm-openai]"
```

## CLI

```bash
python scripts/suggest_human_review.py suggest --report PATH --case-id RC-... --fake
python scripts/suggest_human_review.py inspect --suggestion-id LS-...
python scripts/suggest_human_review.py benchmark --dataset datasets/golden/v0.1.0 --split validation --fake
python scripts/suggest_human_review.py benchmark --scripted
```

`--live` requires `enabled: true`, `OPENAI_API_KEY`, and a configured per-run budget.

`max_live_calls` and `max_usd` are **per CLI/benchmark/service run**, not account-wide or persistent organization budgets. One run that processes N cases shares one `LiveBudget`. A later process starts a new budget.

`max_live_calls` counts **outbound live provider invocations**, including retries. The cap is checked immediately before every live `provider.suggest()`. Offline fake/scripted providers do not consume this budget.

`max_usd` is a **conservative hard per-run live budget**. Before each live call the service reserves worst-case cost using configured `max_input_tokens` (uncached) plus `max_output_tokens` at the configured model rates. The call is refused when `spent + reserved_max_cost > max_usd`. After a successful response with reported usage, the reservation is replaced by actual cost. A failed or usage-unknown live attempt is charged the reservation so it is never treated as free. The Responses API request sends `max_output_tokens` so that reservation has a bounded output.

Exit codes: 0 success, 1 usage, 3 config/report/IO, 4 ineligible/policy, 5 live-mode refusal.

## Evaluation

`evaluation/semantic_review_benchmark.py` is separate from deterministic product hard gates. Empty denominators are `undefined`. Prompt/model selection may use only `validation`. `train`, `test`, `holdout`, and `final_holdout` are refused.

Fake and scripted runs set `model_quality_claim=false` and `live_model_observation=false`. A later live run may set `live_model_observation=true` and report metrics. It must not claim that Luna quality is proven.

This Sprint 09 implementation does not run live Luna.

## Protected baseline

Sprint 09 does not change AUTO_MATCH `0.88`, product gates, dataset splits `0.60 / 0.15 / 0.15 / 0.10`, hard-negative minima `5 / 5 / 5`, locked `final_holdout`, or Sprint 08 Human Review safety.

## Deferred

Multiple real providers, customer model selection, tenant policy, enterprise billing, secret managers, async jobs, and model routing are out of Sprint 09.
