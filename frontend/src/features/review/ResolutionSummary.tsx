import type { ReviewResolutionRead } from '../../api/types';
import { humanDecisionLabel } from '../../lib/labels';
import styles from './review.module.css';

/**
 * The human decision already recorded against this case, read-only.
 *
 * Rendered only when the detail response carries one. A case with
 * `resolution: null` shows nothing here rather than an empty scaffold, so an
 * unresolved case never looks like a resolved one with blanks.
 *
 * `reviewer_id` is labelled "Reviewer label" and never "Reviewer", "User" or
 * "Account". The API records the authenticated user id on a successful
 * resolve. This panel still displays the stored field as a label rather than
 * as a signed-in account, because this local UI does not authenticate.
 *
 * `human_decision` uses the decision vocabulary, which is why `DEFER` can
 * appear here while the case status beside it reads `DEFERRED`. The spelling
 * comes from the shared decision labels, so the decision a reviewer submits
 * and the decision this panel reports back can never drift apart.
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
