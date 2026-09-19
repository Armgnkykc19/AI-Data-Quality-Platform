/**
 * The three case-scoped reads the workspace performs.
 *
 * Each is a typed name over `useCaseResource`, which owns the request
 * discipline they share: null id means no request, a superseded read is
 * aborted and silent, a stale response cannot overwrite a newer one, and a
 * value is never exposed under a case it was not fetched for.
 *
 * They are deliberately independent. Detail, advisory and history are three
 * separate public resources, and Sprint 11 answers each on its own; chaining
 * them would make an advisory outage hide deterministic evidence that loaded
 * perfectly well. The workspace composes them and decides what a partial
 * failure looks like.
 *
 * The client functions are passed by reference rather than wrapped in a
 * closure, so their identity is stable across renders and the effect that
 * depends on them does not re-fire.
 */

import { getReviewCase, getReviewEvents, getSemanticSuggestions } from '../api/client';
import type { ReviewCaseDetail, ReviewEventRead, SemanticSuggestionRead } from '../api/types';
import { useCaseResource, type CaseResource } from './useCaseResource';

/** `GET /api/v1/review-cases/{id}` — the primary resource for the workspace. */
export function useReviewCase(reviewCaseId: string | null): CaseResource<ReviewCaseDetail> {
  return useCaseResource(reviewCaseId, getReviewCase);
}

/** `GET /api/v1/review-cases/{id}/events` — append-only history, oldest first. */
export function useReviewEvents(reviewCaseId: string | null): CaseResource<ReviewEventRead[]> {
  return useCaseResource(reviewCaseId, getReviewEvents);
}

/**
 * `GET /api/v1/review-cases/{id}/semantic-suggestions` — stored advisories.
 *
 * Reading only. Sprint 11 publishes no way to generate a suggestion over
 * HTTP, so there is no counterpart to this hook and none may be added.
 */
export function useSemanticSuggestions(
  reviewCaseId: string | null,
): CaseResource<SemanticSuggestionRead[]> {
  return useCaseResource(reviewCaseId, getSemanticSuggestions);
}
