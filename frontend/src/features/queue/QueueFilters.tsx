import { REVIEW_STATUSES, type ReviewStatus } from '../../api/types';
import { reviewStatusLabel } from '../../lib/labels';
import styles from './queue.module.css';

/**
 * The status filter, derived from the published vocabulary.
 *
 * The options are built from `REVIEW_STATUSES`, so there is no second copy of
 * the enum to fall out of step with the backend, and every value sent is one
 * the API matches exactly.
 *
 * "All" is `null` and means the parameter is omitted from the request. It is
 * not a status: sending `status=`, `status=ALL` or `status=null` would each be
 * a 422, because the backend reads the enum exactly rather than interpreting
 * an empty value as "no filter".
 *
 * Filtering itself happens in the database. Nothing here narrows a loaded
 * page client-side, which would silently disagree with `total`.
 */
const FILTER_OPTIONS: ReadonlyArray<{ value: ReviewStatus | null; label: string }> = [
  { value: null, label: 'All' },
  ...REVIEW_STATUSES.map((status) => ({ value: status, label: reviewStatusLabel(status) })),
];

export function QueueFilters({
  selected,
  onChange,
}: {
  selected: ReviewStatus | null;
  onChange: (status: ReviewStatus | null) => void;
}) {
  return (
    <div className={styles.filters} role="group" aria-label="Filter by status">
      {FILTER_OPTIONS.map((option) => {
        const isActive = option.value === selected;
        return (
          <button
            key={option.value ?? 'ALL'}
            type="button"
            className={`${styles.filter} ${isActive ? styles.filterActive : ''}`}
            aria-pressed={isActive}
            onClick={() => {
              onChange(option.value);
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
