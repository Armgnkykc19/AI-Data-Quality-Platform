/**
 * The Sprint 11 public HTTP contract, modelled for the browser.
 *
 * These types mirror `review_api/models.py` and nothing deeper. The backend's
 * API surface is already deliberately smaller than the domain behind it --
 * duplicates, persistence metadata, provider telemetry and authorization
 * material are dropped on the way out -- and this file must not widen it back.
 * Nothing here corresponds to `ReviewWorkflowState`, `WorkflowBundle`,
 * `EntityRecord`, `ResolutionResult`, an entity-resolution config, a stored
 * audit payload, a database row, or a semantic provider request. If a field is
 * not published by `review_api`, it has no type here.
 *
 * Two omissions are worth naming, because they look like oversights:
 *
 * `SemanticSuggestionRead` has no `explanation`. Sprint 09 never stores the
 * model's free-form rationale, because model prose can repeat values out of
 * untrusted customer records. A reloaded suggestion carries an empty string,
 * and `tests/review_api/test_openapi.py` asserts the word appears nowhere in
 * the published schema. Declaring the field here would invent data.
 *
 * `ReviewEventRead` has no stored audit payload and no database schema
 * version. Both are persistence-owned, and the first would restate a
 * resolution in a second shape the API does not own.
 *
 * Field names are the wire names, in the backend's snake_case. Renaming them
 * to camelCase would put a translation layer between this file and the
 * contract it exists to pin, and every mismatch would then be a silent
 * `undefined` instead of a type error.
 *
 * Enums are `as const` tuples plus a derived union rather than TypeScript
 * `enum`. The tuple survives to runtime, so filter controls and tests can
 * iterate the exact published vocabulary instead of restating it; the union
 * gives the same exhaustiveness checking an enum would.
 */

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

/**
 * `human_review.models.ReviewStatus`.
 *
 * Note `MATCH`, not `MATCHED`: the terminal success status is spelled exactly
 * like the human decision that produces it. `?status=` is matched against this
 * enum exactly, so a wrong spelling is a 422 rather than an empty list.
 */
export const REVIEW_STATUSES = ['PENDING', 'MATCH', 'NO_MATCH', 'DEFERRED'] as const;
export type ReviewStatus = (typeof REVIEW_STATUSES)[number];

/** `entity_resolution.models.MatchDecisionType` -- what the machine decided. */
export const MATCH_DECISION_TYPES = ['AUTO_MATCH', 'REVIEW', 'NO_MATCH'] as const;
export type MatchDecisionType = (typeof MATCH_DECISION_TYPES)[number];

/**
 * `human_review.models.HumanReviewDecision` -- what a reviewer may submit.
 *
 * A different vocabulary from `ReviewStatus`, and the difference is load
 * bearing: `DEFER` is a decision, `DEFERRED` is the status it produces. The
 * resolve endpoint accepts only the decision spelling and rejects `DEFERRED`
 * outright rather than translating it.
 */
export const HUMAN_REVIEW_DECISIONS = ['MATCH', 'NO_MATCH', 'DEFER'] as const;
export type HumanReviewDecision = (typeof HUMAN_REVIEW_DECISIONS)[number];

/** `review_application.models.ReviewEventType`. */
export const REVIEW_EVENT_TYPES = [
  'CASE_CREATED',
  'SEMANTIC_SUGGESTION_RECORDED',
  'MATCH',
  'NO_MATCH',
  'DEFERRED',
] as const;
export type ReviewEventType = (typeof REVIEW_EVENT_TYPES)[number];

/** `semantic_review.models.SemanticSuggestionType`. Advisory; decides nothing. */
export const SEMANTIC_SUGGESTION_TYPES = [
  'SUGGEST_MATCH',
  'SUGGEST_NO_MATCH',
  'INSUFFICIENT_EVIDENCE',
  'PROVIDER_FAILURE',
] as const;
export type SemanticSuggestionType = (typeof SEMANTIC_SUGGESTION_TYPES)[number];

/** `semantic_review.models.SemanticFailureCode`. Null on a suggestion that succeeded. */
export const SEMANTIC_FAILURE_CODES = [
  'CONFIGURATION_ERROR',
  'PROVIDER_TIMEOUT',
  'PROVIDER_RATE_LIMIT',
  'PROVIDER_UNAVAILABLE',
  'INVALID_STRUCTURED_OUTPUT',
  'RESPONSE_ID_MISMATCH',
  'INPUT_TOO_LARGE',
  'UNSAFE_OR_INELIGIBLE_CASE',
  'BUDGET_EXCEEDED',
] as const;
export type SemanticFailureCode = (typeof SEMANTIC_FAILURE_CODES)[number];

// ---------------------------------------------------------------------------
// Request bounds
//
// Restated here only where the browser must respect them to build a valid
// request. The server remains the authority: these are not revalidated
// client-side, and nothing in the UI may treat them as domain rules.
// ---------------------------------------------------------------------------

/** `review_api.models.DEFAULT_PAGE_LIMIT`. */
export const DEFAULT_PAGE_LIMIT = 50;
/** `review_api.models.MAX_PAGE_LIMIT`. A larger `limit` is a 422. */
export const MAX_PAGE_LIMIT = 200;

/**
 * `review_api.models.MAX_REVIEWER_ID_LENGTH`. Transport hygiene, not a rule.
 *
 * The backend caps the length and deliberately does nothing else: no trim, no
 * case fold, no emptiness rule, because the value is written verbatim into an
 * append-only audit row. A browser input may use this as a `maxLength` so a
 * reviewer is not silently composing a request that will be rejected, and for
 * nothing else. The server remains the authority.
 */
export const MAX_REVIEWER_ID_LENGTH = 256;

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

/** The complete `/health` body. One field, and it never grows. */
export interface HealthResponse {
  status: 'ok';
}

// ---------------------------------------------------------------------------
// Review cases
// ---------------------------------------------------------------------------

/**
 * Why these two records were ever compared.
 *
 * `blocking_key` is customer-derived -- a normalized email address or a phone
 * fragment -- and is the only personal data the API publishes. Treat it as
 * such wherever it is rendered.
 */
export interface BlockingReasonRead {
  reason_type: string;
  blocking_key: string;
}

/** One reason the records look like the same entity. */
export interface SupportingEvidenceRead {
  evidence_type: string;
  field_name: string;
  strength: string;
  contribution: number;
  description: string;
}

/** One reason the records may not be the same entity. */
export interface ConflictingEvidenceRead {
  conflict_type: string;
  field_name: string;
  severity: string;
  penalty: number;
  description: string;
}

/** The human decision already recorded against a case. Null until one is. */
export interface ReviewResolutionRead {
  human_decision: HumanReviewDecision;
  reviewer_id: string | null;
  resolution_sequence: number;
  downstream_action: string;
}

/** One row of the reviewer's queue. Carries no evidence, by design. */
export interface ReviewCaseSummary {
  review_case_id: string;
  record_a_id: string;
  record_b_id: string;
  status: ReviewStatus;
  machine_decision: MatchDecisionType;
  machine_score: number;
  version: number;
  created_at_utc: string;
  updated_at_utc: string;
}

/**
 * Everything a reviewer needs to decide one case.
 *
 * Both thresholds are published because they are what make `machine_score`
 * legible -- a score means something only once a reader knows where it fell
 * relative to them.
 */
export interface ReviewCaseDetail {
  review_case_id: string;
  record_a_id: string;
  record_b_id: string;
  status: ReviewStatus;
  machine_decision: MatchDecisionType;
  machine_score: number;
  auto_match_threshold: number;
  review_threshold: number;
  machine_reason: string;
  human_summary: string;
  missing_evidence_notes: string[];
  blocking_reasons: BlockingReasonRead[];
  supporting_evidence: SupportingEvidenceRead[];
  conflicting_evidence: ConflictingEvidenceRead[];
  resolution: ReviewResolutionRead | null;
  version: number;
  created_at_utc: string;
  updated_at_utc: string;
}

/**
 * A page of the queue.
 *
 * `total` counts every case matching the filter before slicing, so "this page
 * is empty" is distinguishable from "nothing matches".
 */
export interface ReviewCaseListResponse {
  items: ReviewCaseSummary[];
  count: number;
  total: number;
  limit: number;
  offset: number;
}

/** Query parameters for the list endpoint. Every one is optional. */
export interface ListReviewCasesParams {
  /** Exact status match. Omitted entirely when absent -- an empty string is a 422. */
  status?: ReviewStatus;
  /** 1..{@link MAX_PAGE_LIMIT}. Server default is {@link DEFAULT_PAGE_LIMIT}. */
  limit?: number;
  /** >= 0. An offset past the end returns an empty page that still reports `total`. */
  offset?: number;
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

/**
 * One entry of a case's append-only history, oldest first.
 *
 * `event_id` is null on the event returned by the resolve endpoint: the id is
 * assigned during the database write and the service returns the event it
 * handed to storage. A client that needs stable ids reads them here.
 *
 * `is_resolution` is carried rather than inferred from `event_type`, because
 * an advisory suggestion and a human decision both appear in this list and
 * only one of them decided anything.
 */
export interface ReviewEventRead {
  event_id: number | null;
  event_type: ReviewEventType;
  occurred_at_utc: string;
  resolution_sequence: number | null;
  reviewer_id: string | null;
  suggestion_id: string | null;
  is_resolution: boolean;
}

// ---------------------------------------------------------------------------
// Semantic suggestions
// ---------------------------------------------------------------------------

/**
 * A stored Sprint 09 advisory observation. It decided nothing.
 *
 * `advisory` is a constant the API adds and it cannot be false. A case's
 * `status` remains the only statement about what was decided, and no UI may
 * render one of these so that it reads as an outcome.
 */
export interface SemanticSuggestionRead {
  suggestion_id: string;
  suggestion: SemanticSuggestionType;
  reason_codes: string[];
  provider: string;
  requested_model: string;
  failure_code: SemanticFailureCode | null;
  live: boolean;
  created_at_utc: string;
  advisory: true;
}

// ---------------------------------------------------------------------------
// Resolution
// ---------------------------------------------------------------------------

/**
 * One human decision, as a reviewer submits it.
 *
 * Three fields, and the smallness is the security property: everything the
 * Sprint 08 authority reads is loaded server-side, and the request model
 * rejects unrecognised keys rather than ignoring them.
 *
 * `expected_version` is the version the reviewer had in front of them. It is
 * mandatory, never defaulted to the stored value, and never invented or
 * incremented by this client.
 *
 * `reviewer_id` is an unverified audit label, not an authenticated identity.
 * It is recorded verbatim, so it must not be normalised on the way out.
 */
export interface ResolveReviewCaseRequest {
  decision: HumanReviewDecision;
  expected_version: number;
  reviewer_id?: string | null;
}

/** The updated case, plus the history entry appended alongside it. */
export interface ResolveReviewCaseResponse {
  case: ReviewCaseDetail;
  event: ReviewEventRead;
}
