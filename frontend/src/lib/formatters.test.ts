import { describe, expect, it } from 'vitest';

import {
  MISSING_VALUE_PLACEHOLDER,
  formatRelativeTime,
  formatScore,
  formatUtcDate,
  formatUtcTimestamp,
} from './formatters';

describe('formatScore', () => {
  it('renders two decimals by default', () => {
    expect(formatScore(0.8125)).toBe('0.81');
    expect(formatScore(0.7)).toBe('0.70');
    expect(formatScore(1)).toBe('1.00');
  });

  it('honours an explicit precision', () => {
    expect(formatScore(0.8125, 3)).toBe('0.813');
  });

  it('shows a placeholder rather than NaN', () => {
    expect(formatScore(Number.NaN)).toBe(MISSING_VALUE_PLACEHOLDER);
    expect(formatScore(Number.POSITIVE_INFINITY)).toBe(MISSING_VALUE_PLACEHOLDER);
  });
});

describe('formatUtcTimestamp', () => {
  it('renders the instant in UTC, not in the viewer’s zone', () => {
    // Review decisions are audited against server timestamps, so a local-time
    // rendering would put the screen and the audit trail in disagreement.
    expect(formatUtcTimestamp('2026-09-18T09:05:07Z')).toBe('2026-09-18 09:05:07 UTC');
  });

  it('converts an offset timestamp to UTC rather than displaying it as written', () => {
    expect(formatUtcTimestamp('2026-09-18T12:00:00+03:00')).toBe('2026-09-18 09:00:00 UTC');
  });

  it('pads single-digit components', () => {
    expect(formatUtcTimestamp('2026-01-02T03:04:05Z')).toBe('2026-01-02 03:04:05 UTC');
  });

  it('returns an unparseable value unchanged instead of "Invalid Date"', () => {
    expect(formatUtcTimestamp('not-a-timestamp')).toBe('not-a-timestamp');
  });
});

describe('formatUtcDate', () => {
  it('drops the time', () => {
    expect(formatUtcDate('2026-09-18T09:05:07Z')).toBe('2026-09-18');
  });

  it('returns an unparseable value unchanged', () => {
    expect(formatUtcDate('')).toBe('');
  });
});

describe('formatRelativeTime', () => {
  const now = new Date('2026-09-18T12:00:00Z');

  it.each([
    ['2026-09-18T11:59:30Z', 'just now'],
    ['2026-09-18T11:59:00Z', '1 minute ago'],
    ['2026-09-18T11:30:00Z', '30 minutes ago'],
    ['2026-09-18T11:00:00Z', '1 hour ago'],
    ['2026-09-18T02:00:00Z', '10 hours ago'],
    ['2026-09-17T12:00:00Z', '1 day ago'],
    ['2026-09-11T12:00:00Z', '7 days ago'],
  ])('renders %s as %s', (timestamp, expected) => {
    expect(formatRelativeTime(timestamp, now)).toBe(expected);
  });

  it('falls back to a date once a relative label stops helping', () => {
    expect(formatRelativeTime('2026-06-01T12:00:00Z', now)).toBe('2026-06-01');
  });

  it('reads a future timestamp as "just now" rather than counting forward', () => {
    // Clock skew between the server stamp and the browser is ordinary.
    expect(formatRelativeTime('2026-09-18T12:00:30Z', now)).toBe('just now');
  });

  it('returns an unparseable value unchanged', () => {
    expect(formatRelativeTime('whenever', now)).toBe('whenever');
  });
});
