import { describe, expect, it } from 'vitest';

import { resolutionFailureMessage } from './resolutionFailureMessage';
import { ERROR_CODES, type ApiFailure, type ErrorCode } from '../../api/errors';

function apiFailure(code: ErrorCode, httpStatus = 409): ApiFailure {
  // The message is deliberately something the UI must never echo.
  return {
    kind: 'api',
    code,
    httpStatus,
    message: 'Traceback: sqlite3.OperationalError at /var/lib/review/queue.db',
    details: null,
  };
}

const TRANSPORT_FAILURES: ApiFailure[] = [
  { kind: 'network' },
  { kind: 'aborted' },
  { kind: 'malformed', httpStatus: 500 },
  { kind: 'malformed', httpStatus: null },
];

describe('coverage', () => {
  it('answers every published error code', () => {
    for (const code of ERROR_CODES) {
      const message = resolutionFailureMessage(apiFailure(code));
      expect(message.title.length).toBeGreaterThan(0);
      expect(message.description.length).toBeGreaterThan(0);
    }
  });

  it('answers every transport failure', () => {
    for (const failure of TRANSPORT_FAILURES) {
      expect(resolutionFailureMessage(failure).title.length).toBeGreaterThan(0);
    }
  });
});

describe('classification', () => {
  it.each([
    ['MATCH_NOT_AUTHORIZED', 'refusal', false],
    ['HUMAN_REVIEW_CONTRADICTION', 'refusal', true],
    ['REVIEW_CASE_VERSION_CONFLICT', 'staleState', true],
    ['REVIEW_CASE_NOT_PENDING', 'staleState', true],
    ['REVIEW_CASE_NOT_FOUND', 'staleState', true],
    ['AUTHORIZATION_CONTEXT_UNAVAILABLE', 'operational', false],
    ['AUTHORIZATION_CONFIG_UNAVAILABLE', 'operational', false],
    ['REVIEW_QUEUE_NOT_READY', 'operational', false],
    ['REVIEW_STORAGE_UNAVAILABLE', 'uncertain', true],
    ['REVIEW_STORAGE_CORRUPT', 'uncertain', true],
    ['INTERNAL_ERROR', 'uncertain', true],
    ['INVALID_REQUEST', 'interface', false],
    ['NOT_FOUND', 'interface', false],
    ['METHOD_NOT_ALLOWED', 'interface', false],
  ] as const)('puts %s in the %s class', (code, kind, reconcile) => {
    const message = resolutionFailureMessage(apiFailure(code));

    expect(message.kind).toBe(kind);
    expect(message.reconcile).toBe(reconcile);
  });

  it('treats every transport failure as an unconfirmed write', () => {
    // The point of the class: a request that produced no usable answer may
    // still have been applied, so nothing may present it as "did not happen"
    // and nothing may offer to send it again before an authoritative read.
    for (const failure of TRANSPORT_FAILURES) {
      const message = resolutionFailureMessage(failure);
      expect(message.kind).toBe('uncertain');
      expect(message.reconcile).toBe(true);
    }
  });
});

describe('what the copy may say', () => {
  it('never repeats the API’s own message, a path, a status or an exception', () => {
    const failures: ApiFailure[] = [
      ...ERROR_CODES.map((code) => apiFailure(code, 503)),
      ...TRANSPORT_FAILURES,
    ];

    for (const failure of failures) {
      const { title, description } = resolutionFailureMessage(failure);
      const text = `${title} ${description}`;
      for (const leak of ['sqlite', 'Traceback', '/var', '503', '409', '{', 'OperationalError']) {
        expect(text, `leaked ${leak}`).not.toContain(leak);
      }
    }
  });

  it('claims no cause and no actor for a stale or refused decision', () => {
    for (const code of [
      'REVIEW_CASE_VERSION_CONFLICT',
      'REVIEW_CASE_NOT_PENDING',
      'HUMAN_REVIEW_CONTRADICTION',
      'REVIEW_CASE_NOT_FOUND',
    ] as const) {
      const { title, description } = resolutionFailureMessage(apiFailure(code));
      const text = `${title} ${description}`.toLowerCase();
      // The API says the transition is not valid. It does not say who acted,
      // when, what they decided, or that anything was deleted.
      for (const invented of ['another reviewer', 'someone', 'deleted', 'removed by']) {
        expect(text, `invented ${invented}`).not.toContain(invented);
      }
    }
  });

  it('offers no bypass for a refused match and no alternative decision', () => {
    const { title, description } = resolutionFailureMessage(apiFailure('MATCH_NOT_AUTHORIZED', 422));
    const text = `${title} ${description}`.toLowerCase();

    for (const bypass of ['force', 'override the', 'threshold', 'try no match', 'instead']) {
      expect(text, `suggested ${bypass}`).not.toContain(bypass);
    }
    // And it does not report a domain refusal as a server fault.
    expect(text).not.toContain('error');
    expect(text).toContain('not authorize');
  });

  it('tells an uncertain write apart from a refusal in the copy itself', () => {
    const refused = resolutionFailureMessage(apiFailure('MATCH_NOT_AUTHORIZED', 422));
    const unknown = resolutionFailureMessage({ kind: 'network' });

    expect(refused.description.toLowerCase()).toContain('no match resolution was recorded');
    expect(unknown.description.toLowerCase()).toContain('unknown');
  });
});
