import styles from './queue.module.css';

/**
 * Page controls driven entirely by what the last response reported.
 *
 * `count`, `total`, `limit` and `offset` all come from the server. Nothing is
 * accumulated across pages, no page count is guessed, and the step is the
 * `limit` the API echoed rather than a constant repeated here -- if the server
 * ever serves a different page size than was asked for, the controls follow
 * the server.
 *
 * `total` counts the whole filtered set before slicing, which is what makes
 * "showing 1–50 of 137" possible without fetching 137 cases.
 */
export function Pagination({
  offset,
  count,
  total,
  limit,
  onOffsetChange,
}: {
  offset: number;
  count: number;
  total: number;
  limit: number;
  onOffsetChange: (offset: number) => void;
}) {
  const isFirstPage = offset === 0;
  // An empty page is the end of the road even when `total` is larger: there is
  // nothing after a slice that returned nothing.
  const isLastPage = count === 0 || offset + count >= total;

  const range =
    count === 0
      ? `Showing 0 of ${total}`
      : `Showing ${offset + 1}\u2013${offset + count} of ${total}`;

  return (
    <nav className={styles.pagination} aria-label="Queue pages">
      <button
        type="button"
        className={styles.pageButton}
        onClick={() => {
          // Clamped rather than trusted: a shrinking page size must never
          // produce a negative offset, which the API rejects outright.
          onOffsetChange(Math.max(0, offset - limit));
        }}
        disabled={isFirstPage}
      >
        Previous page
      </button>

      <p className={styles.range}>{range}</p>

      <button
        type="button"
        className={styles.pageButton}
        onClick={() => {
          onOffsetChange(offset + limit);
        }}
        disabled={isLastPage}
      >
        Next page
      </button>
    </nav>
  );
}
