import { describe, expect, it } from 'vitest';

import { REVIEW_STATUSES } from '../api/types';
import { TERMINAL_REVIEW_STATUSES, isTerminalReviewStatus } from './status';

describe('isTerminalReviewStatus', () => {
  it('treats PENDING as the only resolvable status', () => {
    expect(isTerminalReviewStatus('PENDING')).toBe(false);
  });

  it.each(['MATCH', 'NO_MATCH', 'DEFERRED'] as const)('treats %s as terminal', (status) => {
    expect(isTerminalReviewStatus(status)).toBe(true);
  });

  it('classifies DEFERRED as final rather than as a paused state', () => {
    // No endpoint returns a deferred case to PENDING. A deferred pair stays
    // excluded from canonical merging, so modelling DEFER as temporary would
    // misrepresent what the reviewer committed to.
    expect(isTerminalReviewStatus('DEFERRED')).toBe(true);
  });

  it('covers every published status, so a new one cannot be silently missed', () => {
    const terminal = REVIEW_STATUSES.filter(isTerminalReviewStatus);
    const nonTerminal = REVIEW_STATUSES.filter((status) => !isTerminalReviewStatus(status));

    expect([...terminal].sort()).toEqual([...TERMINAL_REVIEW_STATUSES].sort());
    expect(nonTerminal).toEqual(['PENDING']);
  });
});
