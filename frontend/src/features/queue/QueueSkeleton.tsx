import styles from './queue.module.css';

const PLACEHOLDER_ROWS = 6;

/**
 * Placeholder rows for the first load, when there is no queue to preserve.
 *
 * `aria-hidden` because these shapes carry no information: the fact that the
 * queue is loading is announced once by the panel's live region, and having a
 * screen reader also walk six empty rows would be noise.
 */
export function QueueSkeleton() {
  return (
    <div className={styles.skeleton} aria-hidden="true">
      {Array.from({ length: PLACEHOLDER_ROWS }, (_, index) => (
        <div className={styles.skeletonRow} key={index} />
      ))}
    </div>
  );
}
