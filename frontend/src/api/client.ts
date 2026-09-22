/**
 * The typed client for the six Sprint 11 operations. It transports; it decides nothing.
 *
 * Three properties are load-bearing, and each has a test that fails if it is
 * lost.
 *
 * **Every path is relative.** There is no base URL, no host, no port, and no
 * `VITE_API_URL`. The browser always issues same-origin requests, and where
 * they actually go is the Vite proxy's business (see `vite.config.ts`). That
 * is what lets the project keep its deliberate no-CORS boundary: an absolute
 * `http://127.0.0.1:8000/...` here would make every request cross-origin and
 * would be blocked -- or, worse, would motivate adding CORS to an API that
 * serves customer-derived evidence. Since Sprint 13 that API also
 * authenticates and authorizes every review request, and a cross-origin
 * surface is exactly where its session cookie should not be exposed.
 *
 * **Nothing is ever retried.** Not a 503, not a network failure, and above all
 * not `POST .../resolve`. Resolution is not idempotent, and a retry would
 * re-run Sprint 08 authorization against a queue state the reviewer never saw
 * -- which is the exact failure optimistic concurrency exists to prevent. One
 * call from a caller is one `fetch`. A user-triggered "Retry" button in a
 * later phase is a second call, made deliberately, by a human.
 *
 * **No domain logic lives here.** No threshold is compared, no status is
 * judged, no version is incremented or inferred. `expected_version` is
 * whatever the caller passes, which must be the version the reviewer was
 * shown. The client's whole job is URL construction, JSON, and turning a
 * non-contract response into a typed failure instead of a plausible-looking
 * value.
 */

import {
  ApiRequestError,
  classifyFetchRejection,
  decodeErrorEnvelope,
  type ApiFailure,
} from './errors';
import type {
  HealthResponse,
  ListReviewCasesParams,
  ResolveReviewCaseRequest,
  ResolveReviewCaseResponse,
  ReviewCaseDetail,
  ReviewCaseListResponse,
  ReviewEventRead,
  SemanticSuggestionRead,
} from './types';

const HEALTH_PATH = '/health';
const REVIEW_CASES_PATH = '/api/v1/review-cases';

const JSON_MEDIA_TYPE = 'application/json';

/** Passed to every operation so a caller can cancel an in-flight request. */
export interface RequestOptions {
  signal?: AbortSignal;
}

/** What the contract says a successful body looks like at the top level. */
type BodyShape = 'object' | 'array';

type ParsedBody = { parsed: true; value: unknown } | { parsed: false };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function matchesShape(value: unknown, shape: BodyShape): boolean {
  return shape === 'array' ? Array.isArray(value) : isRecord(value);
}

/**
 * Read and parse a body without letting a parse failure become a value.
 *
 * An unparseable body is reported as `parsed: false` rather than resolving to
 * `undefined`, which is what keeps a malformed 200 from flowing into the UI as
 * an empty case.
 */
async function readBody(response: Response): Promise<ParsedBody> {
  let text: string;
  try {
    text = await response.text();
  } catch {
    return { parsed: false };
  }
  if (text.length === 0) {
    return { parsed: false };
  }
  try {
    return { parsed: true, value: JSON.parse(text) as unknown };
  } catch {
    return { parsed: false };
  }
}

/**
 * One request, one `fetch`, one typed result or one typed failure.
 *
 * The success body is shape-checked but not schema-validated. Phase A adds no
 * runtime validation library: the check that matters here is "is this the
 * contract's envelope at all", and a field-by-field validator would duplicate
 * the backend's own response models without being able to disagree with them
 * usefully.
 */
async function request<T>(
  path: string,
  init: RequestInit,
  shape: BodyShape,
  options?: RequestOptions,
): Promise<T> {
  // Assigned conditionally rather than spread with a possible `undefined`:
  // under `exactOptionalPropertyTypes`, an explicit undefined is not the same
  // as an absent property.
  const requestInit: RequestInit = { ...init };
  if (options?.signal !== undefined) {
    requestInit.signal = options.signal;
  }

  let response: Response;
  try {
    response = await fetch(path, requestInit);
  } catch (reason) {
    throw new ApiRequestError(classifyFetchRejection(reason, options?.signal));
  }

  const body = await readBody(response);

  if (!response.ok) {
    const failure: ApiFailure = body.parsed
      ? decodeErrorEnvelope(response.status, body.value)
      : { kind: 'malformed', httpStatus: response.status };
    throw new ApiRequestError(failure);
  }

  if (!body.parsed || !matchesShape(body.value, shape)) {
    throw new ApiRequestError({ kind: 'malformed', httpStatus: response.status });
  }

  return body.value as T;
}

function getJson<T>(path: string, shape: BodyShape, options?: RequestOptions): Promise<T> {
  return request<T>(path, { method: 'GET', headers: { Accept: JSON_MEDIA_TYPE } }, shape, options);
}

/**
 * A review case id in a path segment.
 *
 * Encoded rather than interpolated raw. Sprint 08 ids are `RC-` plus a digest
 * and need no escaping, but the id reaching this function came from a
 * response or from application state, and building a URL by concatenation is
 * how a `/` or a `?` in an unexpected value silently becomes a different
 * request.
 */
function caseSegment(reviewCaseId: string): string {
  return `${REVIEW_CASES_PATH}/${encodeURIComponent(reviewCaseId)}`;
}

// ---------------------------------------------------------------------------
// Operations
// ---------------------------------------------------------------------------

/** `GET /health`. Liveness only -- it reports nothing about the queue. */
export function getHealth(options?: RequestOptions): Promise<HealthResponse> {
  return getJson<HealthResponse>(HEALTH_PATH, 'object', options);
}

/**
 * `GET /api/v1/review-cases`.
 *
 * Absent parameters are omitted from the query string entirely rather than
 * sent empty: `?status=` is a 422, because the backend matches the status
 * enum exactly and does not treat an empty value as "no filter".
 */
export function listReviewCases(
  params: ListReviewCasesParams = {},
  options?: RequestOptions,
): Promise<ReviewCaseListResponse> {
  const query = new URLSearchParams();
  if (params.status !== undefined) {
    query.set('status', params.status);
  }
  if (params.limit !== undefined) {
    query.set('limit', String(params.limit));
  }
  if (params.offset !== undefined) {
    query.set('offset', String(params.offset));
  }

  const queryString = query.toString();
  const path = queryString === '' ? REVIEW_CASES_PATH : `${REVIEW_CASES_PATH}?${queryString}`;
  return getJson<ReviewCaseListResponse>(path, 'object', options);
}

/** `GET /api/v1/review-cases/{id}`. Full detail, including the version to echo back. */
export function getReviewCase(
  reviewCaseId: string,
  options?: RequestOptions,
): Promise<ReviewCaseDetail> {
  return getJson<ReviewCaseDetail>(caseSegment(reviewCaseId), 'object', options);
}

/** `GET /api/v1/review-cases/{id}/events`. A bare array, oldest first. */
export function getReviewEvents(
  reviewCaseId: string,
  options?: RequestOptions,
): Promise<ReviewEventRead[]> {
  return getJson<ReviewEventRead[]>(`${caseSegment(reviewCaseId)}/events`, 'array', options);
}

/**
 * `GET /api/v1/review-cases/{id}/semantic-suggestions`. A bare array.
 *
 * Reading only. The API exposes no live semantic generation over HTTP at all,
 * so there is no counterpart to this function and none may be added: it would
 * put a provider call and a spend decision in a reviewer-triggered request
 * path.
 */
export function getSemanticSuggestions(
  reviewCaseId: string,
  options?: RequestOptions,
): Promise<SemanticSuggestionRead[]> {
  return getJson<SemanticSuggestionRead[]>(
    `${caseSegment(reviewCaseId)}/semantic-suggestions`,
    'array',
    options,
  );
}

/**
 * `POST /api/v1/review-cases/{id}/resolve`. The one authoritative write.
 *
 * The body is serialized exactly as given. `expected_version` is not defaulted,
 * adjusted, or re-read from anywhere, and `reviewer_id` is sent verbatim --
 * including an empty string, which the backend accepts and records as-is.
 * Omitting the property and sending null both mean "no reviewer id" to the
 * API, and this function preserves whichever the caller chose.
 *
 * Returns the authoritative updated case and the appended event. Note that the
 * returned event's `event_id` is always null; stable ids come from the history
 * endpoint.
 */
export function resolveReviewCase(
  reviewCaseId: string,
  body: ResolveReviewCaseRequest,
  options?: RequestOptions,
): Promise<ResolveReviewCaseResponse> {
  return request<ResolveReviewCaseResponse>(
    `${caseSegment(reviewCaseId)}/resolve`,
    {
      method: 'POST',
      headers: { 'Content-Type': JSON_MEDIA_TYPE, Accept: JSON_MEDIA_TYPE },
      body: JSON.stringify(body),
    },
    'object',
    options,
  );
}
