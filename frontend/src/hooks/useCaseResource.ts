/**
 * One case-scoped read, with the cross-case guarantee the workspace depends on.
 *
 * The three workspace panels -- detail, advisory, history -- each read a
 * separate public resource for one review case, and each needs identical
 * request discipline. This is that discipline, written once; the typed
 * wrappers beside it supply the endpoint and the response type.
 *
 * **Data is bound to the case it was fetched for.** The loaded value is
 * exposed only while the id it came from is still the requested id. That is
 * the whole reason this primitive exists rather than reusing the queue's
 * hook: the queue can safely keep the previous page on screen while the next
 * loads, because the panel says it is updating and the rows are still true
 * rows. A case workspace cannot. Case A's evidence under a header reading
 * Case B is not a stale view, it is a wrong one -- and with customer-derived
 * evidence on screen, attributing it to the wrong pair is the kind of mistake
 * that ends in a wrong merge. The previous value stays in state, but nothing
 * can read it once the selection moves.
 *
 * **A refresh of the same case keeps its data.** The request key includes a
 * refresh counter, so re-reading the same case produces a new request while
 * the id -- and therefore the exposed data -- stays put. That is the
 * difference between `updating` and `loading`.
 *
 * **Nothing happens without a selection.** A null id issues no request at
 * all and reports `idle`.
 *
 * Ordering and cancellation follow the queue hook: an abort signal plus a
 * monotonic request id, so a superseded response can neither be reported as a
 * failure nor overwrite a newer one. There is no retry and no polling.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { RequestOptions } from '../api/client';
import { failureFromError, type ApiFailure } from '../api/errors';

/**
 * What the panel should render.
 *
 * - `idle` — no case is selected; nothing was requested.
 * - `loading` — a first read for this case is in flight; `data` is null.
 * - `updating` — a re-read of the same case is in flight over its own data.
 * - `ready` — `data` belongs to the requested case.
 * - `failed` — the last read for this case failed. `data` is this case's
 *   previous value when a refresh failed over it, and null otherwise.
 */
export type CaseResourceStatus = 'idle' | 'loading' | 'updating' | 'ready' | 'failed';

export interface CaseResource<T> {
  status: CaseResourceStatus;
  /** Never another case's value. Null unless it belongs to the requested id. */
  data: T | null;
  failure: ApiFailure | null;
  /** Re-read this case once. Never called automatically. */
  refresh: () => void;
}

/** A client read that is scoped to one review case. */
export type CaseReader<T> = (reviewCaseId: string, options?: RequestOptions) => Promise<T>;

interface Held<T> {
  key: string;
  reviewCaseId: string;
  value: T;
}

interface Refused {
  key: string;
  failure: ApiFailure;
}

export function useCaseResource<T>(
  reviewCaseId: string | null,
  read: CaseReader<T>,
): CaseResource<T> {
  const [held, setHeld] = useState<Held<T> | null>(null);
  const [refused, setRefused] = useState<Refused | null>(null);
  const [refreshCount, setRefreshCount] = useState(0);

  const latestRequestId = useRef(0);

  const requestKey = reviewCaseId === null ? null : `${reviewCaseId}|${refreshCount}`;

  useEffect(() => {
    if (reviewCaseId === null || requestKey === null) {
      // Nothing selected: no request, and no stale request left running.
      latestRequestId.current += 1;
      return;
    }

    const controller = new AbortController();
    latestRequestId.current += 1;
    const requestId = latestRequestId.current;

    const isCurrent = () => !controller.signal.aborted && requestId === latestRequestId.current;

    read(reviewCaseId, { signal: controller.signal })
      .then((value) => {
        if (isCurrent()) {
          setHeld({ key: requestKey, reviewCaseId, value });
        }
      })
      .catch((error: unknown) => {
        if (isCurrent()) {
          setRefused({ key: requestKey, failure: failureFromError(error) });
        }
      });

    return () => {
      controller.abort();
    };
  }, [reviewCaseId, requestKey, read]);

  const refresh = useCallback(() => {
    setRefreshCount((count) => count + 1);
  }, []);

  // The guard that makes cross-case leakage impossible: a held value is
  // readable only while its own case is the requested one.
  const data = held !== null && held.reviewCaseId === reviewCaseId ? held.value : null;
  const failure = refused !== null && refused.key === requestKey ? refused.failure : null;

  let status: CaseResourceStatus;
  if (reviewCaseId === null) {
    status = 'idle';
  } else if (failure !== null) {
    status = 'failed';
  } else if (held?.key === requestKey) {
    status = 'ready';
  } else {
    // A read is in flight. Whether that is a first load or a re-read is
    // exactly the question of whether this case already has something on
    // screen, which is what a non-null `data` means here.
    status = data === null ? 'loading' : 'updating';
  }

  return { status, data, failure, refresh };
}
