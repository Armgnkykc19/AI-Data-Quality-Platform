import type { ReviewCaseDetail } from '../../api/types';
import { formatScore } from '../../lib/formatters';
import styles from './review.module.css';

/**
 * The deterministic engine's numbers, reported and not interpreted.
 *
 * The score and both thresholds are published together because a score means
 * nothing without them. They are shown as three numbers side by side, and
 * deliberately not as a bar, a gauge, a band or a colour: any of those would
 * encode a comparison, and a comparison is a conclusion.
 *
 * There is no "above the review threshold", no distance-to-threshold, no
 * confidence category. Sprint 08 authorization projects a connected component
 * across every AUTO_MATCH edge and every recorded human decision, so what
 * these three numbers imply for *this* pair is not derivable from this pair.
 * A workspace that summarised them would be guessing in the one place where
 * guessing produces a wrong merge.
 *
 * `machine_reason` is rendered as the server wrote it. It is not parsed,
 * matched, keyword-searched or turned into structure.
 */
export function MachineAssessment({ detail }: { detail: ReviewCaseDetail }) {
  const reason = detail.machine_reason.trim();

  return (
    <section className={styles.section} aria-labelledby="machine-assessment-heading">
      <h3 className={styles.sectionHeading} id="machine-assessment-heading">
        Machine assessment
      </h3>

      <dl className={styles.numbers}>
        <div className={styles.number}>
          <dt className={styles.factLabel}>Machine score</dt>
          <dd className={styles.numberValue}>{formatScore(detail.machine_score)}</dd>
        </div>
        <div className={styles.number}>
          <dt className={styles.factLabel}>Review threshold</dt>
          <dd className={styles.numberValue}>{formatScore(detail.review_threshold)}</dd>
        </div>
        <div className={styles.number}>
          <dt className={styles.factLabel}>Auto-match threshold</dt>
          <dd className={styles.numberValue}>{formatScore(detail.auto_match_threshold)}</dd>
        </div>
      </dl>

      <p className={styles.fieldLabel}>Machine reason</p>
      {reason === '' ? (
        <p className={styles.absent}>No machine reason was published for this case.</p>
      ) : (
        <p className={styles.prose}>{detail.machine_reason}</p>
      )}
    </section>
  );
}
