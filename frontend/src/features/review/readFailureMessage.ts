/**
 * What to tell a reviewer when a workspace read fails.
 *
 * Two functions rather than one, because the two failures mean different
 * things to the person reading them. The case detail is the primary resource:
 * without it there is no pair, no status and no evidence, so its failure
 * takes the whole workspace. An advisory or a history read is secondary, and
 * its failure should cost the reviewer that panel and nothing else -- the
 * deterministic evidence they came to look at is still on screen and still
 * true.
 *
 * Branching is on `ApiFailure.kind` and the public `ErrorCode`, never on
 * message text. `retryable` asks "could reading again plausibly differ",
 * which is why a missing case and corrupt stored state are both false: the
 * first will keep being missing until the queue is re-read, and the second
 * would return the same contradictory rows.
 *
 * Copy is kept separate from the queue's own mapping on purpose. The strings
 * differ in what they tell the reviewer to do next, and collapsing them into
 * one table would mean writing sentences vague enough to fit both places.
 */

import type { ApiFailure, ErrorCode } from '../../api/errors';

export interface ReadFailureMessage {
  title: string;
  description: string;
  /** Whether to offer a manual retry. Nothing retries on its own. */
  retryable: boolean;
}

/**
 * `404 REVIEW_CASE_NOT_FOUND` on the detail endpoint.
 *
 * Reported as what the API actually said, and nothing beyond it. The case may
 * have been removed, the queue may have been rebuilt, or this view may simply
 * be old; the API does not say which, and neither does this. Re-reading the
 * detail cannot help -- the useful action is re-reading the queue, which the
 * workspace offers only when the workspace was given a way to do it.
 */
const CASE_NOT_FOUND: ReadFailureMessage = {
  title: 'This case is not available.',
  description:
    'The review API reports no case with this identifier. The queue view you selected it from may be out of date.',
  retryable: false,
};

const CASE_UNAVAILABLE: ReadFailureMessage = {
  title: 'This case could not be loaded.',
  description:
    'The review API could not read its storage. This usually needs an operator; you can try again once it is back.',
  retryable: true,
};

const CASE_CORRUPT: ReadFailureMessage = {
  title: 'The review API returned inconsistent stored state for this case.',
  description:
    'The stored case contradicts itself. Reading it again will return the same result — this needs operator attention.',
  retryable: false,
};

const CASE_INTERNAL: ReadFailureMessage = {
  title: 'The review API failed unexpectedly.',
  description: 'Something went wrong on the server while loading this case. You can try again.',
  retryable: true,
};

const CASE_CLIENT_DEFECT: ReadFailureMessage = {
  title: 'This case could not be loaded.',
  description:
    'This interface sent a request the review API rejected. That is a defect here rather than something you can correct — please report it.',
  retryable: false,
};

const CASE_UNREACHABLE: ReadFailureMessage = {
  title: 'Cannot reach the local review API.',
  description:
    'No response came back. Check that the review API is running on 127.0.0.1:8000, then try again.',
  retryable: true,
};

const CASE_UNEXPECTED: ReadFailureMessage = {
  title: 'The local review API returned an unexpected response.',
  description:
    'The response did not match the API contract this interface was built against. You can try again.',
  retryable: true,
};

/** True when the detail endpoint says this case does not exist. */
export function isCaseNotFound(failure: ApiFailure): boolean {
  return failure.kind === 'api' && failure.code === 'REVIEW_CASE_NOT_FOUND';
}

/** The whole-workspace failure, for the primary resource. */
export function caseFailureMessage(failure: ApiFailure): ReadFailureMessage {
  switch (failure.kind) {
    case 'network':
      return CASE_UNREACHABLE;
    case 'malformed':
    case 'aborted':
      // An abort never reaches a panel -- cancelled reads are discarded by the
      // hook -- but the mapping stays total so no failure can render blank.
      return CASE_UNEXPECTED;
    case 'api':
      return caseApiMessage(failure.code);
  }
}

function caseApiMessage(code: ErrorCode): ReadFailureMessage {
  switch (code) {
    case 'REVIEW_CASE_NOT_FOUND':
      return CASE_NOT_FOUND;
    case 'REVIEW_STORAGE_UNAVAILABLE':
      return CASE_UNAVAILABLE;
    case 'REVIEW_STORAGE_CORRUPT':
      return CASE_CORRUPT;
    case 'INVALID_REQUEST':
    case 'NOT_FOUND':
    case 'METHOD_NOT_ALLOWED':
      return CASE_CLIENT_DEFECT;
    default:
      // The remaining codes belong to the resolution path and cannot come
      // from a read. Reporting one generically beats inventing copy for a
      // condition that would mean the API answered a question nobody asked.
      return CASE_INTERNAL;
  }
}

/**
 * The panel-local failure, for a secondary resource.
 *
 * `panelName` is this application's own word for the panel ("advisory",
 * "history"), never anything derived from the response.
 */
export function panelFailureMessage(failure: ApiFailure, panelName: string): ReadFailureMessage {
  const title = `The ${panelName} could not be loaded.`;

  if (failure.kind === 'network') {
    return {
      title,
      description: 'The local review API did not respond. The rest of this case is unaffected.',
      retryable: true,
    };
  }
  if (failure.kind === 'api' && failure.code === 'REVIEW_STORAGE_CORRUPT') {
    return {
      title,
      description:
        'The review API returned inconsistent stored state. Reading it again will return the same result — this needs operator attention.',
      retryable: false,
    };
  }
  if (failure.kind === 'api' && failure.code === 'REVIEW_CASE_NOT_FOUND') {
    return {
      title,
      description: 'The review API reports no case with this identifier.',
      retryable: false,
    };
  }
  return {
    title,
    description: 'The rest of this case is unaffected. You can try loading it again.',
    retryable: true,
  };
}
