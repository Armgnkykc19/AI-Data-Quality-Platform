/**
 * Decoding the Sprint 11 public error envelope, and the rule that keeps it safe.
 *
 * Every failure the API returns has one shape:
 *
 *     {"error": {"code": "...", "message": "...", "details": null}}
 *
 * `code` is an API-owned token, stable across releases and the only thing a
 * client may branch on. `message` is a static sentence the API owns; it is
 * carried here as opaque display text and is never inspected, matched, or
 * parsed. Branching on prose would couple the UI to wording the backend is
 * free to change, and the backend goes to real lengths to ensure no exception
 * text ever reaches a response -- reading it as data would waste that.
 *
 * Three decoding rules follow from that, and each is tested:
 *
 * A response that does not carry a recognised envelope is `malformed`, never
 * an `api` failure with a guessed code. A UI that invented a code for an
 * unrecognised body would branch on a condition the backend never reported.
 *
 * `details` is narrowed to the two structures the API actually builds, and
 * anything else becomes null. That is what stops a raw response body from
 * reaching reviewer-facing UI: there is no pass-through channel for it.
 *
 * An aborted request is its own variant. Cancelling an in-flight read when the
 * reviewer selects a different case is expected behaviour, not a failure, and
 * a UI that rendered it as one would flash errors at anyone moving quickly.
 */

/**
 * The complete public vocabulary, in the order `review_api.errors.ErrorCode`
 * declares it. `tests/review_api/test_api_models.py` pins that list, so a new
 * backend code is a deliberate contract change rather than a silent one.
 */
export const ERROR_CODES = [
  'INVALID_REQUEST',
  'NOT_FOUND',
  'METHOD_NOT_ALLOWED',
  'INTERNAL_ERROR',
  'REVIEW_CASE_NOT_FOUND',
  'REVIEW_STORAGE_UNAVAILABLE',
  'REVIEW_STORAGE_CORRUPT',
  'REVIEW_CASE_VERSION_CONFLICT',
  'REVIEW_CASE_NOT_PENDING',
  'HUMAN_REVIEW_CONTRADICTION',
  'MATCH_NOT_AUTHORIZED',
  'AUTHORIZATION_CONTEXT_UNAVAILABLE',
  'AUTHORIZATION_CONFIG_UNAVAILABLE',
  'REVIEW_QUEUE_NOT_READY',
] as const;

export type ErrorCode = (typeof ERROR_CODES)[number];

const ERROR_CODE_SET: ReadonlySet<string> = new Set<string>(ERROR_CODES);

export function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === 'string' && ERROR_CODE_SET.has(value);
}

// ---------------------------------------------------------------------------
// Details
// ---------------------------------------------------------------------------

/** One entry of the sanitized validation feedback: which field, which rule. */
export interface ValidationFieldDetail {
  /** Pydantic's `loc`, stringified -- e.g. `['body', 'expected_version']`. */
  location: readonly string[];
  /** Pydantic's error `type` -- e.g. `'int_parsing'`, `'extra_forbidden'`. */
  type: string;
}

/**
 * The decoded `details` payload.
 *
 * A frontend-owned model rather than the wire shape, so it carries a `kind`
 * discriminant and camelCase names. Only the two structures the API actually
 * builds are represented; every other body decodes to null.
 */
export type ApiErrorDetails =
  | { kind: 'validation'; fields: readonly ValidationFieldDetail[] }
  | { kind: 'versionConflict'; expectedVersion: number };

// ---------------------------------------------------------------------------
// Failures
// ---------------------------------------------------------------------------

export type ApiFailure =
  /** The API answered with a recognised envelope. The only branchable case. */
  | {
      kind: 'api';
      code: ErrorCode;
      httpStatus: number;
      /** Static, API-owned prose. Display only -- never matched against. */
      message: string;
      details: ApiErrorDetails | null;
    }
  /** The request never produced a response: server down, proxy down, DNS, TLS. */
  | { kind: 'network' }
  /** The caller aborted. Expected, and not reviewer-facing. */
  | { kind: 'aborted' }
  /**
   * A response arrived but did not match the contract: an error body that is
   * not the envelope, an unknown code, or a success body that is not the
   * documented shape. `httpStatus` is null when the body failed to parse
   * outside of an HTTP failure.
   */
  | { kind: 'malformed'; httpStatus: number | null };

/** The one error type every client function throws. */
export class ApiRequestError extends Error {
  readonly failure: ApiFailure;

  constructor(failure: ApiFailure) {
    super(describeFailure(failure));
    this.name = 'ApiRequestError';
    this.failure = failure;
  }
}

/**
 * A short, developer-facing summary for `Error.message`.
 *
 * Never shown to a reviewer, and deliberately does not include the API's own
 * `message`: an `Error.message` tends to end up in logs and consoles, and the
 * envelope's prose belongs on screen, not in a stack trace.
 */
function describeFailure(failure: ApiFailure): string {
  switch (failure.kind) {
    case 'api':
      return `API error ${failure.code} (HTTP ${failure.httpStatus})`;
    case 'network':
      return 'Network request failed';
    case 'aborted':
      return 'Request aborted';
    case 'malformed':
      return failure.httpStatus === null
        ? 'Malformed response'
        : `Malformed response (HTTP ${failure.httpStatus})`;
  }
}

// ---------------------------------------------------------------------------
// Predicates -- the supported way for callers to branch
// ---------------------------------------------------------------------------

export function isApiRequestError(error: unknown): error is ApiRequestError {
  return error instanceof ApiRequestError;
}

/**
 * True for a request the caller cancelled.
 *
 * Callers should test this first and return quietly. It is the difference
 * between "the reviewer moved on" and "the queue is unreachable".
 */
export function isAbortFailure(error: unknown): boolean {
  return isApiRequestError(error) && error.failure.kind === 'aborted';
}

/** True when the API reported exactly `code`. The only safe way to branch. */
export function hasErrorCode(error: unknown, code: ErrorCode): boolean {
  return isApiRequestError(error) && error.failure.kind === 'api' && error.failure.code === code;
}

/** The reported code, or null for a transport-level or unrecognised failure. */
export function errorCodeOf(error: unknown): ErrorCode | null {
  if (!isApiRequestError(error) || error.failure.kind !== 'api') {
    return null;
  }
  return error.failure.code;
}

// ---------------------------------------------------------------------------
// Decoding
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function decodeValidationField(raw: unknown): ValidationFieldDetail | null {
  if (!isRecord(raw)) {
    return null;
  }
  const { location, type } = raw;
  if (!Array.isArray(location) || typeof type !== 'string') {
    return null;
  }
  if (!location.every((part): part is string => typeof part === 'string')) {
    return null;
  }
  return { location, type };
}

/**
 * Narrow `details` to a structure this module recognises, or drop it.
 *
 * Partial validation feedback is refused rather than trimmed: a `fields` array
 * with one unreadable entry means the shape is not what we think it is, and
 * showing the readable half would present a partial answer as a complete one.
 */
export function decodeErrorDetails(raw: unknown): ApiErrorDetails | null {
  if (!isRecord(raw)) {
    return null;
  }

  if (Array.isArray(raw['fields'])) {
    const decoded: ValidationFieldDetail[] = [];
    for (const entry of raw['fields']) {
      const field = decodeValidationField(entry);
      if (field === null) {
        return null;
      }
      decoded.push(field);
    }
    return { kind: 'validation', fields: decoded };
  }

  const expectedVersion = raw['expected_version'];
  if (typeof expectedVersion === 'number' && Number.isInteger(expectedVersion)) {
    return { kind: 'versionConflict', expectedVersion };
  }

  return null;
}

/**
 * Turn a non-2xx response body into an `ApiFailure`.
 *
 * `code` and `message` are required, because they are what the UI acts on. A
 * missing `details` key is tolerated as null rather than rejected: the API
 * always sends it, but degrading a real `REVIEW_CASE_VERSION_CONFLICT` into a
 * `malformed` failure over an absent null would lose the one signal that
 * protects a reviewer from overwriting someone else's decision.
 */
export function decodeErrorEnvelope(httpStatus: number, body: unknown): ApiFailure {
  if (!isRecord(body)) {
    return { kind: 'malformed', httpStatus };
  }

  const error = body['error'];
  if (!isRecord(error)) {
    return { kind: 'malformed', httpStatus };
  }

  const { code, message } = error;
  if (!isErrorCode(code) || typeof message !== 'string') {
    return { kind: 'malformed', httpStatus };
  }

  return {
    kind: 'api',
    code,
    httpStatus,
    message,
    details: decodeErrorDetails(error['details']),
  };
}

/**
 * Classify a `fetch` rejection.
 *
 * `fetch` rejects with a `DOMException` named `AbortError` when the signal
 * fires, and with a `TypeError` for everything else it cannot complete.
 * `signal.aborted` is checked as well, because a request aborted before the
 * fetch begins can surface differently across runtimes.
 */
export function classifyFetchRejection(reason: unknown, signal?: AbortSignal): ApiFailure {
  if (signal?.aborted === true) {
    return { kind: 'aborted' };
  }
  if (reason instanceof Error && reason.name === 'AbortError') {
    return { kind: 'aborted' };
  }
  return { kind: 'network' };
}
