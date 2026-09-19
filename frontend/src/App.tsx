import { useEffect, useState } from 'react';

import { DEFAULT_PAGE_LIMIT, type ReviewStatus } from './api/types';
import { QueuePanel } from './features/queue/QueuePanel';
import { ReviewWorkspace } from './features/review/ReviewWorkspace';
import { useReviewCases } from './hooks/useReviewCases';
import styles from './App.module.css';

/**
 * The reviewer workspace, and the only place queue coordination lives.
 *
 * Three pieces of state, deliberately here rather than inside the queue
 * panel: the status filter, the page offset, and the selected case. They have
 * to move together -- changing a filter resets the page, and from Phase C the
 * selected case drives a second panel -- so a single owner is what keeps them
 * from disagreeing. No store, no context, no router: one screen, three values.
 *
 * `PENDING` is the initial filter because this is a work queue and unresolved
 * cases are what a reviewer came for. It is a product default and nothing
 * more: the API has no notion of a default status, and "All" omits the
 * parameter entirely rather than sending some neutral value.
 *
 * The page size is the API's own default, so the first request asks for
 * exactly what the server would have chosen anyway, and every page step after
 * that follows the `limit` the server echoed back.
 */

const PAGE_LIMIT = DEFAULT_PAGE_LIMIT;

export default function App() {
  const [statusFilter, setStatusFilter] = useState<ReviewStatus | null>('PENDING');
  const [offset, setOffset] = useState(0);
  const [selectedReviewCaseId, setSelectedReviewCaseId] = useState<string | null>(null);

  const queue = useReviewCases({ status: statusFilter, limit: PAGE_LIMIT, offset });
  const { data } = queue;

  // Recover from a page that has fallen off the end of the queue.
  //
  // The queue can shrink between two requests -- cases are registered and
  // resolved outside this screen -- leaving the reviewer on an offset the
  // filtered set no longer reaches. The server reports that honestly as an
  // empty page that still counts the whole match, and the only sensible
  // answer is to go back to the first page.
  //
  // This cannot loop. It reacts to the offset the response echoed, not to the
  // state value, and it fires only when that echoed offset is above zero; the
  // recovery request comes back reporting offset 0, where the condition is
  // false whatever else the response says.
  //
  // The rule below is suppressed rather than worked around, and the reasons
  // the usual escapes do not apply are worth recording. The value cannot be
  // derived during render: an offset derived from "is the current page empty"
  // flips back the moment the recovery page arrives non-empty, which
  // oscillates rather than settles. A render-phase adjustment trades this
  // rule for `set-state-in-render`. Reacting inside the fetch callback would
  // mean handing the offset's owner a subscription into the data hook, which
  // is a larger coupling than the one extra render this costs -- and it costs
  // that render only in the rare case where the queue shrank underneath the
  // reviewer.
  useEffect(() => {
    if (data === null) {
      return;
    }
    if (data.total > 0 && data.count === 0 && data.offset > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setOffset(0);
    }
  }, [data]);

  // The queue row for the selected case, only when it is on the visible page.
  //
  // Handed to the workspace so it can compare the summary's version against
  // the detail's and say when the two disagree. Deliberately null when the
  // selection is outside the current slice: there is nothing to compare, and
  // a comparison against a row from a different filter or page would report a
  // divergence that says more about where the reviewer navigated than about
  // the case.
  const selectedQueueSummary =
    data?.items.find((item) => item.review_case_id === selectedReviewCaseId) ?? null;

  const handleStatusFilterChange = (status: ReviewStatus | null) => {
    setStatusFilter(status);
    // A page number means nothing across two different filters, and page four
    // of the deferred cases is not page four of the pending ones.
    setOffset(0);
  };

  return (
    <div className={styles.page}>
      <header className={styles.pageHeader}>
        <div className={styles.notice} role="note">
          <p className={styles.noticeTitle}>Local reviewer tool — not authenticated.</p>
          <p className={styles.noticeBody}>
            This interface talks to a review API bound to localhost. It has no authentication, no
            verified reviewer identity, and no tenant isolation. It is not internet-ready.
          </p>
        </div>
        <h1 className={styles.title}>Reviewer UI</h1>
      </header>

      <main className={styles.workspace}>
        <QueuePanel
          data={data}
          isInitialLoading={queue.isInitialLoading}
          isUpdating={queue.isUpdating}
          failure={queue.failure}
          statusFilter={statusFilter}
          selectedReviewCaseId={selectedReviewCaseId}
          onStatusFilterChange={handleStatusFilterChange}
          onOffsetChange={setOffset}
          onSelect={setSelectedReviewCaseId}
          onRefresh={queue.refresh}
        />

        <ReviewWorkspace
          reviewCaseId={selectedReviewCaseId}
          selectedQueueSummary={selectedQueueSummary}
          onRefreshQueue={queue.refresh}
        />
      </main>
    </div>
  );
}
