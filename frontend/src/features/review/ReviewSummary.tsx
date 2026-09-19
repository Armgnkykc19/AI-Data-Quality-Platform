import type { ReviewCaseDetail } from '../../api/types';
import styles from './review.module.css';

/**
 * What Sprint 08 wrote about the case in words.
 *
 * Both fields are rendered exactly as published. The summary is not derived
 * from the evidence lists, and the notes are not derived from anything: Sprint
 * 08 builds both from field names, evidence types and scores, and rebuilding
 * them here would produce a second, competing account of the same case.
 *
 * An empty value is reported as absent rather than as reassurance. "No
 * missing-evidence notes were published" says what the contract supports;
 * "nothing is missing" would be a claim about the records that no published
 * field makes.
 */
export function ReviewSummary({ detail }: { detail: ReviewCaseDetail }) {
  const summary = detail.human_summary.trim();

  return (
    <section className={styles.section} aria-labelledby="review-summary-heading">
      <h3 className={styles.sectionHeading} id="review-summary-heading">
        Review summary
      </h3>

      {summary === '' ? (
        <p className={styles.absent}>No human-review summary was published for this case.</p>
      ) : (
        <p className={styles.prose}>{detail.human_summary}</p>
      )}

      <p className={styles.fieldLabel}>Missing evidence notes</p>
      {detail.missing_evidence_notes.length === 0 ? (
        <p className={styles.absent}>
          No missing-evidence notes were published for this case.
        </p>
      ) : (
        <ul className={styles.notes}>
          {detail.missing_evidence_notes.map((note, index) => (
            <li key={`note-${index}`}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
