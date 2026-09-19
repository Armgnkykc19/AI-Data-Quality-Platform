/**
 * What to tell a reviewer when a resolution does not succeed, and what to do next.
 *
 * Kept apart from `readFailureMessage.ts` on purpose. A failed read costs the
 * reviewer a view; a failed write may or may not have changed the queue, and
 * the sentences that follow from that have nothing in common with "we could
 * not show you this". Collapsing the two tables would mean writing copy vague
 * enough to fit both, which is precisely the copy nobody can act on.
 *
 * Every failure lands in exactly one of five classes, and the class is the
 * safety decision:
 *
 * `refusal` — the API evaluated the request and said no. Sprint 11 proves a
 * refusal writes nothing: `ReviewQueueService.resolve_case` calls the domain
 * before it opens a transaction, and `test_a_refused_match_writes_nothing`
 * pins it. The reviewer's decision did not happen, and they know exactly what
 * they asked for.
 *
 * `staleState` — the stored review state is not what this decision was made
 * against. Nothing was recorded for *this* request, but something else has
 * moved, so the authoritative reads have to run again before the reviewer is
 * offered another decision.
 *
 * `operational` — the server could not judge the request at all: it has no
 * authorization context, no config, or no prepared queue. Nothing was
 * recorded, nothing about this case changed, and nothing the reviewer can
 * type will help.
 *
 * `uncertain` — the client cannot know whether the server committed. A lost
 * response, a storage failure mid-write, an unexpected 500. This is the class
 * that must never be answered with a retry button: the first request may
 * already have resolved the case, and a second POST would be a second
 * decision made blind. The only safe next step is an authoritative GET.
 *
 * `interface` — this client sent something the API rejected structurally.
 * That is a defect here, not reviewer input: the UI builds the request from
 * the case it was shown and has no field a reviewer can malform.
 *
 * `reconcile` says whether authoritative reads must be re-run before another
 * decision is offered. It is true for everything uncertain or stale, and
 * false where Sprint 11's own guarantees make a re-read pointless.
 *
 * Branching is on `ApiFailure.kind` and the public `ErrorCode`. No message
 * text is inspected, and no copy here repeats anything the server said.
 */

import type { ApiFailure, ErrorCode } from '../../api/errors';

export type ResolutionFailureClass =
  | 'refusal'
  | 'staleState'
  | 'operational'
  | 'uncertain'
  | 'interface';

export interface ResolutionFailureMessage {
  kind: ResolutionFailureClass;
  title: string;
  description: string;
  /** Re-read detail, history and queue before offering another decision. */
  reconcile: boolean;
}

/** The sentence every uncertain outcome shares, so none of them sounds decided. */
const UNCERTAIN_TITLE = 'This decision could not be confirmed.';

const VERSION_CONFLICT: ResolutionFailureMessage = {
  kind: 'staleState',
  title: 'The case changed before this decision was recorded.',
  description:
    'This decision was made against a version of the case that is no longer current, so the review API recorded nothing. The case is being re-read — review its current state before deciding again.',
  reconcile: true,
};

const NOT_PENDING: ResolutionFailureMessage = {
  kind: 'staleState',
  title: 'This case is no longer pending.',
  description:
    'The review API does not consider this case open for a decision, so nothing was recorded for this request. Its current state is being re-read.',
  reconcile: true,
};

const CONTRADICTION: ResolutionFailureMessage = {
  kind: 'refusal',
  title: 'The review API rejected this decision.',
  description:
    'It conflicts with review state already recorded for these records. No resolution was recorded for this case, and this interface cannot override that check.',
  reconcile: true,
};

const MATCH_REFUSED: ResolutionFailureMessage = {
  kind: 'refusal',
  title: 'The review API did not authorize this match.',
  description:
    'Its match safety rules refused the merge, so no match resolution was recorded. This interface cannot evaluate or override that check.',
  reconcile: false,
};

const CANNOT_AUTHORIZE: ResolutionFailureMessage = {
  kind: 'operational',
  title: 'The review API cannot authorize decisions right now.',
  description:
    'It could not load what it needs to judge a match, so no decision was recorded and nothing about this case changed. This usually needs an operator.',
  reconcile: false,
};

const QUEUE_NOT_READY: ResolutionFailureMessage = {
  kind: 'operational',
  title: 'The review queue is not ready to accept decisions.',
  description:
    'The queue on the server has not been prepared for review. Preparing it is an operator task; nothing was recorded and nothing here can start it.',
  reconcile: false,
};

const CASE_GONE: ResolutionFailureMessage = {
  kind: 'staleState',
  title: 'This case is no longer available.',
  description:
    'The review API reports no case with this identifier, so nothing was recorded. The queue view it was selected from may be out of date.',
  reconcile: true,
};

const STORAGE_UNAVAILABLE: ResolutionFailureMessage = {
  kind: 'uncertain',
  title: UNCERTAIN_TITLE,
  description:
    'The review API could not reach its storage, so whether this decision was recorded is unknown. The case is being re-read — do not decide again until its current state is shown.',
  reconcile: true,
};

const STORAGE_CORRUPT: ResolutionFailureMessage = {
  kind: 'uncertain',
  title: UNCERTAIN_TITLE,
  description:
    'The review API reported inconsistent stored state for this queue. Whether this decision was recorded is unknown, and repeating it would not make that clearer — this needs operator attention.',
  reconcile: true,
};

const SERVER_FAILED: ResolutionFailureMessage = {
  kind: 'uncertain',
  title: UNCERTAIN_TITLE,
  description:
    'The review API failed unexpectedly while handling this decision, so whether it was recorded is unknown. The case is being re-read from the API.',
  reconcile: true,
};

const UNREACHABLE: ResolutionFailureMessage = {
  kind: 'uncertain',
  title: UNCERTAIN_TITLE,
  description:
    'No response came back from the local review API. A request that gets no answer may still have reached the server, so whether this decision was recorded is unknown. The case is being re-read.',
  reconcile: true,
};

const UNREADABLE_RESPONSE: ResolutionFailureMessage = {
  kind: 'uncertain',
  title: UNCERTAIN_TITLE,
  description:
    'The response did not match the API contract this interface was built against, so whether this decision was recorded is unknown. The case is being re-read.',
  reconcile: true,
};

const CANCELLED: ResolutionFailureMessage = {
  kind: 'uncertain',
  title: UNCERTAIN_TITLE,
  description:
    'The request was cancelled before a result came back. A cancelled request may already have reached the review API, so whether this decision was recorded is unknown. The case is being re-read.',
  reconcile: true,
};

const CLIENT_DEFECT: ResolutionFailureMessage = {
  kind: 'interface',
  title: 'This interface sent a request the review API rejected.',
  description:
    'No decision was recorded. That is a defect here rather than something you can correct — please report it.',
  reconcile: false,
};

/** The whole mapping, total over `ApiFailure`. */
export function resolutionFailureMessage(failure: ApiFailure): ResolutionFailureMessage {
  switch (failure.kind) {
    case 'network':
      return UNREACHABLE;
    case 'malformed':
      return UNREADABLE_RESPONSE;
    case 'aborted':
      // Nothing in this app aborts a resolution, and the reason it does not
      // is exactly what this entry says: a cancelled write is an unknown
      // write, never a write that did not happen.
      return CANCELLED;
    case 'api':
      return resolutionApiMessage(failure.code);
  }
}

/**
 * Exhaustive over the published vocabulary, with no `default`.
 *
 * A code added to `review_api.errors.ErrorCode` and mirrored into
 * `ERROR_CODES` fails the build here rather than falling into whichever
 * branch happened to sit at the bottom -- which is how a refusal ends up
 * reported as an uncertain write, or worse, the other way round.
 */
function resolutionApiMessage(code: ErrorCode): ResolutionFailureMessage {
  switch (code) {
    case 'REVIEW_CASE_VERSION_CONFLICT':
      return VERSION_CONFLICT;
    case 'REVIEW_CASE_NOT_PENDING':
      return NOT_PENDING;
    case 'HUMAN_REVIEW_CONTRADICTION':
      return CONTRADICTION;
    case 'MATCH_NOT_AUTHORIZED':
      return MATCH_REFUSED;
    case 'AUTHORIZATION_CONTEXT_UNAVAILABLE':
    case 'AUTHORIZATION_CONFIG_UNAVAILABLE':
      return CANNOT_AUTHORIZE;
    case 'REVIEW_QUEUE_NOT_READY':
      return QUEUE_NOT_READY;
    case 'REVIEW_CASE_NOT_FOUND':
      return CASE_GONE;
    case 'REVIEW_STORAGE_UNAVAILABLE':
      return STORAGE_UNAVAILABLE;
    case 'REVIEW_STORAGE_CORRUPT':
      return STORAGE_CORRUPT;
    case 'INTERNAL_ERROR':
      return SERVER_FAILED;
    case 'INVALID_REQUEST':
    case 'NOT_FOUND':
    case 'METHOD_NOT_ALLOWED':
      return CLIENT_DEFECT;
  }
}
