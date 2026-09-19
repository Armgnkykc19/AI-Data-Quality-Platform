/**
 * How the published vocabularies are spelled for a reader.
 *
 * Every map here is exhaustive over its enum by type, so a token added to the
 * contract fails the build rather than reaching the screen shouted in
 * SCREAMING_SNAKE_CASE.
 *
 * Spelling is the only thing these change. Three rules hold throughout, and
 * each exists because breaking it would put a conclusion on screen that no
 * backend field supports:
 *
 * A label never becomes an instruction. `SUGGEST_MATCH` reads "Suggest match",
 * never "Match these records" or "Recommended: match". The suggestion is an
 * advisory observation Sprint 09 recorded; turning it into an imperative would
 * make an advisory panel look like a decision the reviewer is expected to
 * ratify.
 *
 * A label never adds a judgement. A resolution event reads "Resolved as
 * Match", not "Approved"; `DEFERRED` reads "Deferred", not "Paused" or
 * "Postponed". Nothing in the contract says a deferred pair will be revisited,
 * and in fact nothing can reopen it.
 *
 * The two `MATCH` vocabularies stay apart. `ReviewStatus.MATCH` is where a
 * case ended up and `MatchDecisionType.MATCH` — reachable as a machine verdict
 * — is what the deterministic engine concluded. They are different statements
 * about different things, so they get their own maps even though two of the
 * strings coincide.
 */

import type {
  MatchDecisionType,
  ReviewEventType,
  ReviewStatus,
  SemanticSuggestionType,
} from '../api/types';

/** `human_review.models.ReviewStatus` — where the case stands. */
export const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  PENDING: 'Pending',
  MATCH: 'Match',
  NO_MATCH: 'No match',
  DEFERRED: 'Deferred',
};

export function reviewStatusLabel(status: ReviewStatus): string {
  return REVIEW_STATUS_LABELS[status];
}

/** `entity_resolution.models.MatchDecisionType` — what the machine concluded. */
export const MACHINE_DECISION_LABELS: Record<MatchDecisionType, string> = {
  AUTO_MATCH: 'Auto match',
  REVIEW: 'Review',
  NO_MATCH: 'No match',
};

export function machineDecisionLabel(decision: MatchDecisionType): string {
  return MACHINE_DECISION_LABELS[decision];
}

/** `semantic_review.models.SemanticSuggestionType` — what Sprint 09 observed. */
export const SEMANTIC_SUGGESTION_LABELS: Record<SemanticSuggestionType, string> = {
  SUGGEST_MATCH: 'Suggest match',
  SUGGEST_NO_MATCH: 'Suggest no match',
  INSUFFICIENT_EVIDENCE: 'Insufficient evidence',
  PROVIDER_FAILURE: 'Provider failure',
};

export function semanticSuggestionLabel(suggestion: SemanticSuggestionType): string {
  return SEMANTIC_SUGGESTION_LABELS[suggestion];
}

/** `review_application.models.ReviewEventType` — what the history records. */
export const EVENT_TYPE_LABELS: Record<ReviewEventType, string> = {
  CASE_CREATED: 'Case created',
  SEMANTIC_SUGGESTION_RECORDED: 'Semantic advisory recorded',
  MATCH: 'Resolved as Match',
  NO_MATCH: 'Resolved as No match',
  DEFERRED: 'Resolved as Deferred',
};

export function eventTypeLabel(eventType: ReviewEventType): string {
  return EVENT_TYPE_LABELS[eventType];
}
