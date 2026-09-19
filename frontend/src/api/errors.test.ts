import { describe, expect, it } from 'vitest';

import {
  ApiRequestError,
  ERROR_CODES,
  classifyFetchRejection,
  decodeErrorDetails,
  decodeErrorEnvelope,
  errorCodeOf,
  hasErrorCode,
  isAbortFailure,
  isErrorCode,
} from './errors';

function envelope(code: string, message = 'Something went wrong.', details: unknown = null) {
  return { error: { code, message, details } };
}

describe('the public error vocabulary', () => {
  it('is exactly the fourteen codes review_api declares, in its order', () => {
    // Pinned as a list rather than a count, so a backend code that is added,
    // removed or renamed fails here instead of quietly never being handled.
    expect([...ERROR_CODES]).toEqual([
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
    ]);
  });

  it('declares no code for a boundary a later sprint owns', () => {
    for (const premature of ['UNAUTHENTICATED', 'FORBIDDEN', 'QUEUE_ALREADY_REGISTERED']) {
      expect(isErrorCode(premature)).toBe(false);
    }
  });
});

describe('decodeErrorEnvelope', () => {
  it.each(ERROR_CODES)('decodes %s into a branchable api failure', (code) => {
    const failure = decodeErrorEnvelope(500, envelope(code));

    expect(failure).toEqual({
      kind: 'api',
      code,
      httpStatus: 500,
      message: 'Something went wrong.',
      details: null,
    });
  });

  it('preserves the HTTP status the response carried', () => {
    const failure = decodeErrorEnvelope(409, envelope('REVIEW_CASE_VERSION_CONFLICT'));

    expect(failure.kind === 'api' && failure.httpStatus).toBe(409);
  });

  it('carries the API message as opaque display data', () => {
    // Held so a UI may show it. Never matched against: branching on prose
    // would couple the client to wording the backend is free to change.
    const failure = decodeErrorEnvelope(404, envelope('REVIEW_CASE_NOT_FOUND', 'No review case.'));

    expect(failure.kind === 'api' && failure.message).toBe('No review case.');
  });

  it('tolerates a missing details key as null rather than rejecting the envelope', () => {
    // Deliberate asymmetry. The API always sends `details`, but degrading a
    // real version conflict into a malformed failure over an absent null would
    // lose the one signal that stops a reviewer overwriting a decision.
    const failure = decodeErrorEnvelope(409, {
      error: { code: 'REVIEW_CASE_VERSION_CONFLICT', message: 'Reload it.' },
    });

    expect(failure).toEqual({
      kind: 'api',
      code: 'REVIEW_CASE_VERSION_CONFLICT',
      httpStatus: 409,
      message: 'Reload it.',
      details: null,
    });
  });

  it.each([
    ['an unknown code', envelope('SOMETHING_NEW')],
    ['a non-string message', { error: { code: 'INTERNAL_ERROR', message: 42, details: null } }],
    ['a missing error key', { detail: 'Not Found' }],
    ['an error that is not an object', { error: 'boom' }],
    ['a top-level array', ['boom']],
    ['a bare string', 'Internal Server Error'],
    ['null', null],
  ])('treats %s as malformed rather than guessing a code', (_label, body) => {
    expect(decodeErrorEnvelope(500, body)).toEqual({ kind: 'malformed', httpStatus: 500 });
  });
});

describe('decodeErrorDetails', () => {
  it('decodes sanitized validation feedback', () => {
    const details = decodeErrorDetails({
      fields: [{ location: ['body', 'expected_version'], type: 'int_parsing' }],
    });

    expect(details).toEqual({
      kind: 'validation',
      fields: [{ location: ['body', 'expected_version'], type: 'int_parsing' }],
    });
  });

  it('decodes the version-conflict echo of the client’s own value', () => {
    expect(decodeErrorDetails({ expected_version: 3 })).toEqual({
      kind: 'versionConflict',
      expectedVersion: 3,
    });
  });

  it('refuses a fields array with one unreadable entry, rather than trimming it', () => {
    // Showing the readable half would present a partial answer as a complete
    // one, and the shape is evidently not what this decoder thinks it is.
    const details = decodeErrorDetails({
      fields: [{ location: ['body'], type: 'missing' }, { location: 'body' }],
    });

    expect(details).toBeNull();
  });

  it.each([
    ['an unrecognised object', { stored_version: 4 }],
    ['a non-integer version', { expected_version: 1.5 }],
    ['a stringified version', { expected_version: '3' }],
    ['an array', [1, 2]],
    ['null', null],
  ])('drops %s so no raw body can reach the UI', (_label, raw) => {
    expect(decodeErrorDetails(raw)).toBeNull();
  });
});

describe('classifyFetchRejection', () => {
  it('reports an aborted signal as aborted, not as a network failure', () => {
    const controller = new AbortController();
    controller.abort();

    expect(classifyFetchRejection(new Error('aborted'), controller.signal)).toEqual({
      kind: 'aborted',
    });
  });

  it('recognises an AbortError even without the signal', () => {
    const abortError = new Error('The operation was aborted.');
    abortError.name = 'AbortError';

    expect(classifyFetchRejection(abortError)).toEqual({ kind: 'aborted' });
  });

  it('reports an unreachable server as a network failure', () => {
    const controller = new AbortController();

    expect(classifyFetchRejection(new TypeError('Failed to fetch'), controller.signal)).toEqual({
      kind: 'network',
    });
  });
});

describe('caller-facing predicates', () => {
  it('identifies an abort so a caller can return quietly', () => {
    const aborted = new ApiRequestError({ kind: 'aborted' });

    expect(isAbortFailure(aborted)).toBe(true);
    expect(isAbortFailure(new ApiRequestError({ kind: 'network' }))).toBe(false);
    expect(isAbortFailure(new Error('unrelated'))).toBe(false);
  });

  it('matches a specific code and nothing else', () => {
    const error = new ApiRequestError(
      decodeErrorEnvelope(422, envelope('MATCH_NOT_AUTHORIZED', 'Not permitted.')),
    );

    expect(hasErrorCode(error, 'MATCH_NOT_AUTHORIZED')).toBe(true);
    expect(hasErrorCode(error, 'HUMAN_REVIEW_CONTRADICTION')).toBe(false);
    expect(errorCodeOf(error)).toBe('MATCH_NOT_AUTHORIZED');
  });

  it('reports no code for a transport-level failure', () => {
    expect(errorCodeOf(new ApiRequestError({ kind: 'network' }))).toBeNull();
    expect(errorCodeOf(new ApiRequestError({ kind: 'malformed', httpStatus: 502 }))).toBeNull();
    expect(errorCodeOf(new Error('unrelated'))).toBeNull();
  });

  it('keeps the API message out of Error.message', () => {
    // Error.message ends up in logs and consoles; the envelope's prose belongs
    // on screen instead.
    const error = new ApiRequestError(
      decodeErrorEnvelope(503, envelope('REVIEW_QUEUE_NOT_READY', 'The queue is not ready.')),
    );

    expect(error.message).toBe('API error REVIEW_QUEUE_NOT_READY (HTTP 503)');
    expect(error.message).not.toContain('The queue is not ready.');
  });
});
