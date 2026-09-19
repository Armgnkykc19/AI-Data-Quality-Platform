import { StatusPill } from '../../components/StatusPill';
import type { ReviewCaseDetail } from '../../api/types';
import { formatUtcTimestamp } from '../../lib/formatters';
import { machineDecisionLabel } from '../../lib/labels';
import styles from './review.module.css';

/**
 * Who is under review, where the case stands, and when it last moved.
 *
 * The two records are shown as identifiers under explicit "Record A" and
 * "Record B" labels, and that is all there is to show. Sprint 11 publishes no
 * field values -- no name, no email, no phone, no company -- so a card that
 * looked like a customer record would have to invent one. Labelling the ids
 * plainly is the honest shape for what the contract actually carries.
 *
 * Status and machine decision sit side by side but are never merged. One is
 * where the case ended up and the other is what the deterministic engine
 * concluded; they draw from different vocabularies that happen to share two
 * spellings, and the headings keep them apart.
 */
export function CaseHeader({ detail }: { detail: ReviewCaseDetail }) {
  return (
    <header className={styles.caseHeader}>
      <div className={styles.pair}>
        <div className={styles.record}>
          <p className={styles.recordLabel}>Record A</p>
          <p className={styles.recordId}>{detail.record_a_id}</p>
        </div>
        <div className={styles.record}>
          <p className={styles.recordLabel}>Record B</p>
          <p className={styles.recordId}>{detail.record_b_id}</p>
        </div>
      </div>

      <dl className={styles.facts}>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Review status</dt>
          <dd className={styles.factValue}>
            <StatusPill status={detail.status} />
          </dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Machine decision</dt>
          <dd className={styles.factValue}>{machineDecisionLabel(detail.machine_decision)}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Version</dt>
          <dd className={styles.factValue}>{detail.version}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Created</dt>
          <dd className={styles.factValue}>{formatUtcTimestamp(detail.created_at_utc)}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Updated</dt>
          <dd className={styles.factValue}>{formatUtcTimestamp(detail.updated_at_utc)}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Case identifier</dt>
          <dd className={`${styles.factValue} ${styles.monospace}`}>{detail.review_case_id}</dd>
        </div>
      </dl>
    </header>
  );
}
