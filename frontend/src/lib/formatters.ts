/**
 * Presentation-only conversions. Pure, total, and locale-independent.
 *
 * Two rules hold throughout.
 *
 * Nothing here throws or renders a broken value. A malformed timestamp from
 * the API is returned unchanged rather than displayed as "Invalid Date", and
 * a non-finite score becomes a visible placeholder. An evidence panel that
 * crashes on one odd field is worse than one that shows the field verbatim.
 *
 * Nothing here reads meaning into a number. `formatScore` renders a score; it
 * does not compare it to a threshold, and no function in this file takes a
 * threshold at all. Deciding what a score implies is Sprint 08's job.
 */

/** Shown where a value exists in the contract but is not a usable number. */
export const MISSING_VALUE_PLACEHOLDER = '—';

const SECONDS_PER_MINUTE = 60;
const SECONDS_PER_HOUR = 3600;
const SECONDS_PER_DAY = 86_400;
/** Beyond this, a relative label stops being more useful than a date. */
const RELATIVE_CUTOFF_DAYS = 30;

/**
 * Render a machine score for display.
 *
 * Fixed decimals rather than a locale-aware formatter: scores are compared by
 * eye against two thresholds rendered beside them, and a thousands separator
 * or a comma decimal mark would make that comparison harder, not easier.
 */
export function formatScore(value: number, fractionDigits = 2): string {
  if (!Number.isFinite(value)) {
    return MISSING_VALUE_PLACEHOLDER;
  }
  return value.toFixed(fractionDigits);
}

function pad(value: number, width: number): string {
  return String(value).padStart(width, '0');
}

function parseUtc(isoTimestamp: string): Date | null {
  const parsed = new Date(isoTimestamp);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * Render an API timestamp as a readable UTC instant.
 *
 * The API emits second-resolution UTC (`2026-09-19T14:32:05Z`), and this keeps
 * it in UTC rather than converting to the viewer's zone. Review decisions are
 * audited against server-side timestamps, so showing a local-time rendering of
 * one would mean the screen and the audit trail disagree about when something
 * happened.
 *
 * An unparseable value is returned as it arrived: the raw string is at least
 * true, and it makes a contract problem visible instead of hiding it.
 */
export function formatUtcTimestamp(isoTimestamp: string): string {
  const parsed = parseUtc(isoTimestamp);
  if (parsed === null) {
    return isoTimestamp;
  }
  const date = `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1, 2)}-${pad(parsed.getUTCDate(), 2)}`;
  const time = `${pad(parsed.getUTCHours(), 2)}:${pad(parsed.getUTCMinutes(), 2)}:${pad(parsed.getUTCSeconds(), 2)}`;
  return `${date} ${time} UTC`;
}

/** The UTC calendar date alone, for labels where the time adds nothing. */
export function formatUtcDate(isoTimestamp: string): string {
  const parsed = parseUtc(isoTimestamp);
  if (parsed === null) {
    return isoTimestamp;
  }
  return `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1, 2)}-${pad(parsed.getUTCDate(), 2)}`;
}

function plural(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? '' : 's'} ago`;
}

/**
 * Render how long ago something happened, relative to an explicit `now`.
 *
 * `now` is a required argument rather than a call to `Date.now()` inside, so
 * the function is pure and a test can state an exact expectation without
 * freezing the clock.
 *
 * A timestamp in the future reads as "just now". Clock skew between the
 * server's stamp and the browser is ordinary; rendering "in 3 seconds" would
 * make a normal condition look like a bug.
 */
export function formatRelativeTime(isoTimestamp: string, now: Date): string {
  const parsed = parseUtc(isoTimestamp);
  if (parsed === null) {
    return isoTimestamp;
  }

  const elapsedSeconds = Math.floor((now.getTime() - parsed.getTime()) / 1000);
  if (elapsedSeconds < SECONDS_PER_MINUTE) {
    return 'just now';
  }
  if (elapsedSeconds < SECONDS_PER_HOUR) {
    return plural(Math.floor(elapsedSeconds / SECONDS_PER_MINUTE), 'minute');
  }
  if (elapsedSeconds < SECONDS_PER_DAY) {
    return plural(Math.floor(elapsedSeconds / SECONDS_PER_HOUR), 'hour');
  }

  const elapsedDays = Math.floor(elapsedSeconds / SECONDS_PER_DAY);
  if (elapsedDays < RELATIVE_CUTOFF_DAYS) {
    return plural(elapsedDays, 'day');
  }
  return formatUtcDate(isoTimestamp);
}
