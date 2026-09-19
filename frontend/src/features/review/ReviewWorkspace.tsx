import { ErrorBanner } from '../../components/ErrorBanner';
import type { ReviewCaseSummary } from '../../api/types';
import { useReviewCase, useReviewEvents, useSemanticSuggestions } from '../../hooks/useReviewCase';
import { CaseEvidence } from './CaseEvidence';
import { CaseHeader } from './CaseHeader';
import { EventHistory } from './EventHistory';
import { MachineAssessment } from './MachineAssessment';
import { ResolutionPanel } from './ResolutionPanel';
import { ResolutionSummary } from './ResolutionSummary';
import { ReviewSummary } from './ReviewSummary';
import { SemanticAdvisory } from './SemanticAdvisory';
import { caseFailureMessage, isCaseNotFound } from './readFailureMessage';
import styles from './review.module.css';

/**
 * Everything the API publishes about one case, and nothing more.
 *
 * The workspace reads three independent public resources and composes them.
 * They are not chained: an advisory outage must not hide deterministic
 * evidence that loaded perfectly well, and a history outage must not hide the
 * case. Only the detail is primary, because without it there is no pair, no
 * status and nothing for the other panels to be about.
 *
 * One write lives here, and it lives in exactly one child. `ResolutionPanel`
 * owns the decision controls, the confirmation and the single
 * `POST .../resolve`; this component owns the three reads and hands the panel
 * the authoritative detail plus a way to ask for all of them again. Nothing
 * else on this screen writes, and the panel cannot read anything the
 * workspace does not pass it.
 *
 * The panel is keyed by the selected case and sits outside the branch that
 * chooses between the case, the loading placeholder and the failure banner.
 * Both properties are deliberate. The key means a resolution outcome can
 * never outlive the case it belonged to. Its position means a post-write
 * refresh that itself fails does not take the outcome off screen with it --
 * "we recorded your decision but could not re-read the case" is exactly the
 * state a reviewer most needs to be told about, and it is the one state where
 * the detail read has failed.
 *
 * Two boundaries worth naming:
 *
 * The workspace never reconciles itself with the queue. The detail response
 * is authoritative for what is displayed here, the queue row is authoritative
 * for what is displayed there, and they were fetched at different moments.
 * Where both are available the workspace will *say* they disagree, which is
 * information the reviewer can act on; it will not quietly rewrite either, and
 * it will not re-read the queue on its own.
 *
 * Nothing on this screen is derived. Scores and thresholds are reported,
 * evidence is listed in the server's order, and no field is combined with
 * another to produce a verdict.
 */
export function ReviewWorkspace({
  reviewCaseId,
  selectedQueueSummary,
  onRefreshQueue,
}: {
  reviewCaseId: string | null;
  /** The queue row for this case, only when it is in the visible page. */
  selectedQueueSummary: ReviewCaseSummary | null;
  onRefreshQueue: () => void;
}) {
  const detail = useReviewCase(reviewCaseId);
  const suggestions = useSemanticSuggestions(reviewCaseId);
  const history = useReviewEvents(reviewCaseId);

  const refreshCase = () => {
    // One read each, explicitly. This never touches the queue: the queue has
    // its own control, and a button that secretly did both would make the
    // reviewer unable to predict what they are about to request.
    detail.refresh();
    suggestions.refresh();
    history.refresh();
  };

  /**
   * Converge on server state after a write, or after one whose result is
   * unknown.
   *
   * Detail and history, because a resolution changes both. The queue, because
   * a resolved case leaves the pending filter and the reviewer should not be
   * looking at a row that no longer belongs there.
   *
   * Not the semantic suggestions. They are immutable advisory observations
   * Sprint 09 recorded before any of this, and resolving a case cannot change
   * them -- so re-reading them would be a request whose answer is known in
   * advance, issued at the exact moment the reviewer is least interested in
   * advisory content.
   */
  const reconcileAfterWrite = () => {
    detail.refresh();
    history.refresh();
    onRefreshQueue();
  };

  if (reviewCaseId === null) {
    return (
      <Shell>
        <p className={styles.placeholder}>Select a review case to inspect its details.</p>
      </Shell>
    );
  }

  const caseDetail = detail.data;
  // The case is shown only when the primary read currently holds. A refresh
  // that failed over previously loaded data still takes the case off screen:
  // what is held is then known to be behind the server, and a reviewer
  // deciding from it would be deciding from something stale.
  const showCase = detail.failure === null && caseDetail !== null;

  const isStaleQueueRow =
    showCase && selectedQueueSummary !== null && selectedQueueSummary.version !== caseDetail.version;

  const isReadingCase =
    detail.status === 'loading' ||
    detail.status === 'updating' ||
    history.status === 'loading' ||
    history.status === 'updating';
  const caseReadFailed = detail.status === 'failed' || history.status === 'failed';

  return (
    <Shell
      actions={
        showCase ? (
          <button
            type="button"
            className={styles.refresh}
            onClick={refreshCase}
            disabled={detail.status === 'updating'}
          >
            Refresh case
          </button>
        ) : undefined
      }
    >
      {detail.failure !== null && (
        <CaseFailure
          failure={detail.failure}
          onRetry={detail.refresh}
          onRefreshQueue={onRefreshQueue}
        />
      )}

      {detail.failure === null && caseDetail === null && (
        <p className={styles.placeholder} role="status">
          Loading case…
        </p>
      )}

      {showCase && (
        <>
          <p className={styles.live} role="status">
            {detail.status === 'updating' ? 'Updating case…' : ''}
          </p>

          {isStaleQueueRow && (
            <p className={styles.divergence}>
              This case changed since the current queue view was loaded. The details below are the
              review API&rsquo;s current state.
            </p>
          )}

          <CaseHeader detail={caseDetail} />
          <MachineAssessment detail={caseDetail} />
          <ReviewSummary detail={caseDetail} />
          <CaseEvidence detail={caseDetail} />
          {caseDetail.resolution !== null && (
            <ResolutionSummary resolution={caseDetail.resolution} />
          )}
        </>
      )}

      <ResolutionPanel
        key={reviewCaseId}
        reviewCaseId={reviewCaseId}
        detail={showCase ? caseDetail : null}
        isReadingCase={isReadingCase}
        caseReadFailed={caseReadFailed}
        onReconcile={reconcileAfterWrite}
      />

      {showCase && (
        <>
          <SemanticAdvisory resource={suggestions} />
          <EventHistory resource={history} />
        </>
      )}
    </Shell>
  );
}

function Shell({
  children,
  actions,
}: {
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <section className={styles.workspace} aria-labelledby="workspace-heading">
      <header className={styles.workspaceHeader}>
        <h2 className={styles.workspaceHeading} id="workspace-heading">
          Review workspace
        </h2>
        {actions}
      </header>
      {children}
    </section>
  );
}

/**
 * The detail read failed, so the whole workspace is unavailable.
 *
 * The advisory and history panels are not rendered alongside it. They are
 * about a case the API says it cannot give us, and showing two empty panels
 * under a missing case would suggest the case exists and merely has no
 * advisory or history.
 *
 * A missing case is the one failure where re-reading the detail cannot help,
 * so it offers to re-read the queue instead -- through the callback the
 * workspace was handed, never by reaching into queue state itself.
 */
function CaseFailure({
  failure,
  onRetry,
  onRefreshQueue,
}: {
  failure: NonNullable<ReturnType<typeof useReviewCase>['failure']>;
  onRetry: () => void;
  onRefreshQueue: () => void;
}) {
  const message = caseFailureMessage(failure);
  const notFound = isCaseNotFound(failure);

  return (
    <ErrorBanner
      title={message.title}
      description={message.description}
      retryLabel={notFound ? 'Refresh queue' : 'Retry'}
      {...(notFound
        ? { onRetry: onRefreshQueue }
        : message.retryable
          ? { onRetry }
          : {})}
    />
  );
}
