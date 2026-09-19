import styles from './ErrorBanner.module.css';

/**
 * A failure the reviewer needs to see now.
 *
 * `role="alert"` because a queue that failed to load changes what the reviewer
 * can do next, and finding that out by noticing an absence is worse than being
 * told. The retry control is optional and its absence is meaningful: some
 * failures cannot be improved by asking again, and offering the button anyway
 * would invite a reviewer to keep trying something that cannot work.
 *
 * `polite` chooses how loudly it is announced. A failure that costs the
 * reviewer the whole screen interrupts; one that costs them a single
 * secondary panel does not, because several panels failing at once would
 * otherwise talk over each other and over whatever the reviewer was reading.
 *
 * Only text this application wrote appears here. Nothing from a response body
 * reaches it -- not an exception message, not a status line, not raw JSON.
 */
export function ErrorBanner({
  title,
  description,
  onRetry,
  retryLabel = 'Retry',
  polite = false,
}: {
  title: string;
  description: string;
  onRetry?: () => void;
  retryLabel?: string;
  polite?: boolean;
}) {
  return (
    <div className={styles.banner} role={polite ? 'status' : 'alert'}>
      <p className={styles.title}>{title}</p>
      <p className={styles.description}>{description}</p>
      {onRetry !== undefined && (
        <button className={styles.retry} type="button" onClick={onRetry}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}
