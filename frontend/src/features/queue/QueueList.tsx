import { EmptyState } from '../../components/EmptyState';
import type { ReviewCaseSummary, ReviewStatus } from '../../api/types';
import { reviewStatusLabel } from '../../lib/status';
import { QueueRow } from './QueueRow';
import { QueueSkeleton } from './QueueSkeleton';
import styles from './queue.module.css';

/**
 * The list region: placeholders, an honest empty state, or rows.
 *
 * The two empty states are kept apart because they mean different things. A
 * filtered queue with nothing in it is a filter the reviewer can clear; an
 * unfiltered queue with nothing in it is a queue with no cases.
 *
 * Neither claims an operator action is required. A database with a valid
 * schema that has never had a workflow registered answers this endpoint with
 * an ordinary empty list, indistinguishable from a fully reviewed queue --
 * `REVIEW_QUEUE_NOT_READY` only surfaces when a decision is submitted. Telling
 * a reviewer the queue is unregistered would be a guess.
 */
export function QueueList({
  items,
  isInitialLoading,
  statusFilter,
  selectedReviewCaseId,
  onSelect,
  onClearFilter,
}: {
  items: readonly ReviewCaseSummary[] | null;
  isInitialLoading: boolean;
  statusFilter: ReviewStatus | null;
  selectedReviewCaseId: string | null;
  onSelect: (reviewCaseId: string) => void;
  onClearFilter: () => void;
}) {
  if (isInitialLoading) {
    return (
      <div className={styles.listRegion} aria-busy="true">
        <QueueSkeleton />
      </div>
    );
  }

  if (items === null) {
    return <div className={styles.listRegion} />;
  }

  if (items.length === 0) {
    return (
      <div className={styles.listRegion}>
        {statusFilter === null ? (
          <EmptyState title="No review cases in this queue." />
        ) : (
          <EmptyState
            title={`No cases with status ${reviewStatusLabel(statusFilter)}.`}
            description="Other statuses may still have cases."
            action={
              <button type="button" className={styles.clearFilter} onClick={onClearFilter}>
                Clear filter
              </button>
            }
          />
        )}
      </div>
    );
  }

  return (
    <div className={styles.listRegion}>
      <ul className={styles.list}>
        {items.map((summary) => (
          <QueueRow
            key={summary.review_case_id}
            summary={summary}
            isSelected={summary.review_case_id === selectedReviewCaseId}
            onSelect={onSelect}
          />
        ))}
      </ul>
    </div>
  );
}
