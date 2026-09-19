import type { ReactNode } from 'react';

import styles from './EmptyState.module.css';

/**
 * A panel that has loaded successfully and has nothing to show.
 *
 * Distinct from an error on purpose: an empty queue is a normal outcome, and
 * presenting it with an alert would teach reviewers to read "nothing to do"
 * as "something is broken".
 */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className={styles.empty}>
      <p className={styles.title}>{title}</p>
      {description !== undefined && <p className={styles.description}>{description}</p>}
      {action !== undefined && <div className={styles.action}>{action}</div>}
    </div>
  );
}
