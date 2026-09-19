import { useEffect, useRef, useState } from 'react';

import type { HumanReviewDecision, ReviewCaseDetail } from '../../api/types';
import { useResolveReviewCase, type ResolveOutcome } from '../../hooks/useResolveReviewCase';
import { humanDecisionLabel } from '../../lib/labels';
import { ResolutionConfirmation, type ResolutionIntent } from './ResolutionConfirmation';
import { ResolutionControls } from './ResolutionControls';
import { ResolutionNotice } from './ResolutionNotice';
import { resolutionFailureMessage } from './resolutionFailureMessage';
import styles from './resolution.module.css';

/**
 * The human resolution workflow: ask, confirm, send once, then re-read.
 *
 * This component owns the only write in the application, so its whole shape
 * is about what it refuses to do.
 *
 * **Availability comes from the authoritative detail and nothing else.** The
 * controls exist when `detail.status === 'PENDING'`, full stop. Not when the
 * queue row says pending, not when a score sits between thresholds, not when
 * the machine decided REVIEW, not when history shows no resolution. The panel
 * is never handed a suggestion, and it never reads `machine_score`,
 * `machine_decision`, `auto_match_threshold` or `review_threshold` -- so no
 * future edit can quietly make a decision easier or harder to reach because
 * of what the machine thought.
 *
 * **The confirmation is bound to an exact case and version.** The intent is
 * captured when a decision is chosen and is honoured only while the
 * authoritative detail still shows that case at that version. If either
 * moves, the intent is discarded rather than retargeted: submitting it
 * against a newer version would mean recording a decision the reviewer made
 * while looking at something else, which is the precise failure
 * `expected_version` exists to prevent.
 *
 * **Convergence is always a GET.** A success is not painted into local state,
 * and the POST response is not treated as history -- its event carries
 * `event_id: null` by construction. After anything that may have changed the
 * queue, or that leaves the client unable to say whether it did, the panel
 * asks its parent to re-read the authoritative detail, the event history and
 * the queue, and it offers no further decision until those reads settle.
 *
 * **Nothing is ever resent.** There is no retry button, no timeout, no
 * backoff and no automatic second POST on any path -- least of all the
 * uncertain ones, where the first request may already have resolved the case.
 * A second request happens only because a human chose a decision again and
 * confirmed it again.
 */
type PanelState =
  | { kind: 'choosing' }
  | { kind: 'confirming'; intent: ResolutionIntent }
  /** A request settled. `awaitingReads` is true while convergence is owed. */
  | { kind: 'settled'; outcome: ResolveOutcome; awaitingReads: boolean };

export function ResolutionPanel({
  reviewCaseId,
  detail,
  isReadingCase,
  caseReadFailed,
  onReconcile,
}: {
  reviewCaseId: string;
  /** The authoritative detail, or null while it is unavailable. */
  detail: ReviewCaseDetail | null;
  /** A detail or history read is in flight. */
  isReadingCase: boolean;
  /** The last detail or history read failed. */
  caseReadFailed: boolean;
  /** Re-read detail, history and queue. Never called on its own schedule. */
  onReconcile: () => void;
}) {
  const [reviewerLabel, setReviewerLabel] = useState('');
  const [state, setState] = useState<PanelState>({ kind: 'choosing' });
  const resolve = useResolveReviewCase(reviewCaseId);

  const decisionRefs = useRef(new Map<HumanReviewDecision, HTMLButtonElement | null>());
  const confirmationRef = useRef<HTMLElement | null>(null);

  const capturedIntent = state.kind === 'confirming' ? state.intent : null;

  // Derived rather than cleaned up in an effect, so there is no render in
  // which a stale intent is still live. The moment the authoritative detail
  // reports a different case or a different version, this is null and the
  // confirmation cannot be submitted at all.
  const activeIntent =
    capturedIntent !== null &&
    detail !== null &&
    detail.review_case_id === capturedIntent.reviewCaseId &&
    detail.version === capturedIntent.expectedVersion
      ? capturedIntent
      : null;

  // Told rather than swallowed: a confirmation that vanishes silently looks
  // like a click that did not register.
  const intentInvalidated = capturedIntent !== null && detail !== null && activeIntent === null;

  const settled = state.kind === 'settled' ? state : null;
  const isReconciling = settled?.awaitingReads === true && isReadingCase;
  const reconcileFailed =
    settled?.awaitingReads === true && !isReadingCase && caseReadFailed;

  useEffect(() => {
    if (activeIntent !== null) {
      confirmationRef.current?.focus();
    }
  }, [activeIntent]);

  const handleChoose = (decision: HumanReviewDecision) => {
    if (detail === null || detail.status !== 'PENDING') {
      return;
    }
    setState({
      kind: 'confirming',
      intent: {
        reviewCaseId: detail.review_case_id,
        // The version the reviewer is looking at, read from the authoritative
        // detail at the moment of choosing. There is no other source.
        expectedVersion: detail.version,
        decision,
        reviewerId: reviewerLabel === '' ? null : reviewerLabel,
        recordAId: detail.record_a_id,
        recordBId: detail.record_b_id,
      },
    });
  };

  const handleCancel = () => {
    const decision = capturedIntent?.decision;
    setState({ kind: 'choosing' });
    if (decision !== undefined) {
      // The decision buttons stay mounted and enabled while a confirmation is
      // open, so focus can go straight back to the one that opened it.
      decisionRefs.current.get(decision)?.focus();
    }
  };

  const handleOutcome = (outcome: ResolveOutcome) => {
    const awaitingReads =
      outcome.kind === 'recorded' || resolutionFailureMessage(outcome.failure).reconcile;
    setState({ kind: 'settled', outcome, awaitingReads });
    if (awaitingReads) {
      // Reads only. Nothing on any path here sends a second POST.
      onReconcile();
    }
  };

  const handleConfirm = () => {
    if (activeIntent === null) {
      return;
    }
    resolve.submit(
      {
        reviewCaseId: activeIntent.reviewCaseId,
        decision: activeIntent.decision,
        expectedVersion: activeIntent.expectedVersion,
        reviewerId: activeIntent.reviewerId,
      },
      handleOutcome,
    );
  };

  const showControls = detail !== null && detail.status === 'PENDING';
  // A failed *history* read must not cost the reviewer their decision -- the
  // deterministic case detail they are deciding from loaded perfectly well.
  // A failed *detail* read already removes the controls, because the parent
  // passes no detail at all in that state.
  const busy = isReadingCase || resolve.isSubmitting;

  if (!showControls && settled === null && !intentInvalidated && !resolve.isSubmitting) {
    return null;
  }

  return (
    <section className={styles.resolution} aria-labelledby="human-decision-heading">
      <h3 className={styles.heading} id="human-decision-heading">
        Human decision
      </h3>

      {resolve.isSubmitting && (
        <p className={styles.status} role="status">
          Recording decision…
        </p>
      )}

      {intentInvalidated && (
        <ResolutionNotice
          tone="attention"
          title="That confirmation was cancelled."
          description="The case changed while the confirmation was open, so nothing was submitted. Review the current case and choose again."
        />
      )}

      {settled !== null && (
        <SettledNotice
          outcome={settled.outcome}
          isReconciling={isReconciling}
          reconcileFailed={reconcileFailed}
        />
      )}

      {showControls && (
        <>
          <p className={styles.intro}>
            These controls record a human decision against this case. The review API decides
            whether the decision can be applied, and this interface never decides for it.
          </p>
          <ResolutionControls
            reviewerLabel={reviewerLabel}
            onReviewerLabelChange={setReviewerLabel}
            onChoose={handleChoose}
            registerDecisionRef={(decision, node) => {
              decisionRefs.current.set(decision, node);
            }}
            decisionsDisabled={busy}
            // Frozen while a confirmation is open: the label shown on the
            // confirmation is the one that will be sent, and letting it drift
            // underneath would make the two disagree.
            reviewerFieldDisabled={busy || activeIntent !== null}
          />
        </>
      )}

      {activeIntent !== null && (
        <ResolutionConfirmation
          intent={activeIntent}
          isSubmitting={resolve.isSubmitting}
          panelRef={confirmationRef}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
        />
      )}
    </section>
  );
}

/**
 * The outcome of the last submission, plus where convergence got to.
 *
 * A recorded decision reports the decision that was sent, not anything read
 * out of the response: the authoritative statement about this case is the
 * detail panel above, refreshed from `GET`.
 */
function SettledNotice({
  outcome,
  isReconciling,
  reconcileFailed,
}: {
  outcome: ResolveOutcome;
  isReconciling: boolean;
  reconcileFailed: boolean;
}) {
  const followUp = isReconciling
    ? 'Refreshing authoritative case state…'
    : reconcileFailed
      ? 'The current case state could not be re-read, so what is shown below may be out of date. Refresh it before deciding anything else.'
      : undefined;

  if (outcome.kind === 'recorded') {
    return (
      <ResolutionNotice
        tone="success"
        title="Decision recorded."
        description={`The review API accepted the ${humanDecisionLabel(outcome.decision)} decision for this case.`}
        {...(followUp === undefined ? {} : { followUp })}
      />
    );
  }

  const message = resolutionFailureMessage(outcome.failure);
  return (
    <ResolutionNotice
      tone="attention"
      title={message.title}
      description={message.description}
      {...(followUp === undefined ? {} : { followUp })}
    />
  );
}
