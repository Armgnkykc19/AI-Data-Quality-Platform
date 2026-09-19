/**
 * Classification of published review state. No inference, ever.
 *
 * The boundary this file sits on is worth stating, because it is easy to
 * cross by accident. A helper here may *restate* a fact the public contract
 * already publishes -- which statuses are terminal is one, because
 * `ReviewWorkflow.resolve_case` refuses anything that is not `PENDING` and
 * says so in the contract as `REVIEW_CASE_NOT_PENDING`.
 *
 * A helper here may **not** derive a decision. There is no `shouldMatch`, no
 * `isSafeToMerge`, no `recommendedDecision`, and no comparison of
 * `machine_score` against `auto_match_threshold`. Sprint 08 authorization
 * projects a connected component across every AUTO_MATCH edge and every
 * recorded human decision, so a verdict that looks derivable from one case is
 * not derivable at all. The backend is the only authority, and the UI must not
 * become a second one.
 */

import type { ReviewStatus } from '../api/types';

/**
 * The three statuses a case can end in.
 *
 * All three are final. `DEFERRED` in particular is **not** reopenable under
 * the Sprint 08/10/11 workflow: a deferred pair stays excluded from canonical
 * merging, and no endpoint can move it back to `PENDING`. Modelling DEFER as
 * a temporary state would misrepresent what a reviewer is committing to.
 */
export const TERMINAL_REVIEW_STATUSES = ['MATCH', 'NO_MATCH', 'DEFERRED'] as const;

export type TerminalReviewStatus = (typeof TERMINAL_REVIEW_STATUSES)[number];

const TERMINAL_SET: ReadonlySet<string> = new Set<string>(TERMINAL_REVIEW_STATUSES);

/**
 * True when the case can no longer be resolved.
 *
 * `PENDING` is the only status that is not terminal. A UI uses this to disable
 * decision controls as a courtesy -- to avoid a request the backend would
 * answer with `409 REVIEW_CASE_NOT_PENDING` -- never as the rule itself.
 */
export function isTerminalReviewStatus(status: ReviewStatus): status is TerminalReviewStatus {
  return TERMINAL_SET.has(status);
}

/**
 * How each status is spelled for a reader.
 *
 * The wire tokens are shouted constants; a queue of them is hard to scan. The
 * mapping is exhaustive over `ReviewStatus` by type, so adding a status to the
 * contract fails the build here rather than rendering a raw token.
 *
 * Only the spelling changes. `MATCH` does not become "Matched" and `DEFERRED`
 * does not become "Paused": a label that softened what a terminal decision
 * means would misdescribe what the reviewer is looking at.
 */
export const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  PENDING: 'Pending',
  MATCH: 'Match',
  NO_MATCH: 'No match',
  DEFERRED: 'Deferred',
};

export function reviewStatusLabel(status: ReviewStatus): string {
  return REVIEW_STATUS_LABELS[status];
}
