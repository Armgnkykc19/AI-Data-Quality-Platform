/**
 * What to tell a reviewer when the queue fails to load.
 *
 * Every branch here is on `ApiFailure.kind` and, for an API failure, on the
 * public `ErrorCode`. No message text is ever inspected: the backend owns its
 * prose, is free to reword it, and deliberately keeps every exception message
 * out of the response in the first place.
 *
 * `retryable` is the load-bearing field. It is not "was this bad" but "could
 * asking again plausibly help", and the three cases where it is false are
 * false for different reasons worth keeping straight:
 *
 * - Corrupt stored state is not a transient condition. Stored rows contradict
 *   themselves, and a second identical request reads the same rows. Offering
 *   a retry would invite a reviewer to keep pressing a button that cannot
 *   change the answer, and would frame an operator-level problem as a blip.
 * - An invalid request is a defect in this interface. The same request will
 *   be rejected the same way every time.
 * - A 404 or 405 on the list endpoint means the client is addressing a URL the
 *   API does not publish, which is the same kind of defect.
 *
 * None of these messages names a file, a table, a path, a version or an
 * exception. What reaches the reviewer is written here and nowhere else.
 */

import type { ApiFailure } from '../../api/errors';

export interface QueueFailureMessage {
  title: string;
  description: string;
  /** Whether to offer a manual retry. The client never retries on its own. */
  retryable: boolean;
}

const UNAVAILABLE: QueueFailureMessage = {
  title: 'The review queue is unavailable.',
  description:
    'The review API could not read its storage. This usually needs an operator; you can try again once it is back.',
  retryable: true,
};

const CORRUPT: QueueFailureMessage = {
  title: 'The review queue returned inconsistent stored state.',
  description:
    'The API read stored review state that contradicts itself. Trying again will return the same result — this needs operator attention.',
  retryable: false,
};

const INTERNAL: QueueFailureMessage = {
  title: 'The review API failed unexpectedly.',
  description: 'Something went wrong on the server while loading the queue. You can try again.',
  retryable: true,
};

const CLIENT_DEFECT: QueueFailureMessage = {
  title: 'The queue could not be loaded.',
  description:
    'This interface sent a request the review API rejected. That is a defect here rather than something you can correct — please report it.',
  retryable: false,
};

const UNREACHABLE: QueueFailureMessage = {
  title: 'Cannot reach the local review API.',
  description:
    'No response came back. Check that the review API is running on 127.0.0.1:8000, then try again.',
  retryable: true,
};

const UNEXPECTED_RESPONSE: QueueFailureMessage = {
  title: 'The local review API returned an unexpected response.',
  description:
    'The response did not match the API contract this interface was built against. You can try again.',
  retryable: true,
};

export function queueFailureMessage(failure: ApiFailure): QueueFailureMessage {
  switch (failure.kind) {
    case 'network':
      return UNREACHABLE;
    case 'malformed':
      return UNEXPECTED_RESPONSE;
    case 'aborted':
      // Not reachable through the queue hook, which discards cancelled
      // requests rather than reporting them. Mapped anyway so this function is
      // total over ApiFailure, and mapped to the neutral message because an
      // abort describes nothing a reviewer needs to act on.
      return UNEXPECTED_RESPONSE;
    case 'api':
      return apiFailureMessage(failure.code);
  }
}

function apiFailureMessage(code: Extract<ApiFailure, { kind: 'api' }>['code']): QueueFailureMessage {
  switch (code) {
    case 'REVIEW_STORAGE_UNAVAILABLE':
      return UNAVAILABLE;
    case 'REVIEW_STORAGE_CORRUPT':
      return CORRUPT;
    case 'INVALID_REQUEST':
    case 'NOT_FOUND':
    case 'METHOD_NOT_ALLOWED':
      return CLIENT_DEFECT;
    case 'INTERNAL_ERROR':
      return INTERNAL;
    default:
      // The remaining codes belong to the case-detail and resolution paths and
      // cannot be produced by a list request. Reporting one generically is
      // more honest than inventing queue-specific copy for a condition that
      // would mean the API is answering something other than what was asked.
      return INTERNAL;
  }
}
