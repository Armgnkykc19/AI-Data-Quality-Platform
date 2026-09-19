import styles from './App.module.css';

/**
 * The Phase A application shell.
 *
 * It renders the page frame and the one thing that is true today: this is a
 * local, unauthenticated tool, and the reviewer workspace does not exist yet.
 *
 * Nothing is mocked up here on purpose. A placeholder queue, a sample case, or
 * a statistics strip filled with invented numbers would all be indistinguishable
 * from working software in a screenshot, and the data this product handles is
 * customer-derived review evidence. An empty frame that says what is missing is
 * more honest than a convincing one that says nothing.
 *
 * The local-only notice is not Phase A scaffolding to be removed later. It
 * states a property that holds for the whole of Sprint 12: there is no
 * authentication, `reviewer_id` is an unverified label, and the API is bound to
 * loopback. Sprint 13 owns the identity boundary that makes it removable.
 */
export default function App() {
  return (
    <div className={styles.page}>
      <div className={styles.notice} role="note">
        <p className={styles.noticeTitle}>Local reviewer tool — not authenticated.</p>
        <p className={styles.noticeBody}>
          This interface talks to a review API bound to localhost. It has no authentication, no
          verified reviewer identity, and no tenant isolation. It is not internet-ready.
        </p>
      </div>

      <main>
        <h1 className={styles.title}>Reviewer UI</h1>
        <p className={styles.lede}>
          The browser client for the persistent human review queue. The reviewer workspace is
          not built yet; this build contains the typed API boundary it will run on.
        </p>

        <section className={styles.panel} aria-labelledby="phase-a-heading">
          <h2 className={styles.panelTitle} id="phase-a-heading">
            Foundation only
          </h2>
          <p className={styles.panelBody}>
            The review queue, case workspace, advisory semantic suggestions, event history and
            decision controls arrive in later phases. No review data is loaded or displayed here.
          </p>
        </section>
      </main>
    </div>
  );
}
