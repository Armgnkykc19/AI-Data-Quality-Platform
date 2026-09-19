import { describe, expect, it } from 'vitest';

import { queueFailureMessage } from './queueFailureMessage';
import { ERROR_CODES, type ApiFailure, type ErrorCode } from '../../api/errors';

function apiFailure(code: ErrorCode, httpStatus = 500): ApiFailure {
  return { kind: 'api', code, httpStatus, message: 'Backend prose.', details: null };
}

describe('queueFailureMessage', () => {
  it('answers every public error code with a message', () => {
    // Total by construction, so a backend code added later cannot leave the
    // queue rendering an empty banner.
    for (const code of ERROR_CODES) {
      const message = queueFailureMessage(apiFailure(code));
      expect(message.title.length).toBeGreaterThan(0);
      expect(message.description.length).toBeGreaterThan(0);
    }
  });

  it.each([
    ['REVIEW_STORAGE_UNAVAILABLE', true],
    ['INTERNAL_ERROR', true],
    ['REVIEW_STORAGE_CORRUPT', false],
    ['INVALID_REQUEST', false],
    ['NOT_FOUND', false],
    ['METHOD_NOT_ALLOWED', false],
  ] as const)('marks %s retryable=%s', (code, retryable) => {
    expect(queueFailureMessage(apiFailure(code)).retryable).toBe(retryable);
  });

  it('separates a corrupt queue from an unavailable one', () => {
    // Both are storage problems, but only one can improve by asking again.
    const corrupt = queueFailureMessage(apiFailure('REVIEW_STORAGE_CORRUPT'));
    const unavailable = queueFailureMessage(apiFailure('REVIEW_STORAGE_UNAVAILABLE'));

    expect(corrupt.title).not.toBe(unavailable.title);
    expect(corrupt.retryable).toBe(false);
    expect(unavailable.retryable).toBe(true);
  });

  it('frames an invalid request as a defect in this interface', () => {
    const message = queueFailureMessage(apiFailure('INVALID_REQUEST', 422));

    expect(message.description).toMatch(/defect here/i);
    expect(message.retryable).toBe(false);
  });

  it('distinguishes an unreachable API from an unexpected response', () => {
    const network = queueFailureMessage({ kind: 'network' });
    const malformed = queueFailureMessage({ kind: 'malformed', httpStatus: 502 });

    expect(network.title).toBe('Cannot reach the local review API.');
    expect(malformed.title).toBe('The local review API returned an unexpected response.');
    expect(network.retryable).toBe(true);
    expect(malformed.retryable).toBe(true);
  });

  it('never repeats the backend’s own prose', () => {
    // The API message is display data the panel may choose to show; this
    // mapping is the application's own copy, keyed by code alone.
    for (const code of ERROR_CODES) {
      const message = queueFailureMessage(apiFailure(code));
      expect(message.title).not.toContain('Backend prose.');
      expect(message.description).not.toContain('Backend prose.');
    }
  });

  it('names no storage engine, path, table or status code', () => {
    for (const failure of [
      apiFailure('REVIEW_STORAGE_CORRUPT'),
      apiFailure('REVIEW_STORAGE_UNAVAILABLE'),
      { kind: 'malformed', httpStatus: 500 } as ApiFailure,
    ]) {
      const { title, description } = queueFailureMessage(failure);
      for (const leak of ['sqlite', 'sql', '.db', 'table', 'traceback', '500']) {
        expect(`${title} ${description}`.toLowerCase()).not.toContain(leak);
      }
    }
  });
});
