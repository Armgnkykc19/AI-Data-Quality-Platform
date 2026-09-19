import { HUMAN_REVIEW_DECISIONS, MAX_REVIEWER_ID_LENGTH } from '../../api/types';
import type { HumanReviewDecision } from '../../api/types';
import { humanDecisionLabel } from '../../lib/labels';
import styles from './resolution.module.css';

const REVIEWER_INPUT_ID = 'resolution-reviewer-label';
const REVIEWER_NOTE_ID = 'resolution-reviewer-note';

/**
 * The three human decisions, and the unverified label recorded beside them.
 *
 * Rendered only for a case the authoritative detail reports as `PENDING`; the
 * decision to render it at all is the panel's, and this component has no
 * access to a status, a score, a threshold or an advisory to second-guess it
 * with. That absence is the design. Nothing reaches this file that could make
 * one of the buttons look more correct than another.
 *
 * The three are laid out in the published `HUMAN_REVIEW_DECISIONS` order and
 * share one class. None is preselected, none carries a pressed or selected
 * state, and none is ever reordered or emphasised -- not by a semantic
 * suggestion, not by the machine score, not by where that score fell relative
 * to the thresholds. There is no "recommended action" here and there is no
 * code path that could produce one.
 *
 * Clicking a decision opens a confirmation. It sends nothing.
 */
export function ResolutionControls({
  reviewerLabel,
  onReviewerLabelChange,
  onChoose,
  registerDecisionRef,
  decisionsDisabled,
  reviewerFieldDisabled,
}: {
  reviewerLabel: string;
  onReviewerLabelChange: (value: string) => void;
  onChoose: (decision: HumanReviewDecision) => void;
  /** Lets the panel put focus back on a decision after a cancelled confirmation. */
  registerDecisionRef: (decision: HumanReviewDecision, node: HTMLButtonElement | null) => void;
  decisionsDisabled: boolean;
  reviewerFieldDisabled: boolean;
}) {
  return (
    <>
      <div className={styles.reviewerField}>
        <label className={styles.reviewerLabel} htmlFor={REVIEWER_INPUT_ID}>
          Reviewer label (optional)
        </label>
        <input
          className={styles.reviewerInput}
          id={REVIEWER_INPUT_ID}
          type="text"
          value={reviewerLabel}
          // Input ergonomics only. `review_api` caps the same length, and the
          // server is the authority; nothing here revalidates, trims, case
          // folds or rewrites the value, because the backend records it
          // verbatim into an append-only audit row.
          maxLength={MAX_REVIEWER_ID_LENGTH}
          autoComplete="off"
          spellCheck={false}
          disabled={reviewerFieldDisabled}
          aria-describedby={REVIEWER_NOTE_ID}
          onChange={(event) => {
            onReviewerLabelChange(event.target.value);
          }}
        />
        <p className={styles.reviewerNote} id={REVIEWER_NOTE_ID}>
          This is an unverified label, not an authenticated identity. It is sent exactly as typed
          and recorded with the decision. This interface does not remember it.
        </p>
      </div>

      <div className={styles.decisions} role="group" aria-label="Human decision">
        {HUMAN_REVIEW_DECISIONS.map((decision) => (
          <button
            key={decision}
            className={styles.decision}
            type="button"
            disabled={decisionsDisabled}
            ref={(node) => {
              registerDecisionRef(decision, node);
            }}
            onClick={() => {
              onChoose(decision);
            }}
          >
            {humanDecisionLabel(decision)}
          </button>
        ))}
      </div>
    </>
  );
}
