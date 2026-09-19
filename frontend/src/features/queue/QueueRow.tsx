import { StatusPill } from '../../components/StatusPill';
import type { ReviewCaseSummary } from '../../api/types';
import { formatRelativeTime, formatScore, formatUtcTimestamp } from '../../lib/formatters';
import styles from './queue.module.css';

/**
 * One queue row, built from `ReviewCaseSummary` and nothing else.
 *
 * The summary carries no evidence, no thresholds, no machine reason and no
 * resolution, and none of those are inferred here. In particular the score is
 * rendered and never judged: the thresholds that would make it mean something
 * are not part of this DTO, and deciding what a score implies is Sprint 08's
 * job, not a queue row's.
 *
 * A real `<button>` inside a list item, rather than a div with a click
 * handler: it is focusable, operable with Enter and Space, and announced as
 * actionable without a line of ARIA. Selection is exposed with `aria-current`
 * and repeated as text, so it survives both a screen reader and a reviewer who
 * cannot separate the highlight from the background.
 */
export function QueueRow({
  summary,
  isSelected,
  onSelect,
}: {
  summary: ReviewCaseSummary;
  isSelected: boolean;
  onSelect: (reviewCaseId: string) => void;
}) {
  const updatedAt = formatUtcTimestamp(summary.updated_at_utc);

  return (
    <li>
      <button
        type="button"
        className={`${styles.row} ${isSelected ? styles.rowSelected : ''}`}
        aria-current={isSelected}
        onClick={() => {
          onSelect(summary.review_case_id);
        }}
      >
        {isSelected && <span className="visually-hidden">Selected. </span>}

        <span className={styles.rowPair}>
          {summary.record_a_id}
          <span aria-hidden="true"> ↔ </span>
          <span className="visually-hidden"> and </span>
          {summary.record_b_id}
        </span>

        <span className={styles.rowMeta}>
          <StatusPill status={summary.status} />
          <span className={styles.rowScore}>
            <span className="visually-hidden">Machine score </span>
            {formatScore(summary.machine_score)}
          </span>
          {/* Relative time is easier to scan; the exact UTC instant stays
              reachable, because that is what the audit trail records. */}
          <span className={styles.rowTime} title={updatedAt}>
            <span className="visually-hidden">Updated </span>
            {formatRelativeTime(summary.updated_at_utc, new Date())}
          </span>
        </span>

        <span className={styles.rowId} title={summary.review_case_id}>
          {summary.review_case_id}
        </span>
      </button>
    </li>
  );
}
