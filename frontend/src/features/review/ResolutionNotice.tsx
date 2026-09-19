import styles from './resolution.module.css';

/**
 * What happened to a decision, in this application's own words.
 *
 * `tone` chooses how it is announced and nothing else about what it says. An
 * `attention` notice is an `alert` because a refused, stale or unconfirmed
 * decision changes what the reviewer must do next, and finding that out by
 * noticing an absence would be worse. A recorded decision is a `status`: it
 * is good news, and it does not need to interrupt.
 *
 * `followUp` carries the reconciliation line -- "the case is being re-read",
 * or "it could not be re-read" -- so the outcome and the state of the
 * authoritative refresh are never conflated into one sentence.
 *
 * Every string reaching this component is written by this application. No
 * response body, status code, path, exception text or backend message is
 * rendered here, and there is no prop through which one could be.
 */
export function ResolutionNotice({
  tone,
  title,
  description,
  followUp,
}: {
  tone: 'success' | 'attention';
  title: string;
  description: string;
  followUp?: string;
}) {
  const toneClass = tone === 'success' ? styles.noticeSuccess : styles.noticeAttention;

  return (
    <div
      className={`${styles.notice} ${toneClass}`}
      role={tone === 'success' ? 'status' : 'alert'}
    >
      <p className={styles.noticeTitle}>{title}</p>
      <p className={styles.noticeBody}>{description}</p>
      {followUp !== undefined && <p className={styles.noticeFollowUp}>{followUp}</p>}
    </div>
  );
}
