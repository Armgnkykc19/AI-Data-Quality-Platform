import type { RefObject } from 'react';

import type { HumanReviewDecision } from '../../api/types';
import { humanDecisionLabel } from '../../lib/labels';
import styles from './resolution.module.css';

const CONFIRM_HEADING_ID = 'resolution-confirm-heading';

/**
 * A decision captured at the moment the reviewer asked to confirm it.
 *
 * `expectedVersion` is the load-bearing field. It is the version of the
 * authoritative case detail that was on screen when the decision was chosen,
 * and it is the value the POST will carry. It is never recomputed, never
 * refreshed into a newer value while the confirmation is open, and never
 * incremented. If the authoritative case moves underneath an open
 * confirmation, the panel discards the whole intent rather than submitting it
 * against a version the reviewer never saw -- which is the failure optimistic
 * concurrency exists to prevent, and substituting the new version would be
 * the client re-creating it by hand.
 *
 * The record ids are captured too, so the confirmation states the pair it was
 * built from rather than whatever the detail says at render time. They cannot
 * disagree -- the intent dies when the case or version changes -- and the
 * point is that they cannot come to disagree later either.
 */
export interface ResolutionIntent {
  reviewCaseId: string;
  expectedVersion: number;
  decision: HumanReviewDecision;
  /** Null when no label was entered. Sent verbatim otherwise. */
  reviewerId: string | null;
  recordAId: string;
  recordBId: string;
}

/**
 * The explicit step between choosing a decision and recording one.
 *
 * An inline panel rather than `window.confirm` or a native `<dialog>`: the
 * first cannot state a case id, a pair and a warning, and the second brings
 * modal focus behaviour that differs across browsers and jsdom for no benefit
 * a reviewer would notice here. The panel is a labelled region, it receives
 * focus when it opens, and it is reachable and operable entirely from the
 * keyboard.
 *
 * What it claims is limited to what is true before the request is sent. For a
 * MATCH it says the review API checks authorization -- not that this match is
 * safe, not that these records may be merged, not that the check will pass.
 * For a NO MATCH it claims nothing about contradictions. Any such sentence
 * would be the browser reconstructing a verdict only the Sprint 08 authority
 * can reach, and it would be on screen before the server had been asked.
 */
export function ResolutionConfirmation({
  intent,
  isSubmitting,
  panelRef,
  onConfirm,
  onCancel,
}: {
  intent: ResolutionIntent;
  isSubmitting: boolean;
  panelRef: RefObject<HTMLElement | null>;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const label = humanDecisionLabel(intent.decision);

  return (
    <section
      className={styles.confirmation}
      ref={panelRef}
      tabIndex={-1}
      aria-labelledby={CONFIRM_HEADING_ID}
    >
      <h4 className={styles.confirmationHeading} id={CONFIRM_HEADING_ID}>
        Confirm this decision
      </h4>

      <dl className={styles.summary}>
        <dt className={styles.summaryLabel}>Decision</dt>
        <dd className={styles.summaryValue}>{label}</dd>

        <dt className={styles.summaryLabel}>Case</dt>
        <dd className={styles.summaryValue}>{intent.reviewCaseId}</dd>

        <dt className={styles.summaryLabel}>Record A</dt>
        <dd className={styles.summaryValue}>{intent.recordAId}</dd>

        <dt className={styles.summaryLabel}>Record B</dt>
        <dd className={styles.summaryValue}>{intent.recordBId}</dd>

        {intent.reviewerId !== null && (
          <>
            <dt className={styles.summaryLabel}>Reviewer label</dt>
            <dd className={styles.summaryValue}>{intent.reviewerId}</dd>
          </>
        )}
      </dl>

      <p className={styles.warning}>
        Recording this decision writes a durable resolution to the review queue. This interface
        provides no undo, no reopen and no second decision for a resolved case.
      </p>

      {intent.decision === 'MATCH' && (
        <p className={styles.warning}>Match authorization is checked by the review API.</p>
      )}

      {intent.decision === 'DEFER' && (
        <p className={styles.warning}>
          A Defer decision records the case with the Deferred status. Under the current workflow
          that is where the case stays; it is not a temporary pause in this interface.
        </p>
      )}

      <div className={styles.confirmActions}>
        <button
          className={styles.confirm}
          type="button"
          disabled={isSubmitting}
          onClick={onConfirm}
        >
          Confirm
        </button>
        <button className={styles.cancel} type="button" disabled={isSubmitting} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </section>
  );
}
