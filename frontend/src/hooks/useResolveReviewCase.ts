/**
 * The one write this interface performs, and the rules that keep it survivable.
 *
 * `POST /api/v1/review-cases/{id}/resolve` is not idempotent and it is not
 * reversible: it drives the Sprint 08 authority, appends an audit row, and
 * advances the case version. So this hook is deliberately the smallest thing
 * that can send it, and almost all of its code is refusal.
 *
 * **One request per explicit human action.** A submission that arrives while
 * another is in flight is dropped, synchronously, on a ref rather than on
 * rendered state -- two clicks inside one tick must not become two decisions.
 * There is no retry of any kind: no automatic re-send, no backoff, no timeout
 * loop. A second POST happens only because a human deliberately started a
 * second submission.
 *
 * **No abort.** The reads elsewhere in this app cancel freely, because a
 * cancelled GET costs nothing. A cancelled POST costs the one thing that
 * matters: the request may already have reached the server, so aborting turns
 * a knowable outcome into an unknowable one for no benefit. An in-flight
 * resolution is therefore allowed to finish; what this hook controls is
 * whether anyone is told about it.
 *
 * **A result never surfaces under another case.** The outcome is delivered
 * only while the case it was submitted for is still the selected one. Showing
 * "Decision recorded" over a different pair would be worse than showing
 * nothing, and with customer-derived evidence on screen it is the kind of
 * mistake that ends in a wrong merge.
 *
 * **Nothing here is authoritative.** The success outcome carries the decision
 * that was submitted and not the response body. The response's `case` is a
 * true statement about the case, but the response's `event` carries
 * `event_id: null` by construction, and treating any of it as the rendered
 * final state invites a UI that stops re-reading. Convergence is the caller's
 * job, through the published GETs.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { resolveReviewCase } from '../api/client';
import { failureFromError, type ApiFailure } from '../api/errors';
import type { HumanReviewDecision, ResolveReviewCaseRequest } from '../api/types';

/**
 * One human decision, captured before the request is built.
 *
 * `expectedVersion` must be the version of the authoritative case detail the
 * reviewer was actually shown. This hook never supplies, defaults, increments
 * or re-reads it, and it has no access to anything it could infer it from.
 *
 * `reviewerId` is an unverified audit label. `null` means none was entered; a
 * non-null value is sent exactly as given, because the backend records it
 * verbatim and any normalisation here would silently rewrite a durable row.
 */
export interface ResolveSubmission {
  reviewCaseId: string;
  decision: HumanReviewDecision;
  expectedVersion: number;
  reviewerId: string | null;
}

/**
 * What became of one submission.
 *
 * `recorded` means the API answered 2xx with a body matching the contract.
 * `failed` carries the typed failure, and says nothing about whether the
 * server committed anything -- that distinction belongs to the failure
 * mapping, which is the only place that decides what a given failure implies.
 */
export type ResolveOutcome =
  | { kind: 'recorded'; decision: HumanReviewDecision }
  | { kind: 'failed'; decision: HumanReviewDecision; failure: ApiFailure };

export interface ResolveController {
  /** True while this hook's own request is in flight. */
  isSubmitting: boolean;
  /**
   * Send exactly one resolution, or do nothing.
   *
   * Dropped without a request when another submission is in flight, or when
   * the submission is for a case other than the selected one. `onOutcome` is
   * invoked at most once, and never after the selection has moved on.
   */
  submit: (submission: ResolveSubmission, onOutcome: (outcome: ResolveOutcome) => void) => void;
}

export function useResolveReviewCase(reviewCaseId: string): ResolveController {
  const [isSubmitting, setIsSubmitting] = useState(false);

  // A ref, not state: the block has to hold within a single tick, before any
  // re-render could disable a button.
  const inFlight = useRef(false);

  // The selection as of the last commit. Read when a request settles, which
  // is always later than the commit that set it.
  const selected = useRef(reviewCaseId);
  useEffect(() => {
    selected.current = reviewCaseId;
  }, [reviewCaseId]);

  const submit = useCallback(
    (submission: ResolveSubmission, onOutcome: (outcome: ResolveOutcome) => void) => {
      if (inFlight.current) {
        return;
      }
      if (submission.reviewCaseId !== selected.current) {
        return;
      }

      inFlight.current = true;
      setIsSubmitting(true);

      // Built here rather than by the caller so there is exactly one place
      // where a browser decision becomes the published request body.
      const body: ResolveReviewCaseRequest = {
        decision: submission.decision,
        expected_version: submission.expectedVersion,
        reviewer_id: submission.reviewerId,
      };

      const settle = (outcome: ResolveOutcome) => {
        inFlight.current = false;
        setIsSubmitting(false);
        if (submission.reviewCaseId !== selected.current) {
          // The request happened and may well have been applied. It is simply
          // not this screen's news any more, and the case's own reads are
          // what will report it if the reviewer comes back.
          return;
        }
        onOutcome(outcome);
      };

      // No `.catch` after `.then`: a throw from `onOutcome` must not be
      // reported as a failed resolution.
      resolveReviewCase(submission.reviewCaseId, body).then(
        () => {
          settle({ kind: 'recorded', decision: submission.decision });
        },
        (error: unknown) => {
          settle({
            kind: 'failed',
            decision: submission.decision,
            failure: failureFromError(error),
          });
        },
      );
    },
    [],
  );

  return { isSubmitting, submit };
}
