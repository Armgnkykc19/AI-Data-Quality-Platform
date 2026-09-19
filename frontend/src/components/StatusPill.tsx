import type { ReviewStatus } from '../api/types';
import { reviewStatusLabel } from '../lib/status';
import styles from './StatusPill.module.css';

/**
 * A review status, as readable text.
 *
 * The label is always rendered. Colour is a secondary cue layered on top of
 * it, never the carrier of the meaning: a reviewer who cannot distinguish the
 * tints must still be able to tell a pending case from a resolved one, and a
 * screen reader reads the word rather than the class.
 */
export function StatusPill({ status }: { status: ReviewStatus }) {
  return (
    <span className={`${styles.pill} ${styles[status]}`} data-status={status}>
      {reviewStatusLabel(status)}
    </span>
  );
}
