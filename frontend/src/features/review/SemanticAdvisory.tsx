import { ErrorBanner } from '../../components/ErrorBanner';
import type { SemanticSuggestionRead } from '../../api/types';
import type { CaseResource } from '../../hooks/useCaseResource';
import { formatUtcTimestamp } from '../../lib/formatters';
import { semanticSuggestionLabel } from '../../lib/labels';
import { panelFailureMessage } from './readFailureMessage';
import styles from './review.module.css';

/**
 * Stored Sprint 09 observations. They decided nothing, and the panel says so.
 *
 * This is the boundary Sprint 12 is most at risk of blurring, so the design
 * is defensive on purpose. The heading names the panel "AI advisory", every
 * entry is prefixed with the word "Advisory", and a standing note states that
 * the reviewer decides and the backend is authoritative. Nothing is styled
 * like the status pill, nothing is phrased as an instruction, and
 * `SUGGEST_MATCH` reads "Suggest match" rather than "Match these records" or
 * "Recommended".
 *
 * Three things are absent by contract rather than by oversight. There is no
 * explanation: Sprint 09 never stores the model's prose, because prose can
 * repeat values out of untrusted customer records, and reconstructing one
 * from the reason codes would invent text the database never held. There is
 * no provider payload. And there is no way to generate a suggestion -- Sprint
 * 11 publishes no such endpoint, so the panel offers no regeneration control.
 *
 * A `PROVIDER_FAILURE` entry is an advisory outcome, not an API error: the
 * endpoint answered 200 and this is what it recorded. It is rendered inside
 * the list as a failed observation and never escalated to a panel error,
 * because an advisory that could not be produced says nothing about the
 * deterministic evidence the reviewer actually came to read.
 */
export function SemanticAdvisory({
  resource,
}: {
  resource: CaseResource<SemanticSuggestionRead[]>;
}) {
  const { status, data, failure } = resource;

  return (
    <section className={styles.section} aria-labelledby="advisory-heading">
      <h3 className={styles.sectionHeading} id="advisory-heading">
        AI advisory
      </h3>
      <p className={styles.sectionNote}>
        Advisory only. These observations decided nothing: the review status above is the only
        record of what was decided, and the review API remains the authority on any decision.
      </p>

      {status === 'loading' && <p className={styles.absent}>Loading advisory…</p>}

      {failure !== null && <PanelFailure failure={failure} onRetry={resource.refresh} />}

      {data !== null && data.length === 0 && (
        <p className={styles.absent}>No persisted AI advisory is available for this case.</p>
      )}

      {data !== null && data.length > 0 && (
        <ul className={styles.advisoryList}>
          {data.map((suggestion) => (
            <AdvisoryEntry key={suggestion.suggestion_id} suggestion={suggestion} />
          ))}
        </ul>
      )}
    </section>
  );
}

function PanelFailure({
  failure,
  onRetry,
}: {
  failure: NonNullable<CaseResource<unknown>['failure']>;
  onRetry: () => void;
}) {
  const message = panelFailureMessage(failure, 'AI advisory');
  return (
    <ErrorBanner
      title={message.title}
      description={message.description}
      polite
      retryLabel="Retry advisory"
      {...(message.retryable ? { onRetry } : {})}
    />
  );
}

/**
 * One stored observation.
 *
 * Every entry carries the word "Advisory" in its own line rather than
 * relying on the panel heading alone, so a suggestion read out of context --
 * by a screen reader moving through the list, or by someone scrolling past
 * the heading -- still arrives labelled.
 */
function AdvisoryEntry({ suggestion }: { suggestion: SemanticSuggestionRead }) {
  return (
    <li className={styles.advisoryItem}>
      <p className={styles.advisoryVerdict}>
        <span className={styles.advisoryTag}>Advisory</span>{' '}
        {semanticSuggestionLabel(suggestion.suggestion)}
      </p>

      <p className={styles.fieldLabel}>Reason codes</p>
      {suggestion.reason_codes.length === 0 ? (
        <p className={styles.absent}>None published.</p>
      ) : (
        <ul className={styles.reasonCodes}>
          {suggestion.reason_codes.map((code, index) => (
            <li className={styles.breakAnywhere} key={`${code}-${index}`}>
              {code}
            </li>
          ))}
        </ul>
      )}

      <dl className={styles.facts}>
        {suggestion.failure_code !== null && (
          <div className={styles.fact}>
            <dt className={styles.factLabel}>Advisory failure</dt>
            <dd className={`${styles.factValue} ${styles.breakAnywhere}`}>
              {suggestion.failure_code}
            </dd>
          </div>
        )}
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Provider</dt>
          <dd className={`${styles.factValue} ${styles.breakAnywhere}`}>{suggestion.provider}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Requested model</dt>
          <dd className={`${styles.factValue} ${styles.breakAnywhere}`}>
            {suggestion.requested_model}
          </dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Live provider call</dt>
          <dd className={styles.factValue}>{suggestion.live ? 'Yes' : 'No'}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.factLabel}>Recorded</dt>
          <dd className={styles.factValue}>{formatUtcTimestamp(suggestion.created_at_utc)}</dd>
        </div>
      </dl>

      {suggestion.failure_code !== null && (
        <p className={styles.sectionNote}>
          The advisory could not be produced. The deterministic evidence on this page is
          unaffected.
        </p>
      )}
    </li>
  );
}
