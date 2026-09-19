import { act, renderHook, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useReviewCases, type ReviewCasesQuery } from './useReviewCases';
import { caseListResponse, emptyCaseListResponse } from '../test/fixtures/sprint11';
import { createFetchStub, errorResponse, type FetchStub } from '../test/http';

const PENDING_QUERY: ReviewCasesQuery = { status: 'PENDING', limit: 50, offset: 0 };

let http: FetchStub;

beforeEach(() => {
  http = createFetchStub();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderQueue(initial: ReviewCasesQuery = PENDING_QUERY) {
  return renderHook((query: ReviewCasesQuery) => useReviewCases(query), {
    initialProps: initial,
  });
}

describe('the initial request', () => {
  it('asks for the given status, limit and offset', async () => {
    http.alwaysRespond(caseListResponse);

    const { result } = renderQueue();

    await waitFor(() => {
      expect(result.current.data).toEqual(caseListResponse);
    });
    expect(http.lastUrl()).toBe('/api/v1/review-cases?status=PENDING&limit=50&offset=0');
  });

  it('omits the status parameter entirely for the All filter', async () => {
    // `?status=`, `?status=ALL` and `?status=null` are each a 422: the backend
    // matches the enum exactly rather than reading an empty value as no filter.
    http.alwaysRespond(caseListResponse);

    const { result } = renderQueue({ status: null, limit: 50, offset: 0 });

    await waitFor(() => {
      expect(result.current.data).not.toBeNull();
    });
    expect(http.lastUrl()).toBe('/api/v1/review-cases?limit=50&offset=0');
    expect(http.lastUrl()).not.toContain('status');
  });

  it('reports initial loading before anything has arrived', () => {
    http.defer();

    const { result } = renderQueue();

    expect(result.current.isInitialLoading).toBe(true);
    expect(result.current.isUpdating).toBe(false);
    expect(result.current.data).toBeNull();
  });
});

describe('query changes', () => {
  it('requests the new status from the server when the filter changes', async () => {
    http.alwaysRespond(caseListResponse);
    const { result, rerender } = renderQueue();
    await waitFor(() => {
      expect(result.current.data).not.toBeNull();
    });

    rerender({ status: 'DEFERRED', limit: 50, offset: 0 });

    await waitFor(() => {
      expect(http.lastUrl()).toBe('/api/v1/review-cases?status=DEFERRED&limit=50&offset=0');
    });
  });

  it('keeps the status when only the page changes', async () => {
    http.alwaysRespond(caseListResponse);
    const { result, rerender } = renderQueue();
    await waitFor(() => {
      expect(result.current.data).not.toBeNull();
    });

    rerender({ status: 'PENDING', limit: 50, offset: 50 });

    await waitFor(() => {
      expect(http.lastUrl()).toBe('/api/v1/review-cases?status=PENDING&limit=50&offset=50');
    });
  });

  it('keeps the previous page on screen while the next one loads', async () => {
    // Blanking the panel on every page step would make the queue flicker out
    // of existence each time a reviewer moved through it.
    http.respondOnce(caseListResponse);
    const { result, rerender } = renderQueue();
    await waitFor(() => {
      expect(result.current.data).toEqual(caseListResponse);
    });

    http.defer();
    rerender({ status: 'PENDING', limit: 50, offset: 50 });

    await waitFor(() => {
      expect(result.current.isUpdating).toBe(true);
    });
    expect(result.current.isInitialLoading).toBe(false);
    expect(result.current.data).toEqual(caseListResponse);
  });
});

describe('refresh', () => {
  it('re-runs the current query exactly once', async () => {
    http.alwaysRespond(caseListResponse);
    const { result } = renderQueue({ status: 'MATCH', limit: 50, offset: 100 });
    await waitFor(() => {
      expect(result.current.data).not.toBeNull();
    });
    const before = http.callCount();

    act(() => {
      result.current.refresh();
    });

    await waitFor(() => {
      expect(http.callCount()).toBe(before + 1);
    });
    expect(http.lastUrl()).toBe('/api/v1/review-cases?status=MATCH&limit=50&offset=100');
  });

  it('does not poll: two minutes of clock produce no further request', async () => {
    vi.useFakeTimers();
    try {
      http.alwaysRespond(caseListResponse);
      const { result } = renderQueue();

      // Advancing by a tick also drains the microtasks the fetch chain needs.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(result.current.data).not.toBeNull();
      const after = http.callCount();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(120_000);
      });

      expect(http.callCount()).toBe(after);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('cancellation and ordering', () => {
  it('aborts the superseded request and does not report it as a failure', async () => {
    const first = http.defer();
    const { result, rerender } = renderQueue();
    expect(result.current.isInitialLoading).toBe(true);

    http.respondOnce(caseListResponse);
    rerender({ status: 'MATCH', limit: 50, offset: 0 });

    await waitFor(() => {
      expect(result.current.data).toEqual(caseListResponse);
    });
    // The abort landed on the first request; its rejection must stay silent.
    expect(result.current.failure).toBeNull();
    // Nothing further can arrive from it either.
    await act(async () => {
      first.reject(new Error('already aborted'));
      await Promise.resolve();
    });
    expect(result.current.failure).toBeNull();
  });

  it('never lets an older response overwrite a newer one', async () => {
    // The dangerous ordering: request A is issued, request B supersedes it and
    // completes, and only then does A arrive carrying the previous page.
    const stale = http.defer();
    const { result, rerender } = renderQueue();

    http.respondOnce(emptyCaseListResponse);
    rerender({ status: 'MATCH', limit: 50, offset: 0 });
    await waitFor(() => {
      expect(result.current.data).toEqual(emptyCaseListResponse);
    });

    await act(async () => {
      stale.resolve(caseListResponse);
      await Promise.resolve();
    });

    expect(result.current.data).toEqual(emptyCaseListResponse);
  });

  it('survives StrictMode double-invoking the effect', async () => {
    // React mounts, cleans up and mounts again in development. The first
    // request is aborted by that cleanup and must not surface at all.
    http.alwaysRespond(caseListResponse);

    const { result } = renderHook((query: ReviewCasesQuery) => useReviewCases(query), {
      initialProps: PENDING_QUERY,
      wrapper: StrictMode,
    });

    await waitFor(() => {
      expect(result.current.data).toEqual(caseListResponse);
    });
    expect(result.current.failure).toBeNull();
  });
});

describe('failures', () => {
  it('reports a public error code without retrying', async () => {
    http.mock.mockResolvedValue(errorResponse('REVIEW_STORAGE_UNAVAILABLE', 503));

    const { result } = renderQueue();

    await waitFor(() => {
      expect(result.current.failure).toEqual({
        kind: 'api',
        code: 'REVIEW_STORAGE_UNAVAILABLE',
        httpStatus: 503,
        message: 'Something failed.',
        details: null,
      });
    });
    expect(http.callCount()).toBe(1);
  });

  it('reports an unreachable API as a network failure, once', async () => {
    http.failAlways();

    const { result } = renderQueue();

    await waitFor(() => {
      expect(result.current.failure).toEqual({ kind: 'network' });
    });
    expect(http.callCount()).toBe(1);
    expect(result.current.isInitialLoading).toBe(false);
  });

  it('clears a previous failure once a request succeeds', async () => {
    http.failOnce();
    const { result } = renderQueue();
    await waitFor(() => {
      expect(result.current.failure).not.toBeNull();
    });

    http.respondOnce(caseListResponse);
    act(() => {
      result.current.refresh();
    });

    await waitFor(() => {
      expect(result.current.data).toEqual(caseListResponse);
    });
    expect(result.current.failure).toBeNull();
  });

  it('keeps loaded data when a later refresh fails', async () => {
    http.respondOnce(caseListResponse);
    const { result } = renderQueue();
    await waitFor(() => {
      expect(result.current.data).toEqual(caseListResponse);
    });

    http.failOnce();
    act(() => {
      result.current.refresh();
    });

    await waitFor(() => {
      expect(result.current.failure).toEqual({ kind: 'network' });
    });
    expect(result.current.data).toEqual(caseListResponse);
  });
});
