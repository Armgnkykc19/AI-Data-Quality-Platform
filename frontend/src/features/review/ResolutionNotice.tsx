import type { RefObject } from 'react';

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
 * or "it could not be re-read" -- and sits deliberately *outside* the live
 * region. The workspace already announces "Updating case…" from its own status
 * region while exactly that refresh is running, and a second live region
 * narrating the same refresh would talk over it. What belongs in the
 * announcement is the outcome; the progress of the refresh is on screen for
 * anyone who wants it, and is announced once, by the region that owns it.
 *
 * `containerRef` exists for focus, not for styling. When an outcome appears,
 * the control the reviewer was using -- the Confirm button, and on a terminal
 * result the whole decision area -- is unmounted underneath them, so something
 * stable has to receive focus or it falls to `<body>`.
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
  containerRef,
}: {
  tone: 'success' | 'attention';
  title: string;
  description: string;
  followUp?: string;
  containerRef?: RefObject<HTMLDivElement | null>;
}) {
  const toneClass = tone === 'success' ? styles.noticeSuccess : styles.noticeAttention;

  return (
    <div className={`${styles.notice} ${toneClass}`} ref={containerRef} tabIndex={-1}>
      <div role={tone === 'success' ? 'status' : 'alert'}>
        <p className={styles.noticeTitle}>{title}</p>
        <p className={styles.noticeBody}>{description}</p>
      </div>
      {followUp !== undefined && <p className={styles.noticeFollowUp}>{followUp}</p>}
    </div>
  );
}
