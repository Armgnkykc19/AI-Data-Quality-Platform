/**
 * One page of the review queue, fetched from the Sprint 11 list endpoint.
 *
 * The hook owns the request, not the query. Which status is filtered, which
 * page is shown and how large a page is are the workspace's decisions; this
 * hook is told them and reports what came back. That split is what keeps
 * selection, filtering and pagination coordinated in one place instead of
 * hidden inside a data layer.
 *
 * Four behaviours are subtle enough to state outright.
 *
 * **Pending is derived, not tracked.** Each query -- including each explicit
 * refresh -- has a request key, and a request is in flight exactly while
 * neither a response nor a failure has been recorded against the current key.
 * Nothing sets a loading flag, so there is no moment where the flag and the
 * query disagree, and a stale `true` cannot outlive the request that set it.
 *
 * **A stale response can never overwrite a newer one.** Every request also
 * takes a monotonically increasing id and is applied only if that id is still
 * the latest. Aborting the superseded request is not sufficient on its own: a
 * response that had already resolved before the abort landed would otherwise
 * arrive late and replace a newer page with an older one.
 *
 * **An aborted request is silent.** Cancelling a read because the reviewer
 * changed filter or page is expected, not a failure, and rendering it as one
 * would flash errors at anyone moving quickly. The `signal.aborted` guard
 * covers this in every form, including the case where cancelling after the
 * headers but before the body makes the client report `malformed` rather than
 * `aborted` -- the request was still cancelled, and its outcome is discarded.
 *
 * **Loaded data survives a transition.** The last successful page is kept
 * whatever the current key is, so changing page, changing filter or refreshing
 * leaves it on screen with an updating indicator beside it rather than
 * blanking the panel. Only the very first load has nothing to show. A failure,
 * by contrast, is scoped to the key that produced it, so it disappears the
 * moment the reviewer asks for something else.
 *
 * What the hook deliberately does not do: retry, poll, cache across mounts,
 * call `fetch` directly, read a machine score, or interpret any domain state.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { listReviewCases } from '../api/client';
import { failureFromError, type ApiFailure } from '../api/errors';
import type { ListReviewCasesParams, ReviewCaseListResponse, ReviewStatus } from '../api/types';

export interface ReviewCasesQuery {
  /** `null` means "All": the status parameter is omitted from the request. */
  status: ReviewStatus | null;
  limit: number;
  offset: number;
}

export interface ReviewCasesResult {
  /** The last page that loaded successfully, or null before the first one. */
  data: ReviewCaseListResponse | null;
  /** A request is in flight and there is nothing to show yet. */
  isInitialLoading: boolean;
  /** A request is in flight over data that is already on screen. */
  isUpdating: boolean;
  /** How the current request ended, if it ended badly. */
  failure: ApiFailure | null;
  /** Re-run the current query once. Never called automatically. */
  refresh: () => void;
}

interface LoadedPage {
  key: string;
  response: ReviewCaseListResponse;
}

interface FailedPage {
  key: string;
  failure: ApiFailure;
}

export function useReviewCases(query: ReviewCasesQuery): ReviewCasesResult {
  const { status, limit, offset } = query;

  const [loaded, setLoaded] = useState<LoadedPage | null>(null);
  const [failed, setFailed] = useState<FailedPage | null>(null);
  // Bumped by refresh() so the same query becomes a new key, and therefore a
  // new request, without any of the query values changing.
  const [refreshCount, setRefreshCount] = useState(0);

  const latestRequestId = useRef(0);

  const requestKey = `${status ?? '*'}|${limit}|${offset}|${refreshCount}`;

  useEffect(() => {
    const controller = new AbortController();
    latestRequestId.current += 1;
    const requestId = latestRequestId.current;

    // Built conditionally: `?status=` is a 422, because the backend matches
    // the status enum exactly and does not read an empty value as "no filter".
    const params: ListReviewCasesParams = { limit, offset };
    if (status !== null) {
      params.status = status;
    }

    const isCurrent = () => !controller.signal.aborted && requestId === latestRequestId.current;

    listReviewCases(params, { signal: controller.signal })
      .then((response) => {
        if (isCurrent()) {
          setLoaded({ key: requestKey, response });
        }
      })
      .catch((error: unknown) => {
        if (isCurrent()) {
          setFailed({ key: requestKey, failure: failureFromError(error) });
        }
      });

    // Runs on unmount, on every query change, and twice per mount under
    // StrictMode. In all three cases the outcome of the cancelled request is
    // discarded by the guard above.
    return () => {
      controller.abort();
    };
  }, [status, limit, offset, requestKey]);

  const refresh = useCallback(() => {
    setRefreshCount((count) => count + 1);
  }, []);

  const data = loaded?.response ?? null;
  const failure = failed?.key === requestKey ? failed.failure : null;
  const isPending = loaded?.key !== requestKey && failed?.key !== requestKey;

  return {
    data,
    isInitialLoading: isPending && data === null,
    isUpdating: isPending && data !== null,
    failure,
    refresh,
  };
}
