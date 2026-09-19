import { act, renderHook, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useReviewCase, useReviewEvents, useSemanticSuggestions } from './useReviewCase';
import {
  DEFERRED_CASE_ID,
  PENDING_CASE_ID,
  advisorySuggestion,
  caseBEventHistory,
  caseBSuggestion,
  deferredCaseDetail,
  eventHistory,
  pendingCaseDetail,
} from '../test/fixtures/sprint11';
import { createFetchStub, errorResponse, textResponse, type FetchStub } from '../test/http';

let http: FetchStub;

beforeEach(() => {
  http = createFetchStub();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderDetail(initial: string | null = PENDING_CASE_ID) {
  return renderHook((id: string | null) => useReviewCase(id), { initialProps: initial });
}

describe('no selection', () => {
  it.each([
    ['detail', useReviewCase],
    ['history', useReviewEvents],
    ['advisory', useSemanticSuggestions],
  ])('makes no %s request and reports idle', (_label, hook) => {
    const { result } = renderHook(() => hook(null));

    expect(http.callCount()).toBe(0);
    expect(result.current.status).toBe('idle');
    expect(result.current.data).toBeNull();
    expect(result.current.failure).toBeNull();
  });
});

describe('request targets', () => {
  it('reads the detail from the case path, encoded', async () => {
    http.alwaysRespond(pendingCaseDetail);

    const { result } = renderDetail('RC-1/2?x=1');

    await waitFor(() => {
      expect(result.current.data).toEqual(pendingCaseDetail);
    });
    expect(http.lastUrl()).toBe('/api/v1/review-cases/RC-1%2F2%3Fx%3D1');
  });

  it('reads history from the events sub-path', async () => {
    http.alwaysRespond(eventHistory);

    const { result } = renderHook(() => useReviewEvents(PENDING_CASE_ID));

    await waitFor(() => {
      expect(result.current.data).toEqual(eventHistory);
    });
    expect(http.lastUrl()).toBe(`/api/v1/review-cases/${PENDING_CASE_ID}/events`);
  });

  it('reads advisories from the semantic-suggestions sub-path', async () => {
    http.alwaysRespond([advisorySuggestion]);

    const { result } = renderHook(() => useSemanticSuggestions(PENDING_CASE_ID));

    await waitFor(() => {
      expect(result.current.data).toEqual([advisorySuggestion]);
    });
    expect(http.lastUrl()).toBe(
      `/api/v1/review-cases/${PENDING_CASE_ID}/semantic-suggestions`,
    );
  });
});

describe('the cross-case guarantee', () => {
  it('never exposes case A data while case B is requested', async () => {
    // The boundary this primitive exists for. Case A's evidence under a
    // header reading case B is not a stale view, it is a wrong one.
    http.respondOnce(pendingCaseDetail);
    const { result, rerender } = renderDetail(PENDING_CASE_ID);
    await waitFor(() => {
      expect(result.current.data).toEqual(pendingCaseDetail);
    });

    const pending = http.defer();
    rerender(DEFERRED_CASE_ID);

    expect(result.current.data).toBeNull();
    expect(result.current.status).toBe('loading');

    await act(async () => {
      pending.resolve(deferredCaseDetail);
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.data).toEqual(deferredCaseDetail);
    });
  });

  it.each([
    ['history', useReviewEvents, eventHistory, caseBEventHistory],
    ['advisory', useSemanticSuggestions, [advisorySuggestion], [caseBSuggestion]],
  ])('applies the same rule to %s', async (_label, hook, caseA, caseB) => {
    http.respondOnce(caseA);
    const { result, rerender } = renderHook((id: string) => hook(id), {
      initialProps: PENDING_CASE_ID,
    });
    await waitFor(() => {
      expect(result.current.data).toEqual(caseA);
    });

    const pending = http.defer();
    rerender(DEFERRED_CASE_ID);
    expect(result.current.data).toBeNull();

    await act(async () => {
      pending.resolve(caseB);
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.data).toEqual(caseB);
    });
  });

  it('clears data again when the selection is cleared', async () => {
    http.alwaysRespond(pendingCaseDetail);
    const { result, rerender } = renderDetail(PENDING_CASE_ID);
    await waitFor(() => {
      expect(result.current.data).not.toBeNull();
    });
    const before = http.callCount();

    rerender(null);

    expect(result.current.status).toBe('idle');
    expect(result.current.data).toBeNull();
    expect(http.callCount()).toBe(before);
  });

  it('aborts the request for the case that was navigated away from', async () => {
    const abandoned = http.defer();
    const { result, rerender } = renderDetail(PENDING_CASE_ID);

    http.respondOnce(deferredCaseDetail);
    rerender(DEFERRED_CASE_ID);
    await waitFor(() => {
      expect(result.current.data).toEqual(deferredCaseDetail);
    });

    // The abandoned read must not surface, as data or as a failure.
    await act(async () => {
      abandoned.resolve(pendingCaseDetail);
      await Promise.resolve();
    });

    expect(result.current.data).toEqual(deferredCaseDetail);
    expect(result.current.failure).toBeNull();
  });

  it('never lets an older response for the same case overwrite a newer one', async () => {
    const stale = http.defer();
    const { result } = renderDetail(PENDING_CASE_ID);

    const fresh = { ...pendingCaseDetail, version: 9 };
    http.respondOnce(fresh);
    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.data).toEqual(fresh);
    });

    await act(async () => {
      stale.resolve(pendingCaseDetail);
      await Promise.resolve();
    });

    expect(result.current.data).toEqual(fresh);
  });
});

describe('refreshing the same case', () => {
  it('keeps the current data on screen and reports updating', async () => {
    http.respondOnce(pendingCaseDetail);
    const { result } = renderDetail();
    await waitFor(() => {
      expect(result.current.status).toBe('ready');
    });

    http.defer();
    act(() => {
      result.current.refresh();
    });

    await waitFor(() => {
      expect(result.current.status).toBe('updating');
    });
    expect(result.current.data).toEqual(pendingCaseDetail);
  });

  it('issues exactly one request per refresh', async () => {
    http.alwaysRespond(pendingCaseDetail);
    const { result } = renderDetail();
    await waitFor(() => {
      expect(result.current.status).toBe('ready');
    });
    const before = http.callCount();

    act(() => {
      result.current.refresh();
    });

    // Wait for the refresh to settle rather than for the call to be recorded:
    // the count rises before the response is applied, so asserting on it first
    // would sample the status mid-flight.
    await waitFor(() => {
      expect(result.current.status).toBe('ready');
    });
    expect(http.callCount()).toBe(before + 1);
  });
});

describe('failures', () => {
  it('exposes a public error code', async () => {
    http.mock.mockResolvedValue(errorResponse('REVIEW_CASE_NOT_FOUND', 404));

    const { result } = renderDetail();

    await waitFor(() => {
      expect(result.current.failure).toEqual({
        kind: 'api',
        code: 'REVIEW_CASE_NOT_FOUND',
        httpStatus: 404,
        message: 'Something failed.',
        details: null,
      });
    });
    expect(result.current.status).toBe('failed');
    expect(result.current.data).toBeNull();
  });

  it('exposes a malformed response as malformed', async () => {
    http.respondOnceWith(textResponse('not json', 200));

    const { result } = renderDetail();

    await waitFor(() => {
      expect(result.current.failure).toEqual({ kind: 'malformed', httpStatus: 200 });
    });
  });

  it('never retries on its own', async () => {
    http.failAlways();

    const { result } = renderDetail();

    await waitFor(() => {
      expect(result.current.failure).toEqual({ kind: 'network' });
    });
    expect(http.callCount()).toBe(1);
  });

  it('keeps the previous value when a refresh of the same case fails', async () => {
    http.respondOnce(pendingCaseDetail);
    const { result } = renderDetail();
    await waitFor(() => {
      expect(result.current.status).toBe('ready');
    });

    http.failOnce();
    act(() => {
      result.current.refresh();
    });

    await waitFor(() => {
      expect(result.current.failure).toEqual({ kind: 'network' });
    });
    expect(result.current.data).toEqual(pendingCaseDetail);
  });

  it('does not carry a failure across a case change', async () => {
    http.failOnce();
    const { result, rerender } = renderDetail(PENDING_CASE_ID);
    await waitFor(() => {
      expect(result.current.failure).not.toBeNull();
    });

    http.respondOnce(deferredCaseDetail);
    rerender(DEFERRED_CASE_ID);

    await waitFor(() => {
      expect(result.current.data).toEqual(deferredCaseDetail);
    });
    expect(result.current.failure).toBeNull();
  });

  it('stays silent when the caller aborts', async () => {
    const abandoned = http.defer();
    const { result, rerender } = renderDetail(PENDING_CASE_ID);

    http.respondOnce(deferredCaseDetail);
    rerender(DEFERRED_CASE_ID);
    await waitFor(() => {
      expect(result.current.status).toBe('ready');
    });

    await act(async () => {
      abandoned.reject(abortError());
      await Promise.resolve();
    });

    expect(result.current.failure).toBeNull();
  });

  it('survives StrictMode double-invoking the effect', async () => {
    http.alwaysRespond(pendingCaseDetail);

    const { result } = renderHook((id: string) => useReviewCase(id), {
      initialProps: PENDING_CASE_ID,
      wrapper: StrictMode,
    });

    await waitFor(() => {
      expect(result.current.data).toEqual(pendingCaseDetail);
    });
    expect(result.current.failure).toBeNull();
  });
});

function abortError(): Error {
  const error = new Error('The operation was aborted.');
  error.name = 'AbortError';
  return error;
}
