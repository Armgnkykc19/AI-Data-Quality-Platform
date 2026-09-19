import { ErrorBanner } from '../../components/ErrorBanner';
import type { ApiFailure } from '../../api/errors';
import type { ReviewCaseListResponse, ReviewStatus } from '../../api/types';
import { Pagination } from './Pagination';
import { QueueFilters } from './QueueFilters';
import { QueueList } from './QueueList';
import { queueFailureMessage } from './queueFailureMessage';
import styles from './queue.module.css';

/**
 * The reviewer queue panel. It renders queue state; it owns none of it.
 *
 * Filter, page and selection live in the workspace above, because all three
 * have to stay coordinated with each other and, from Phase C, with the case
 * being inspected. A panel that kept its own copy would be a second source of
 * truth for what the reviewer is looking at.
 *
 * Two presentation decisions are deliberate.
 *
 * A failure does not erase the queue. When a refresh fails over rows that
 * loaded a moment ago, the rows stay and the banner appears above them: the
 * data is still what the server last said, and blanking it would discard
 * something true in order to report something that went wrong.
 *
 * The live region is always mounted and its text changes. A region that is
 * inserted at the moment it has something to say is announced unreliably,
 * which is the usual way a "Loading…" message ends up never being heard.
 */
export function QueuePanel({
  data,
  isInitialLoading,
  isUpdating,
  failure,
  statusFilter,
  selectedReviewCaseId,
  onStatusFilterChange,
  onOffsetChange,
  onSelect,
  onRefresh,
}: {
  data: ReviewCaseListResponse | null;
  isInitialLoading: boolean;
  isUpdating: boolean;
  failure: ApiFailure | null;
  statusFilter: ReviewStatus | null;
  selectedReviewCaseId: string | null;
  onStatusFilterChange: (status: ReviewStatus | null) => void;
  onOffsetChange: (offset: number) => void;
  onSelect: (reviewCaseId: string) => void;
  onRefresh: () => void;
}) {
  const message = failure === null ? null : queueFailureMessage(failure);

  // The selected case may be perfectly real and simply live on another page or
  // outside the current filter. Only Phase C's detail request can establish
  // that a case does not exist, so this says where it is not, not what it is.
  const isSelectionOutsideView =
    selectedReviewCaseId !== null &&
    data !== null &&
    !data.items.some((item) => item.review_case_id === selectedReviewCaseId);

  const liveMessage = isInitialLoading
    ? 'Loading review queue…'
    : isUpdating
      ? 'Updating review queue…'
      : '';

  return (
    <section className={styles.panel} aria-labelledby="queue-heading">
      <header className={styles.header}>
        <h2 className={styles.heading} id="queue-heading">
          Review queue
        </h2>
        <button
          type="button"
          className={styles.refresh}
          onClick={onRefresh}
          // Disabled only while a request is already in flight. It is a spam
          // guard, not a rate limit: the outcome of a second identical request
          // would be discarded anyway.
          disabled={isInitialLoading || isUpdating}
        >
          Refresh queue
        </button>
      </header>

      <QueueFilters selected={statusFilter} onChange={onStatusFilterChange} />

      <p className={styles.live} role="status">
        {liveMessage}
      </p>

      {message !== null && (
        <div className={styles.bannerSlot}>
          <ErrorBanner
            title={message.title}
            description={message.description}
            {...(message.retryable ? { onRetry: onRefresh } : {})}
          />
        </div>
      )}

      {isSelectionOutsideView && (
        <p className={styles.outsideView}>
          Selected case is not in the current queue view. It may be on another page or excluded
          by the status filter.
        </p>
      )}

      <QueueList
        items={data === null ? null : data.items}
        isInitialLoading={isInitialLoading}
        statusFilter={statusFilter}
        selectedReviewCaseId={selectedReviewCaseId}
        onSelect={onSelect}
        onClearFilter={() => {
          onStatusFilterChange(null);
        }}
      />

      {data !== null && data.total > 0 && (
        <Pagination
          // The echoed offset, not the workspace's current one: the range
          // must describe the rows actually on screen, which during an
          // in-flight page change still belong to the previous offset.
          offset={data.offset}
          count={data.count}
          total={data.total}
          limit={data.limit}
          onOffsetChange={onOffsetChange}
        />
      )}
    </section>
  );
}
