import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useResolveReviewCase, type ResolveOutcome } from './useResolveReviewCase';
import {
  DEFERRED_CASE_ID,
  PENDING_CASE_ID,
  matchResolveResponse,
} from '../test/fixtures/sprint11';
import { createFetchStub, errorResponse, jsonResponse, type FetchStub } from '../test/http';

let http: FetchStub;
let outcomes: ResolveOutcome[];

beforeEach(() => {
  http = createFetchStub();
  outcomes = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function record(outcome: ResolveOutcome): void {
  outcomes.push(outcome);
}

function renderController(initial: string = PENDING_CASE_ID) {
  return renderHook((id: string) => useResolveReviewCase(id), { initialProps: initial });
}

/** The body actually put on the wire, parsed back. */
function sentBody(index = 0): unknown {
  const call = http.mock.mock.calls[index];
  if (call === undefined) {
    throw new Error('fetch was never called.');
  }
  return JSON.parse(String(call[1]?.body));
}

function sentInit(index = 0): RequestInit {
  const call = http.mock.mock.calls[index];
  if (call === undefined) {
    throw new Error('fetch was never called.');
  }
  return call[1] ?? {};
}

// ---------------------------------------------------------------------------

describe('the request', () => {
  it('posts to the resolve sub-path of the encoded case id', async () => {
    http.alwaysRespond(matchResolveResponse);
    const { result } = renderController('RC-1/2?x=1');

    act(() => {
      result.current.submit(
        {
          reviewCaseId: 'RC-1/2?x=1',
          decision: 'MATCH',
          expectedVersion: 1,
          reviewerId: null,
        },
        record,
      );
    });

    await waitFor(() => {
      expect(outcomes).toHaveLength(1);
    });
    expect(http.lastUrl()).toBe('/api/v1/review-cases/RC-1%2F2%3Fx%3D1/resolve');
    expect(sentInit().method).toBe('POST');
    expect(sentInit().headers).toMatchObject({ 'Content-Type': 'application/json' });
  });

  it.each([
    ['MATCH', 1],
    ['NO_MATCH', 7],
    ['DEFER', 42],
  ] as const)('sends %s verbatim with the exact expected_version', async (decision, version) => {
    http.alwaysRespond(matchResolveResponse);
    const { result } = renderController();

    act(() => {
      result.current.submit(
        {
          reviewCaseId: PENDING_CASE_ID,
          decision,
          expectedVersion: version,
          reviewerId: null,
        },
        record,
      );
    });

    await waitFor(() => {
      expect(outcomes).toHaveLength(1);
    });
    // The decision vocabulary, never the status vocabulary: DEFER, not
    // DEFERRED. And the version exactly as captured -- not incremented, not
    // re-read, not defaulted.
    expect(sentBody()).toEqual({
      decision,
      expected_version: version,
      reviewer_id: null,
    });
  });

  it('sends an unverified reviewer label exactly as given', async () => {
    http.alwaysRespond(matchResolveResponse);
    const { result } = renderController();

    // Surrounding whitespace and mixed case survive: the backend writes this
    // into an append-only audit row verbatim, so normalising it here would
    // silently record something the reviewer did not type.
    act(() => {
      result.current.submit(
        {
          reviewCaseId: PENDING_CASE_ID,
          decision: 'NO_MATCH',
          expectedVersion: 3,
          reviewerId: '  Desk 3  ',
        },
        record,
      );
    });

    await waitFor(() => {
      expect(outcomes).toHaveLength(1);
    });
    expect(sentBody()).toEqual({
      decision: 'NO_MATCH',
      expected_version: 3,
      reviewer_id: '  Desk 3  ',
    });
  });
});

describe('one request per explicit action', () => {
  it('ignores a second submission while the first is in flight', async () => {
    const deferred = http.defer();
    const { result } = renderController();

    act(() => {
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'MATCH', expectedVersion: 1, reviewerId: null },
        record,
      );
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'DEFER', expectedVersion: 1, reviewerId: null },
        record,
      );
    });

    expect(http.callCount()).toBe(1);
    expect(result.current.isSubmitting).toBe(true);

    await act(async () => {
      deferred.resolve(matchResolveResponse);
    });

    expect(outcomes).toEqual([{ kind: 'recorded', decision: 'MATCH' }]);
    expect(http.callCount()).toBe(1);
  });

  it('accepts a deliberate second submission after the first settled', async () => {
    http.alwaysRespond(matchResolveResponse);
    const { result } = renderController();

    act(() => {
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'MATCH', expectedVersion: 1, reviewerId: null },
        record,
      );
    });
    await waitFor(() => {
      expect(outcomes).toHaveLength(1);
    });

    act(() => {
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'DEFER', expectedVersion: 2, reviewerId: null },
        record,
      );
    });
    await waitFor(() => {
      expect(outcomes).toHaveLength(2);
    });

    expect(http.callCount()).toBe(2);
  });

  it('sends nothing for a case that is not the selected one', () => {
    const { result } = renderController(PENDING_CASE_ID);

    act(() => {
      result.current.submit(
        { reviewCaseId: DEFERRED_CASE_ID, decision: 'MATCH', expectedVersion: 1, reviewerId: null },
        record,
      );
    });

    expect(http.callCount()).toBe(0);
    expect(outcomes).toHaveLength(0);
  });
});

describe('failures are reported, never retried', () => {
  it.each([
    ['REVIEW_CASE_VERSION_CONFLICT', 409],
    ['REVIEW_CASE_NOT_PENDING', 409],
    ['HUMAN_REVIEW_CONTRADICTION', 409],
    ['MATCH_NOT_AUTHORIZED', 422],
    ['REVIEW_STORAGE_UNAVAILABLE', 503],
  ] as const)('reports %s once and sends no second request', async (code, status) => {
    http.alwaysRespond(matchResolveResponse);
    http.respondOnceWith(errorResponse(code, status));
    const { result } = renderController();

    act(() => {
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'MATCH', expectedVersion: 1, reviewerId: null },
        record,
      );
    });

    await waitFor(() => {
      expect(outcomes).toHaveLength(1);
    });
    const outcome = outcomes[0];
    expect(outcome?.kind).toBe('failed');
    if (outcome?.kind === 'failed' && outcome.failure.kind === 'api') {
      expect(outcome.failure.code).toBe(code);
    }
    expect(http.callCount()).toBe(1);
    expect(result.current.isSubmitting).toBe(false);
  });

  it('reports an unreachable API without resending', async () => {
    http.failAlways();
    const { result } = renderController();

    act(() => {
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'MATCH', expectedVersion: 1, reviewerId: null },
        record,
      );
    });

    await waitFor(() => {
      expect(outcomes).toHaveLength(1);
    });
    expect(outcomes[0]).toEqual({
      kind: 'failed',
      decision: 'MATCH',
      failure: { kind: 'network' },
    });
    expect(http.callCount()).toBe(1);
  });

  it('reports a cancelled request as unknown rather than as nothing happened', async () => {
    const aborted = new Error('The operation was aborted.');
    aborted.name = 'AbortError';
    http.failAlways(aborted);
    const { result } = renderController();

    act(() => {
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'MATCH', expectedVersion: 1, reviewerId: null },
        record,
      );
    });

    await waitFor(() => {
      expect(outcomes).toHaveLength(1);
    });
    // Not `recorded`, and not silence either: an abort reaches the caller as a
    // failure whose meaning the resolution mapping decides, and that mapping
    // treats it as an unconfirmed write.
    expect(outcomes[0]).toEqual({
      kind: 'failed',
      decision: 'MATCH',
      failure: { kind: 'aborted' },
    });
  });

  it('reports a body that is not the contract as malformed', async () => {
    http.respondOnceWith(jsonResponse([matchResolveResponse]));
    const { result } = renderController();

    act(() => {
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'DEFER', expectedVersion: 1, reviewerId: null },
        record,
      );
    });

    await waitFor(() => {
      expect(outcomes).toHaveLength(1);
    });
    expect(outcomes[0]).toEqual({
      kind: 'failed',
      decision: 'DEFER',
      failure: { kind: 'malformed', httpStatus: 200 },
    });
  });
});

describe('the success body', () => {
  it('accepts the response whose event carries no id', async () => {
    // The known Sprint 11 wart: the resolve response returns the event handed
    // to storage, before the database assigned its id.
    expect(matchResolveResponse.event.event_id).toBeNull();
    http.alwaysRespond(matchResolveResponse);
    const { result } = renderController();

    act(() => {
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'MATCH', expectedVersion: 1, reviewerId: null },
        record,
      );
    });

    await waitFor(() => {
      expect(outcomes).toEqual([{ kind: 'recorded', decision: 'MATCH' }]);
    });
  });
});

describe('cross-case isolation', () => {
  it('never delivers case A’s result after the selection moved to case B', async () => {
    const deferred = http.defer();
    const { result, rerender } = renderController(PENDING_CASE_ID);

    act(() => {
      result.current.submit(
        { reviewCaseId: PENDING_CASE_ID, decision: 'MATCH', expectedVersion: 1, reviewerId: null },
        record,
      );
    });

    rerender(DEFERRED_CASE_ID);

    await act(async () => {
      deferred.resolve(matchResolveResponse);
    });

    // The request was made and may well have been applied -- what must not
    // happen is case A's outcome being reported while case B is on screen.
    expect(http.callCount()).toBe(1);
    expect(outcomes).toHaveLength(0);
  });
});
