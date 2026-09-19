import { describe, expect, it } from 'vitest';

import { caseFailureMessage, isCaseNotFound, panelFailureMessage } from './readFailureMessage';
import { ERROR_CODES, type ApiFailure, type ErrorCode } from '../../api/errors';

function apiFailure(code: ErrorCode, httpStatus = 500): ApiFailure {
  return { kind: 'api', code, httpStatus, message: 'Backend prose.', details: null };
}

describe('caseFailureMessage', () => {
  it('answers every public error code with a message', () => {
    for (const code of ERROR_CODES) {
      const message = caseFailureMessage(apiFailure(code));
      expect(message.title.length).toBeGreaterThan(0);
      expect(message.description.length).toBeGreaterThan(0);
    }
  });

  it.each([
    ['REVIEW_CASE_NOT_FOUND', false],
    ['REVIEW_STORAGE_CORRUPT', false],
    ['INVALID_REQUEST', false],
    ['REVIEW_STORAGE_UNAVAILABLE', true],
    ['INTERNAL_ERROR', true],
  ] as const)('marks %s retryable=%s', (code, retryable) => {
    expect(caseFailureMessage(apiFailure(code)).retryable).toBe(retryable);
  });

  it('reports a missing case as what the API said, and no more', () => {
    const message = caseFailureMessage(apiFailure('REVIEW_CASE_NOT_FOUND', 404));

    expect(message.title).toBe('This case is not available.');
    expect(message.description).toMatch(/reports no case with this identifier/i);
    expect(message.description).not.toMatch(/deleted|removed by|someone/i);
  });

  it('separates an unreachable API from an unexpected response', () => {
    expect(caseFailureMessage({ kind: 'network' }).title).toBe(
      'Cannot reach the local review API.',
    );
    expect(caseFailureMessage({ kind: 'malformed', httpStatus: 502 }).title).toMatch(
      /unexpected response/i,
    );
  });

  it('never repeats the backend’s own prose or names internals', () => {
    for (const code of ERROR_CODES) {
      const { title, description } = caseFailureMessage(apiFailure(code));
      const text = `${title} ${description}`;
      expect(text).not.toContain('Backend prose.');
      for (const leak of ['sqlite', 'sql', '.db', 'traceback', '500']) {
        expect(text.toLowerCase()).not.toContain(leak);
      }
    }
  });
});

describe('isCaseNotFound', () => {
  it('is true only for the detail endpoint’s 404 code', () => {
    expect(isCaseNotFound(apiFailure('REVIEW_CASE_NOT_FOUND', 404))).toBe(true);
    expect(isCaseNotFound(apiFailure('NOT_FOUND', 404))).toBe(false);
    expect(isCaseNotFound({ kind: 'network' })).toBe(false);
  });
});

describe('panelFailureMessage', () => {
  it('names the panel and says the rest of the case is unaffected', () => {
    const message = panelFailureMessage({ kind: 'network' }, 'AI advisory');

    expect(message.title).toBe('The AI advisory could not be loaded.');
    expect(message.description).toMatch(/rest of this case is unaffected/i);
    expect(message.retryable).toBe(true);
  });

  it('offers no retry for corrupt stored state', () => {
    const message = panelFailureMessage(apiFailure('REVIEW_STORAGE_CORRUPT'), 'event history');

    expect(message.title).toBe('The event history could not be loaded.');
    expect(message.retryable).toBe(false);
  });

  it('offers no retry for a case the API says is missing', () => {
    const message = panelFailureMessage(apiFailure('REVIEW_CASE_NOT_FOUND', 404), 'AI advisory');

    expect(message.retryable).toBe(false);
  });

  it('answers every public code without leaking backend prose', () => {
    for (const code of ERROR_CODES) {
      const { title, description } = panelFailureMessage(apiFailure(code), 'event history');
      expect(title.length).toBeGreaterThan(0);
      expect(`${title} ${description}`).not.toContain('Backend prose.');
    }
  });
});
