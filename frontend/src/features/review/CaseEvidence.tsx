import type { ReviewCaseDetail } from '../../api/types';
import { formatScore } from '../../lib/formatters';
import styles from './review.module.css';

/**
 * The three evidence lists, kept apart and kept in the server's order.
 *
 * Supporting and conflicting evidence are separate sections rather than one
 * annotated list, because merging them would invite a reader to net them off
 * against each other -- and nothing here is entitled to say which side wins.
 * No total is computed, no contribution is summed against a penalty, and
 * nothing is hidden for looking weak: an item the engine recorded is an item
 * the reviewer gets to see.
 *
 * Order is the API's. The repository returns evidence in the order Sprint 08
 * produced it, and re-sorting by contribution would be this layer imposing a
 * ranking the backend did not publish.
 *
 * `blocking_key` is the one customer-derived value Sprint 11 publishes -- a
 * normalized email address or a phone fragment. It is rendered as wrapping
 * text and nothing else: it is never a link, never a URL or query parameter,
 * never logged, and never used as a React key, which would put it into
 * component diagnostics. The index plus the structural reason type is enough
 * to key a list that is rendered in a fixed server order.
 */
export function CaseEvidence({ detail }: { detail: ReviewCaseDetail }) {
  return (
    <>
      <section className={styles.section} aria-labelledby="blocking-reasons-heading">
        <h3 className={styles.sectionHeading} id="blocking-reasons-heading">
          Blocking reasons
        </h3>
        <p className={styles.sectionNote}>Why these two records were compared at all.</p>

        {detail.blocking_reasons.length === 0 ? (
          <p className={styles.absent}>No blocking reasons were published for this case.</p>
        ) : (
          <ul className={styles.evidenceList}>
            {detail.blocking_reasons.map((reason, index) => (
              <li className={styles.evidenceItem} key={`${reason.reason_type}-${index}`}>
                <p className={styles.evidenceType}>{reason.reason_type}</p>
                <p className={styles.blockingKey}>
                  <span className={styles.factLabel}>Blocking key </span>
                  <span className={styles.breakAnywhere}>{reason.blocking_key}</span>
                </p>
                <p className={styles.sensitiveNote}>
                  This value comes from the customer records. Treat it as personal data.
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.section} aria-labelledby="supporting-evidence-heading">
        <h3 className={styles.sectionHeading} id="supporting-evidence-heading">
          Supporting evidence
        </h3>

        {detail.supporting_evidence.length === 0 ? (
          <p className={styles.absent}>No supporting evidence was published for this case.</p>
        ) : (
          <ul className={styles.evidenceList}>
            {detail.supporting_evidence.map((item, index) => (
              <li className={styles.evidenceItem} key={`${item.evidence_type}-${index}`}>
                <p className={styles.evidenceType}>{item.evidence_type}</p>
                <p className={styles.prose}>{item.description}</p>
                <p className={styles.evidenceMeta}>
                  <span>Field: {item.field_name}</span>
                  <span>Strength: {item.strength}</span>
                  <span>Contribution: {formatScore(item.contribution)}</span>
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.section} aria-labelledby="conflicting-evidence-heading">
        <h3 className={styles.sectionHeading} id="conflicting-evidence-heading">
          Conflicting evidence
        </h3>

        {detail.conflicting_evidence.length === 0 ? (
          <p className={styles.absent}>
            No conflicting evidence was published for this case.
          </p>
        ) : (
          <ul className={styles.evidenceList}>
            {detail.conflicting_evidence.map((item, index) => (
              <li className={styles.evidenceItem} key={`${item.conflict_type}-${index}`}>
                <p className={styles.evidenceType}>{item.conflict_type}</p>
                <p className={styles.prose}>{item.description}</p>
                <p className={styles.evidenceMeta}>
                  <span>Field: {item.field_name}</span>
                  <span>Severity: {item.severity}</span>
                  <span>Penalty: {formatScore(item.penalty)}</span>
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
