import type { ReviewResolutionRead } from '../../api/types';
import { reviewStatusLabel } from '../../lib/labels';
import styles from './review.module.css';

/**
 * The human decision already recorded against this case, read-only.
 *
 * Rendered only when the detail response carries one. A case with
 * `resolution: null` shows nothing here rather than an empty scaffold, so an
 * unresolved case never looks like a resolved one with blanks.
 *
 * `reviewer_id` is labelled "Reviewer label" and never "Reviewer", "User" or
 * "Account". Sprint 11 has no authentication: the value is whatever string a
 * client sent, recorded verbatim into an append-only audit row. Presenting it
 * as an identity would attribute a decision to a person the system never
 * verified. Sprint 13 owns that boundary.
 *
 * `human_decision` uses the decision vocabulary, which is why `DEFER` can
 * appear here while the case status beside it reads `DEFERRED`.
 */
export function ResolutionSummary({ resolution }: { resolution: ReviewResolutionRead }) {
  return (
    <section className={styles.section} aria-labelledby="resolution-heading">
      <h3 className={styles.sectionHeading} id="resolution-heading">
        Recorded decision
      </h3>

      <dl className={styles.facts}>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Human decision</dt>
          <dd className={styles.factValue}>{humanDecisionLabel(resolution.human_decision)}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Reviewer label</dt>
          <dd className={styles.factValue}>
            {resolution.reviewer_id === null || resolution.reviewer_id.trim() === '' ? (
              <span className={styles.absent}>None recorded</span>
            ) : (
              <span className={styles.breakAnywhere}>{resolution.reviewer_id}</span>
            )}
          </dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Resolution sequence</dt>
          <dd className={styles.factValue}>{resolution.resolution_sequence}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Downstream action</dt>
          <dd className={`${styles.factValue} ${styles.breakAnywhere}`}>
            {resolution.downstream_action}
          </dd>
        </div>
      </dl>

      <p className={styles.sectionNote}>
        Recorded by the review API. This interface does not change decisions.
      </p>
    </section>
  );
}

/**
 * `DEFER` reads "Defer" here, not "Deferred".
 *
 * The status pill in the header already says `DEFERRED`; this field is the
 * decision that produced it, and relabelling one as the other would erase a
 * distinction the API is careful to keep.
 */
function humanDecisionLabel(decision: ReviewResolutionRead['human_decision']): string {
  return decision === 'DEFER' ? 'Defer' : reviewStatusLabel(decision);
}
