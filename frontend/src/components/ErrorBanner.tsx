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
 * Only text this application wrote appears here. Nothing from a response body
 * reaches it -- not an exception message, not a status line, not raw JSON.
 */
export function ErrorBanner({
  title,
  description,
  onRetry,
  retryLabel = 'Retry',
}: {
  title: string;
  description: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className={styles.banner} role="alert">
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
