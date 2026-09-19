/**
 * Synthetic Sprint 11 payloads for tests.
 *
 * Every value here is invented. No golden dataset, no generated dataset, no
 * `final_holdout`, and no record copied from anywhere: the record ids are
 * sequential placeholders and the one customer-shaped field the API publishes
 * -- `blocking_key` -- carries an obviously synthetic token rather than an
 * email address or a phone fragment. Nothing in this file is, or resembles,
 * personal data.
 *
 * Each fixture is written with `satisfies`, so it is checked against the
 * public DTO without being widened to it. A fixture that drifts from the
 * contract fails `npm run typecheck` rather than failing a test later for a
 * reason that looks unrelated.
 *
 * The set is deliberately small: one pending case, one terminal case, the
 * variants Phase A actually asserts on, and nothing kept "in case".
 */

import type {
  HealthResponse,
  ResolveReviewCaseRequest,
  ResolveReviewCaseResponse,
  ReviewCaseDetail,
  ReviewCaseListResponse,
  ReviewCaseSummary,
  ReviewEventRead,
  SemanticSuggestionRead,
} from '../../api/types';

export const PENDING_CASE_ID = 'RC-0000000000000001';
export const DEFERRED_CASE_ID = 'RC-0000000000000002';

export const pendingCaseSummary = {
  review_case_id: PENDING_CASE_ID,
  record_a_id: 'REC-A-0001',
  record_b_id: 'REC-B-0001',
  status: 'PENDING',
  machine_decision: 'REVIEW',
  machine_score: 0.8125,
  version: 1,
  created_at_utc: '2026-09-18T09:00:00Z',
  updated_at_utc: '2026-09-18T09:00:00Z',
} satisfies ReviewCaseSummary;

export const deferredCaseSummary = {
  review_case_id: DEFERRED_CASE_ID,
  record_a_id: 'REC-A-0002',
  record_b_id: 'REC-B-0002',
  status: 'DEFERRED',
  machine_decision: 'REVIEW',
  machine_score: 0.7,
  version: 2,
  created_at_utc: '2026-09-18T09:00:00Z',
  updated_at_utc: '2026-09-18T11:30:00Z',
} satisfies ReviewCaseSummary;

export const caseListResponse = {
  items: [pendingCaseSummary, deferredCaseSummary],
  count: 2,
  total: 2,
  limit: 50,
  offset: 0,
} satisfies ReviewCaseListResponse;

/** The filtered-empty page: nothing on this page, and nothing matching either. */
export const emptyCaseListResponse = {
  items: [],
  count: 0,
  total: 0,
  limit: 50,
  offset: 0,
} satisfies ReviewCaseListResponse;

/**
 * A pending case with both kinds of evidence and no resolution yet.
 *
 * `resolution` is null and `missing_evidence_notes` is populated, because
 * those are the two shapes a detail view has to handle without special-casing.
 */
export const pendingCaseDetail = {
  review_case_id: PENDING_CASE_ID,
  record_a_id: 'REC-A-0001',
  record_b_id: 'REC-B-0001',
  status: 'PENDING',
  machine_decision: 'REVIEW',
  machine_score: 0.8125,
  auto_match_threshold: 0.88,
  review_threshold: 0.62,
  machine_reason: 'Score fell between the review and auto-match thresholds.',
  human_summary: 'Names agree; email domains differ.',
  missing_evidence_notes: ['No phone number on either record.'],
  blocking_reasons: [{ reason_type: 'EMAIL_EXACT_BLOCK', blocking_key: 'synthetic-block-key-0001' }],
  supporting_evidence: [
    {
      evidence_type: 'LAST_NAME_EXACT',
      field_name: 'last_name',
      strength: 'STRONG',
      contribution: 0.31,
      description: 'Exact match on last_name.',
    },
  ],
  conflicting_evidence: [
    {
      conflict_type: 'EMAIL_DIFFERENT',
      field_name: 'email',
      severity: 'SEVERE',
      penalty: 0.25,
      description: 'Both records have different non-empty email addresses.',
    },
  ],
  resolution: null,
  version: 1,
  created_at_utc: '2026-09-18T09:00:00Z',
  updated_at_utc: '2026-09-18T09:00:00Z',
} satisfies ReviewCaseDetail;

/**
 * A terminal case: the DEFER decision recorded, and the version advanced.
 *
 * Note the vocabularies do not match and are not meant to: the human decision
 * is `DEFER` while the status it produced is `DEFERRED`.
 */
export const deferredCaseDetail = {
  ...pendingCaseDetail,
  review_case_id: DEFERRED_CASE_ID,
  record_a_id: 'REC-A-0002',
  record_b_id: 'REC-B-0002',
  status: 'DEFERRED',
  machine_score: 0.7,
  missing_evidence_notes: [],
  resolution: {
    human_decision: 'DEFER',
    reviewer_id: 'local-reviewer',
    resolution_sequence: 1,
    downstream_action: 'remain_excluded_from_unsafe_canonical_merge',
  },
  version: 2,
  updated_at_utc: '2026-09-18T11:30:00Z',
} satisfies ReviewCaseDetail;

export const caseCreatedEvent = {
  event_id: 1,
  event_type: 'CASE_CREATED',
  occurred_at_utc: '2026-09-18T09:00:00Z',
  resolution_sequence: null,
  reviewer_id: null,
  suggestion_id: null,
  is_resolution: false,
} satisfies ReviewEventRead;

export const semanticRecordedEvent = {
  event_id: 2,
  event_type: 'SEMANTIC_SUGGESTION_RECORDED',
  occurred_at_utc: '2026-09-18T10:00:00Z',
  resolution_sequence: null,
  reviewer_id: null,
  suggestion_id: 'ss-0000000000000001',
  is_resolution: false,
} satisfies ReviewEventRead;

export const deferredResolutionEvent = {
  event_id: 3,
  event_type: 'DEFERRED',
  occurred_at_utc: '2026-09-18T11:30:00Z',
  resolution_sequence: 1,
  reviewer_id: 'local-reviewer',
  suggestion_id: null,
  is_resolution: true,
} satisfies ReviewEventRead;

export const eventHistory = [
  caseCreatedEvent,
  semanticRecordedEvent,
  deferredResolutionEvent,
] satisfies ReviewEventRead[];

/** A suggestion the provider actually produced. Advisory all the same. */
export const advisorySuggestion = {
  suggestion_id: 'ss-0000000000000001',
  suggestion: 'SUGGEST_NO_MATCH',
  reason_codes: ['EMAIL_DOMAIN_CONFLICT'],
  provider: 'fake',
  requested_model: 'test-model',
  failure_code: null,
  live: false,
  created_at_utc: '2026-09-18T10:00:00Z',
  advisory: true,
} satisfies SemanticSuggestionRead;

/** The failure variant: the provider did not answer, and that is recorded too. */
export const failedSuggestion = {
  ...advisorySuggestion,
  suggestion_id: 'ss-0000000000000002',
  suggestion: 'PROVIDER_FAILURE',
  reason_codes: [],
  failure_code: 'PROVIDER_TIMEOUT',
  created_at_utc: '2026-09-18T10:05:00Z',
} satisfies SemanticSuggestionRead;

/**
 * What a successful resolution returns.
 *
 * `event.event_id` is null here and that is faithful, not an oversight: the
 * database assigns the id during the write and the service returns the event
 * it handed to storage.
 */
export const resolveResponse = {
  case: deferredCaseDetail,
  event: { ...deferredResolutionEvent, event_id: null },
} satisfies ResolveReviewCaseResponse;

/**
 * The same case after a MATCH decision: terminal status, version advanced.
 *
 * Phase D needs a before/after pair for one case id, because the whole point
 * of the post-resolution refetch is that the second `GET` of the same URL
 * answers differently from the first.
 */
export const matchedCaseDetail = {
  ...pendingCaseDetail,
  status: 'MATCH',
  resolution: {
    human_decision: 'MATCH',
    reviewer_id: null,
    resolution_sequence: 1,
    downstream_action: 'include_in_canonical_merge',
  },
  version: 2,
  updated_at_utc: '2026-09-18T12:00:00Z',
} satisfies ReviewCaseDetail;

/** The durable history row the match produced, with the id storage assigned. */
export const matchResolutionEvent = {
  event_id: 4,
  event_type: 'MATCH',
  occurred_at_utc: '2026-09-18T12:00:00Z',
  resolution_sequence: 1,
  reviewer_id: null,
  suggestion_id: null,
  is_resolution: true,
} satisfies ReviewEventRead;

/**
 * What `POST .../resolve` answers for that match.
 *
 * `event.event_id` is null, faithfully: the service returns the event it
 * handed to storage, and the id is assigned during the write. A client that
 * wants stable ids re-reads the history endpoint, which is exactly what the
 * workspace does.
 */
export const matchResolveResponse = {
  case: matchedCaseDetail,
  event: { ...matchResolutionEvent, event_id: null },
} satisfies ResolveReviewCaseResponse;

export const healthResponse = { status: 'ok' } satisfies HealthResponse;

/**
 * A resolution request carrying every field the API accepts.
 *
 * Kept alongside the omitted-reviewer variant below because the two are not
 * interchangeable to the contract test: only this one exercises the full
 * property set.
 */
export const resolveRequestWithReviewer = {
  decision: 'DEFER',
  expected_version: 1,
  reviewer_id: 'local-reviewer',
} satisfies ResolveReviewCaseRequest;

/** `reviewer_id` omitted entirely, which the API reads as "none". */
export const resolveRequestWithoutReviewer = {
  decision: 'NO_MATCH',
  expected_version: 3,
} satisfies ResolveReviewCaseRequest;

/**
 * Case-B variants, distinguishable from case A at a glance.
 *
 * The cross-case tests need to assert that nothing belonging to case A is
 * rendered while case B is selected, which only works if the two carry
 * visibly different content.
 */
export const caseBEventHistory = [
  { ...caseCreatedEvent, event_id: 11, occurred_at_utc: '2026-09-17T08:00:00Z' },
  { ...deferredResolutionEvent, event_id: 12, reviewer_id: 'case-b-reviewer' },
] satisfies ReviewEventRead[];

export const caseBSuggestion = {
  ...advisorySuggestion,
  suggestion_id: 'ss-000000000000000b',
  suggestion: 'SUGGEST_MATCH',
  reason_codes: ['CASE_B_ONLY_REASON'],
} satisfies SemanticSuggestionRead;

/** The advisory outcome that carries no verdict either way. */
export const insufficientEvidenceSuggestion = {
  ...advisorySuggestion,
  suggestion_id: 'ss-000000000000000c',
  suggestion: 'INSUFFICIENT_EVIDENCE',
  reason_codes: [],
} satisfies SemanticSuggestionRead;

/** A live provider call, so `live: true` has a fixture behind it. */
export const liveSuggestion = {
  ...advisorySuggestion,
  suggestion_id: 'ss-000000000000000d',
  suggestion: 'SUGGEST_MATCH',
  provider: 'openai',
  requested_model: 'test-model-live',
  live: true,
} satisfies SemanticSuggestionRead;
