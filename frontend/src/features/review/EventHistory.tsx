import { ErrorBanner } from '../../components/ErrorBanner';
import type { ReviewEventRead } from '../../api/types';
import type { CaseResource } from '../../hooks/useCaseResource';
import { formatUtcTimestamp } from '../../lib/formatters';
import { eventTypeLabel } from '../../lib/labels';
import { panelFailureMessage } from './readFailureMessage';
import styles from './review.module.css';

/**
 * The case's append-only history, in the order the API returned it.
 *
 * Order is preserved rather than reversed. The repository sorts by the
 * autoincrement `event_id`, which is append order and stays correct when two
 * events share a timestamp, so oldest-first is what the server means. Showing
 * newest-first would be a transformation this layer invented on top of an
 * ordering the backend deliberately chose — and a review trail reads more
 * naturally forwards anyway.
 *
 * Labels are factual. A resolution event reads "Resolved as Match", never
 * "Approved"; a semantic event reads "Semantic advisory recorded", never
 * "AI suggested". Nothing here says whether an outcome was right.
 *
 * No actor is invented. A `reviewer_id` is shown only on events that publish
 * one, labelled as the unverified string it is; `CASE_CREATED` and advisory
 * events carry none, and the list leaves that blank rather than attributing
 * them to the system or to whoever is looking.
 *
 * The stored audit payload and the database schema version are not published
 * by this endpoint and are not reconstructed here.
 */
export function EventHistory({ resource }: { resource: CaseResource<ReviewEventRead[]> }) {
  const { status, data, failure } = resource;

  return (
    <section className={styles.section} aria-labelledby="history-heading">
      <h3 className={styles.sectionHeading} id="history-heading">
        Event history
      </h3>

      {status === 'loading' && <p className={styles.absent}>Loading history…</p>}

      {failure !== null && <HistoryFailure failure={failure} onRetry={resource.refresh} />}

      {data !== null && data.length === 0 && (
        <p className={styles.absent}>No events have been recorded for this case.</p>
      )}

      {data !== null && data.length > 0 && (
        <ol className={styles.timeline}>
          {data.map((event, index) => (
            <TimelineEntry key={event.event_id ?? `event-${index}`} event={event} />
          ))}
        </ol>
      )}
    </section>
  );
}

function HistoryFailure({
  failure,
  onRetry,
}: {
  failure: NonNullable<CaseResource<unknown>['failure']>;
  onRetry: () => void;
}) {
  const message = panelFailureMessage(failure, 'event history');
  return (
    <ErrorBanner
      title={message.title}
      description={message.description}
      polite
      retryLabel="Retry history"
      {...(message.retryable ? { onRetry } : {})}
    />
  );
}

function TimelineEntry({ event }: { event: ReviewEventRead }) {
  return (
    <li className={styles.timelineItem}>
      <p className={styles.timelineLabel}>{eventTypeLabel(event.event_type)}</p>
      <p className={styles.timelineTime}>{formatUtcTimestamp(event.occurred_at_utc)}</p>

      {event.reviewer_id !== null && (
        <p className={styles.timelineMeta}>
          Reviewer label: <span className={styles.breakAnywhere}>{event.reviewer_id}</span>
        </p>
      )}
      {event.resolution_sequence !== null && (
        <p className={styles.timelineMeta}>Resolution sequence: {event.resolution_sequence}</p>
      )}
      {event.suggestion_id !== null && (
        <p className={styles.timelineMeta}>
          Advisory: <span className={styles.breakAnywhere}>{event.suggestion_id}</span>
        </p>
      )}
    </li>
  );
}
