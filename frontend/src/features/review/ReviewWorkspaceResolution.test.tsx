import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReviewWorkspace } from './ReviewWorkspace';
import type { ReviewCaseDetail, ReviewCaseSummary } from '../../api/types';
import {
  DEFERRED_CASE_ID,
  PENDING_CASE_ID,
  advisorySuggestion,
  caseBSuggestion,
  caseCreatedEvent,
  deferredCaseDetail,
  eventHistory,
  matchResolutionEvent,
  matchResolveResponse,
  matchedCaseDetail,
  pendingCaseDetail,
} from '../../test/fixtures/sprint11';
import { createFetchStub, errorResponse, jsonResponse, type FetchStub } from '../../test/http';

/**
 * The write half of the workspace, end to end against a stubbed API.
 *
 * These tests assert observable behaviour only: how many resolve requests
 * were sent, what body they carried, what the screen says afterwards, and --
 * most of all -- the situations in which a second request must not happen.
 * Nothing here reaches into component state or hook internals; the safety
 * properties this phase is about are all visible from outside.
 */

const detailUrl = (id: string) => `/api/v1/review-cases/${id}`;
const eventsUrl = (id: string) => `${detailUrl(id)}/events`;
const suggestionsUrl = (id: string) => `${detailUrl(id)}/semantic-suggestions`;
const resolveUrl = (id: string) => `${detailUrl(id)}/resolve`;

let http: FetchStub;
let routes: Map<string, () => Promise<Response>>;
const refreshQueue = vi.fn();
let view: ReturnType<typeof render> | null = null;

beforeEach(() => {
  http = createFetchStub();
  routes = new Map();
  refreshQueue.mockClear();
  view = null;
  http.routeBy((url) => {
    const handler = routes.get(url);
    if (handler === undefined) {
      throw new Error(`No stub registered for ${url}`);
    }
    return handler();
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function serve(url: string, body: unknown): void {
  routes.set(url, () => Promise.resolve(jsonResponse(body)));
}

function serveResponse(url: string, response: Response): void {
  routes.set(url, () => Promise.resolve(response));
}

function serveNetworkFailure(url: string): void {
  routes.set(url, () => Promise.reject(new TypeError('Failed to fetch')));
}

/**
 * Answer the resolve endpoint, optionally moving the stubbed server on first.
 *
 * `transition` runs before the response is handed back, which is the honest
 * ordering: by the time a client sees a resolution response, the reads have
 * already started answering differently.
 */
function serveResolve(response: Response, transition?: () => void): void {
  routes.set(resolveUrl(PENDING_CASE_ID), () => {
    transition?.();
    return Promise.resolve(response);
  });
}

function serveResolveFailure(reason: unknown, transition?: () => void): void {
  routes.set(resolveUrl(PENDING_CASE_ID), () => {
    transition?.();
    return Promise.reject(reason);
  });
}

function workspace(reviewCaseId: string | null, summary: ReviewCaseSummary | null = null) {
  return (
    <ReviewWorkspace
      reviewCaseId={reviewCaseId}
      selectedQueueSummary={summary}
      onRefreshQueue={refreshQueue}
    />
  );
}

function select(reviewCaseId: string | null) {
  if (view === null) {
    throw new Error('Render the workspace before changing the selection.');
  }
  view.rerender(workspace(reviewCaseId));
}

/** Render a loaded pending case, ready to decide. */
async function renderPending(detail: ReviewCaseDetail = pendingCaseDetail) {
  serve(detailUrl(PENDING_CASE_ID), detail);
  serve(eventsUrl(PENDING_CASE_ID), [caseCreatedEvent]);
  serve(suggestionsUrl(PENDING_CASE_ID), [advisorySuggestion]);
  const user = userEvent.setup();
  view = render(workspace(PENDING_CASE_ID));
  await waitFor(() => {
    expect(screen.getByRole('group', { name: 'Human decision' })).toBeInTheDocument();
  });
  return { user };
}

function decisions() {
  return within(screen.getByRole('group', { name: 'Human decision' }));
}

function decisionButton(name: 'Match' | 'No match' | 'Defer'): HTMLElement {
  return decisions().getByRole('button', { name });
}

function resolveCalls() {
  return http.mock.mock.calls.filter((call) => String(call[0]).endsWith('/resolve'));
}

function resolveBody(index = 0): unknown {
  const call = resolveCalls()[index];
  if (call === undefined) {
    throw new Error('No resolve request was sent.');
  }
  return JSON.parse(String(call[1]?.body));
}

function requestsTo(url: string): number {
  return http.urls().filter((candidate) => candidate === url).length;
}

/**
 * Leave the resolution in flight, and hand back its release.
 *
 * A resolve stub that answers immediately cannot test the double-submit
 * guard: user-event flushes microtasks between the two clicks, so the second
 * one lands after the confirmation has already been replaced and would find
 * nothing to press even with the guard removed. Holding the response is what
 * puts both activations inside the one window the guard exists to cover.
 */
function holdResolve(): () => void {
  let release: () => void = () => undefined;
  routes.set(
    resolveUrl(PENDING_CASE_ID),
    () =>
      new Promise<Response>((resolvePromise) => {
        release = () => {
          resolvePromise(jsonResponse(matchResolveResponse));
        };
      }),
  );
  return () => {
    release();
  };
}

/** Leave the next detail read unanswered, and hand back its release. */
function holdDetail(): () => void {
  let release: () => void = () => undefined;
  routes.set(
    detailUrl(PENDING_CASE_ID),
    () =>
      new Promise<Response>((resolvePromise) => {
        release = () => {
          resolvePromise(jsonResponse(pendingCaseDetail));
        };
      }),
  );
  return () => {
    release();
  };
}

/** Choose a decision and confirm it, which is the only path to a request. */
async function decide(
  user: ReturnType<typeof userEvent.setup>,
  name: 'Match' | 'No match' | 'Defer',
) {
  await user.click(decisionButton(name));
  await user.click(screen.getByRole('button', { name: 'Confirm' }));
}

// ---------------------------------------------------------------------------
// Availability
// ---------------------------------------------------------------------------

describe('when the decision controls exist', () => {
  it('offers exactly the three published decisions, in the published order', async () => {
    await renderPending();

    expect(decisions().getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Match',
      'No match',
      'Defer',
    ]);
  });

  it.each([
    ['MATCH', matchedCaseDetail],
    ['NO_MATCH', { ...matchedCaseDetail, status: 'NO_MATCH' } as ReviewCaseDetail],
    ['DEFERRED', deferredCaseDetail],
  ] as const)('offers nothing to decide on a case that is %s', async (_status, detail) => {
    serve(detailUrl(PENDING_CASE_ID), { ...detail, review_case_id: PENDING_CASE_ID });
    serve(eventsUrl(PENDING_CASE_ID), eventHistory);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    view = render(workspace(PENDING_CASE_ID));

    await waitFor(() => {
      expect(screen.getByRole('region', { name: 'Recorded decision' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('group', { name: 'Human decision' })).toBeNull();
    // No reopen, no undo, no edit, no second decision.
    for (const name of [/^match$/i, /^no match$/i, /^defer$/i, /reopen|undo|edit/i]) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
  });

  it('offers nothing while the authoritative detail is still loading', async () => {
    routes.set(detailUrl(PENDING_CASE_ID), () => new Promise<Response>(() => undefined));
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    view = render(workspace(PENDING_CASE_ID));

    await waitFor(() => {
      expect(screen.getByText('Loading case…')).toBeInTheDocument();
    });
    expect(screen.queryByRole('group', { name: 'Human decision' })).toBeNull();
  });

  it('offers nothing when the authoritative detail could not be read', async () => {
    serveResponse(detailUrl(PENDING_CASE_ID), errorResponse('REVIEW_STORAGE_UNAVAILABLE', 503));
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    view = render(workspace(PENDING_CASE_ID));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.queryByRole('group', { name: 'Human decision' })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Neutrality
// ---------------------------------------------------------------------------

describe('neutrality of the three decisions', () => {
  it('preselects nothing and recommends nothing when an advisory suggests a match', async () => {
    serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), [caseBSuggestion]);
    view = render(workspace(PENDING_CASE_ID));

    await waitFor(() => {
      expect(screen.getByRole('group', { name: 'Human decision' })).toBeInTheDocument();
    });
    expect(within(screen.getByRole('region', { name: 'AI advisory' })).getByText('Suggest match'))
      .toBeInTheDocument();

    const buttons = decisions().getAllByRole('button');
    for (const button of buttons) {
      expect(button).toBeEnabled();
      expect(button).not.toHaveAttribute('aria-pressed');
      expect(button).not.toHaveAttribute('aria-current');
      expect(button.getAttribute('class')).toBe(buttons[0]?.getAttribute('class'));
    }
    expect(screen.queryByText(/recommend/i)).toBeNull();
  });

  it.each([
    ['below the review threshold', 0.2],
    ['between the thresholds', 0.8125],
    ['above the auto-match threshold', 0.97],
  ])('behaves identically with a score %s', async (_label, machine_score) => {
    await renderPending({ ...pendingCaseDetail, machine_score });

    const buttons = decisions().getAllByRole('button');
    expect(buttons.map((button) => button.textContent)).toEqual(['Match', 'No match', 'Defer']);
    for (const button of buttons) {
      expect(button).toBeEnabled();
      expect(button.getAttribute('class')).toBe(buttons[0]?.getAttribute('class'));
    }
  });
});

// ---------------------------------------------------------------------------
// Reviewer label
// ---------------------------------------------------------------------------

describe('the reviewer label', () => {
  it('says it is unverified and claims no account', async () => {
    await renderPending();

    const field = screen.getByLabelText('Reviewer label (optional)');
    expect(field).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByText(/unverified label, not an authenticated identity/i)).toBeInTheDocument();
    for (const identity of [/signed in/i, /account/i, /logged in/i, /verified reviewer/i]) {
      expect(screen.queryByText(identity)).toBeNull();
    }
  });

  it('sends null when nothing was entered', async () => {
    const { user } = await renderPending();
    serveResolve(jsonResponse(matchResolveResponse));

    await decide(user, 'Defer');

    await waitFor(() => {
      expect(resolveCalls()).toHaveLength(1);
    });
    expect(resolveBody()).toEqual({
      decision: 'DEFER',
      expected_version: 1,
      reviewer_id: null,
    });
  });

  it('sends what was typed, unchanged, and stores it nowhere', async () => {
    const { user } = await renderPending();
    serveResolve(jsonResponse(matchResolveResponse));

    await user.type(screen.getByLabelText('Reviewer label (optional)'), 'Desk 3 ');
    await decide(user, 'No match');

    await waitFor(() => {
      expect(resolveCalls()).toHaveLength(1);
    });
    expect(resolveBody()).toEqual({
      decision: 'NO_MATCH',
      expected_version: 1,
      reviewer_id: 'Desk 3 ',
    });
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Confirmation
// ---------------------------------------------------------------------------

describe('the confirmation step', () => {
  it('sends nothing when a decision is merely chosen', async () => {
    const { user } = await renderPending();

    await user.click(decisionButton('Match'));

    expect(resolveCalls()).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument();
  });

  it('states the decision, the case, the pair and the label', async () => {
    const { user } = await renderPending();

    await user.type(screen.getByLabelText('Reviewer label (optional)'), 'desk-3');
    await user.click(decisionButton('Match'));

    const panel = screen.getByRole('region', { name: 'Confirm this decision' });
    expect(within(panel).getByText('Match')).toBeInTheDocument();
    expect(within(panel).getByText(PENDING_CASE_ID)).toBeInTheDocument();
    expect(within(panel).getByText('REC-A-0001')).toBeInTheDocument();
    expect(within(panel).getByText('REC-B-0001')).toBeInTheDocument();
    expect(within(panel).getByText('desk-3')).toBeInTheDocument();
    expect(within(panel).getByText(/no undo, no reopen/i)).toBeInTheDocument();
  });

  it('says only that the API checks match authorization', async () => {
    const { user } = await renderPending();

    await user.click(decisionButton('Match'));

    const panel = screen.getByRole('region', { name: 'Confirm this decision' });
    expect(
      within(panel).getByText('Match authorization is checked by the review API.'),
    ).toBeInTheDocument();
    // Nothing is claimed about the outcome before the API has been asked.
    for (const claim of [/is safe/i, /authorization passed/i, /can be merged/i]) {
      expect(within(panel).queryByText(claim)).toBeNull();
    }
  });

  it('claims nothing about authorization or contradictions for a no match', async () => {
    const { user } = await renderPending();

    await user.click(decisionButton('No match'));

    const panel = screen.getByRole('region', { name: 'Confirm this decision' });
    expect(within(panel).queryByText(/authorization/i)).toBeNull();
    expect(within(panel).queryByText(/contradict/i)).toBeNull();
  });

  it('explains that a defer records the Deferred status', async () => {
    const { user } = await renderPending();

    await user.click(decisionButton('Defer'));

    const panel = screen.getByRole('region', { name: 'Confirm this decision' });
    expect(within(panel).getByText(/records the case with the Deferred status/i)).toBeInTheDocument();
    expect(within(panel).getByText(/not a temporary pause/i)).toBeInTheDocument();
  });

  it('moves focus into the confirmation when it opens', async () => {
    const { user } = await renderPending();

    await user.click(decisionButton('Match'));

    // The region itself, not the Confirm button: landing on Confirm would put
    // an irreversible action one keypress away from the click that opened it.
    expect(screen.getByRole('region', { name: 'Confirm this decision' })).toHaveFocus();
  });

  it('announces the submission and blocks a competing one while it is in flight', async () => {
    const { user } = await renderPending();
    // A resolution that never answers, so the in-flight state can be observed.
    routes.set(resolveUrl(PENDING_CASE_ID), () => new Promise<Response>(() => undefined));

    await user.click(decisionButton('Match'));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => {
      expect(screen.getByText('Recording decision…')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    for (const button of decisions().getAllByRole('button')) {
      expect(button).toBeDisabled();
    }
    expect(resolveCalls()).toHaveLength(1);
  });

  it('sends nothing when the confirmation is cancelled, and returns focus', async () => {
    const { user } = await renderPending();

    await user.click(decisionButton('Defer'));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(resolveCalls()).toHaveLength(0);
    expect(screen.queryByRole('region', { name: 'Confirm this decision' })).toBeNull();
    expect(decisionButton('Defer')).toHaveFocus();
    expect(decisionButton('Defer')).toBeEnabled();
  });

  it('cannot be confirmed twice into two decisions while the first is in flight', async () => {
    const { user } = await renderPending();
    // Held open, so both activations happen inside the window where a second
    // request would actually be possible.
    const release = holdResolve();

    await user.click(decisionButton('Match'));
    const confirm = screen.getByRole('button', { name: 'Confirm' });
    await user.dblClick(confirm);

    expect(resolveCalls()).toHaveLength(1);
    expect(confirm).toBeDisabled();

    // A third attempt while still in flight changes nothing either.
    await user.click(confirm);
    expect(resolveCalls()).toHaveLength(1);

    release();
    await waitFor(() => {
      expect(screen.getByText('Decision recorded.')).toBeInTheDocument();
    });
    expect(resolveCalls()).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Keyboard and focus
// ---------------------------------------------------------------------------

describe('keyboard operation of the confirmation', () => {
  it.each(['{Enter}', ' '])('cancels with %s and writes nothing', async (key) => {
    // A keyboard activation of Cancel must be exactly as inert as a click.
    // Cancel restores focus to the decision control synchronously, so this is
    // also the shape in which a stray second activation would land on a live
    // button -- which is why the request count is asserted, not just the copy.
    const { user } = await renderPending();

    await user.click(decisionButton('Match'));
    Array.from(document.querySelectorAll('button'))
      .find((button) => button.textContent === 'Cancel')
      ?.focus();
    await user.keyboard(key);

    expect(resolveCalls()).toHaveLength(0);
    expect(screen.queryByRole('region', { name: 'Confirm this decision' })).toBeNull();
    expect(decisionButton('Match')).toHaveFocus();
  });

  it('opens the confirmation from the keyboard without writing', async () => {
    const { user } = await renderPending();

    decisionButton('Defer').focus();
    await user.keyboard('{Enter}');

    expect(screen.getByRole('region', { name: 'Confirm this decision' })).toBeInTheDocument();
    expect(resolveCalls()).toHaveLength(0);
  });
});

describe('focus after a result', () => {
  it('moves focus to the outcome when the decision controls disappear', async () => {
    const { user } = await renderPending();
    serveResolve(jsonResponse(matchResolveResponse), () => {
      serve(detailUrl(PENDING_CASE_ID), { ...matchedCaseDetail, review_case_id: PENDING_CASE_ID });
      serve(eventsUrl(PENDING_CASE_ID), [caseCreatedEvent, matchResolutionEvent]);
    });

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByText('Decision recorded.')).toBeInTheDocument();
    });
    // Confirm is gone, and on a terminal result so is the whole decision area.
    // Without an explicit target, focus would fall to <body>.
    expect(screen.getByText('Decision recorded.').closest('[tabindex="-1"]')).toHaveFocus();
    expect(document.body).not.toHaveFocus();
  });

  it('moves focus to the outcome after a refusal too', async () => {
    const { user } = await renderPending();
    serveResolve(errorResponse('MATCH_NOT_AUTHORIZED', 422));

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByText('The review API did not authorize this match.')).toBeInTheDocument();
    });
    expect(
      screen.getByText('The review API did not authorize this match.').closest('[tabindex="-1"]'),
    ).toHaveFocus();
  });

  it('leaves the refresh narration to the one region that owns it', async () => {
    const { user } = await renderPending();
    const release = holdDetail();
    serveResolve(jsonResponse(matchResolveResponse));

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByText(/Refreshing authoritative case state/i)).toBeInTheDocument();
    });
    // The workspace's own status region already says "Updating case…" for this
    // very refresh; the follow-up line is therefore outside the live region so
    // the two do not announce over each other.
    const followUp = screen.getByText(/Refreshing authoritative case state/i);
    expect(followUp.closest('[role="status"]')).toBeNull();
    expect(followUp.closest('[role="alert"]')).toBeNull();
    // The outcome itself is still announced.
    expect(screen.getByText('Decision recorded.').closest('[role="status"]')).not.toBeNull();

    release();
  });
});

// ---------------------------------------------------------------------------
// Success
// ---------------------------------------------------------------------------

describe('a recorded decision', () => {
  it('converges on authoritative state without re-reading the advisory', async () => {
    const { user } = await renderPending();
    const suggestionReadsBefore = requestsTo(suggestionsUrl(PENDING_CASE_ID));

    serveResolve(jsonResponse(matchResolveResponse), () => {
      // The server has transitioned: both reads now answer differently.
      serve(detailUrl(PENDING_CASE_ID), { ...matchedCaseDetail, review_case_id: PENDING_CASE_ID });
      serve(eventsUrl(PENDING_CASE_ID), [caseCreatedEvent, matchResolutionEvent]);
    });

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByRole('region', { name: 'Recorded decision' })).toBeInTheDocument();
    });

    // Exactly one write, with the version that was on screen.
    expect(resolveCalls()).toHaveLength(1);
    expect(resolveBody()).toEqual({
      decision: 'MATCH',
      expected_version: 1,
      reviewer_id: null,
    });
    // Detail and history re-read; the immutable advisory left alone.
    expect(requestsTo(detailUrl(PENDING_CASE_ID))).toBe(2);
    expect(requestsTo(eventsUrl(PENDING_CASE_ID))).toBe(2);
    expect(requestsTo(suggestionsUrl(PENDING_CASE_ID))).toBe(suggestionReadsBefore);
    // The queue is asked to re-read, so the case can leave the pending filter.
    expect(refreshQueue).toHaveBeenCalledTimes(1);

    // The workspace stays open on the case and shows its terminal state.
    expect(screen.getByText(PENDING_CASE_ID)).toBeInTheDocument();
    expect(
      within(screen.getByRole('region', { name: 'Recorded decision' })).getByText('Match'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Human decision' })).toBeNull();
    expect(screen.getByText('Decision recorded.')).toBeInTheDocument();
  });

  it('takes history from the refreshed read, not from the response event', async () => {
    const { user } = await renderPending();

    serveResolve(jsonResponse(matchResolveResponse), () => {
      serve(detailUrl(PENDING_CASE_ID), { ...matchedCaseDetail, review_case_id: PENDING_CASE_ID });
      serve(eventsUrl(PENDING_CASE_ID), [caseCreatedEvent, matchResolutionEvent]);
    });

    await decide(user, 'Match');

    await waitFor(() => {
      expect(
        within(screen.getByRole('region', { name: 'Event history' })).getByText('Resolved as Match'),
      ).toBeInTheDocument();
    });
    // History is rebuilt from GET /events, not by inserting the POST event.
    const timeline = within(screen.getByRole('region', { name: 'Event history' }));
    expect(timeline.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.queryByText(/unknown event/i)).toBeNull();
  });

  it.each([
    ['No match', 'NO_MATCH'],
    ['Defer', 'DEFER'],
  ] as const)('maps the %s control to the %s decision token', async (name, token) => {
    const { user } = await renderPending();
    serveResolve(jsonResponse(matchResolveResponse));

    await decide(user, name);

    await waitFor(() => {
      expect(resolveCalls()).toHaveLength(1);
    });
    expect(resolveBody()).toMatchObject({ decision: token });
  });
});

// ---------------------------------------------------------------------------
// Stale state
// ---------------------------------------------------------------------------

describe('a version conflict', () => {
  it('records nothing, re-reads, and makes the reviewer choose again', async () => {
    const { user } = await renderPending();

    serveResolve(errorResponse('REVIEW_CASE_VERSION_CONFLICT', 409), () => {
      serve(detailUrl(PENDING_CASE_ID), { ...pendingCaseDetail, version: 2 });
    });

    await decide(user, 'Match');

    await waitFor(() => {
      expect(
        screen.getByText('The case changed before this decision was recorded.'),
      ).toBeInTheDocument();
    });

    // No second attempt, ever, and never with a substituted version.
    expect(resolveCalls()).toHaveLength(1);
    expect(resolveBody()).toMatchObject({ expected_version: 1 });
    // The stale confirmation is gone and nothing is carried forward as chosen.
    expect(screen.queryByRole('region', { name: 'Confirm this decision' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();

    // The refreshed case is pending again, so the controls return — neutral.
    await waitFor(() => {
      expect(screen.getByRole('group', { name: 'Human decision' })).toBeInTheDocument();
    });
    for (const button of decisions().getAllByRole('button')) {
      expect(button).not.toHaveAttribute('aria-pressed');
    }
    expect(refreshQueue).toHaveBeenCalledTimes(1);
    expect(resolveCalls()).toHaveLength(1);
  });

  it('sends the refreshed version only when the reviewer decides again', async () => {
    const { user } = await renderPending();

    serveResolve(errorResponse('REVIEW_CASE_VERSION_CONFLICT', 409), () => {
      serve(detailUrl(PENDING_CASE_ID), { ...pendingCaseDetail, version: 5 });
    });
    await decide(user, 'Match');
    await waitFor(() => {
      expect(screen.getByRole('group', { name: 'Human decision' })).toBeInTheDocument();
    });

    serveResolve(jsonResponse(matchResolveResponse));
    await decide(user, 'No match');

    await waitFor(() => {
      expect(resolveCalls()).toHaveLength(2);
    });
    expect(resolveBody(1)).toEqual({
      decision: 'NO_MATCH',
      expected_version: 5,
      reviewer_id: null,
    });
  });

  it('shows the terminal state when the case turns out to be resolved already', async () => {
    const { user } = await renderPending();

    serveResolve(errorResponse('REVIEW_CASE_NOT_PENDING', 409), () => {
      serve(detailUrl(PENDING_CASE_ID), { ...matchedCaseDetail, review_case_id: PENDING_CASE_ID });
      serve(eventsUrl(PENDING_CASE_ID), [caseCreatedEvent, matchResolutionEvent]);
    });

    await decide(user, 'Defer');

    await waitFor(() => {
      expect(screen.getByText('This case is no longer pending.')).toBeInTheDocument();
    });
    expect(resolveCalls()).toHaveLength(1);
    await waitFor(() => {
      expect(screen.getByRole('region', { name: 'Recorded decision' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('group', { name: 'Human decision' })).toBeNull();
    // Nothing is claimed about who resolved it, when, or how.
    expect(screen.queryByText(/another reviewer|someone else/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Domain refusals
// ---------------------------------------------------------------------------

describe('a refused match', () => {
  it('reports the refusal without inventing a reason or a next decision', async () => {
    const { user } = await renderPending();
    serveResolve(errorResponse('MATCH_NOT_AUTHORIZED', 422));

    await decide(user, 'Match');

    await waitFor(() => {
      expect(
        screen.getByText('The review API did not authorize this match.'),
      ).toBeInTheDocument();
    });

    expect(resolveCalls()).toHaveLength(1);
    // Sprint 11 guarantees a refusal writes nothing, so the loaded case is
    // still true and is deliberately not re-read.
    expect(requestsTo(detailUrl(PENDING_CASE_ID))).toBe(1);
    expect(refreshQueue).not.toHaveBeenCalled();
    // No fabricated resolution, no automatic fallback, no bypass.
    expect(screen.queryByRole('region', { name: 'Recorded decision' })).toBeNull();
    for (const name of [/force/i, /override/i, /retry decision/i]) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
    // The reviewer may still decide, deliberately, and nothing is preselected.
    expect(screen.getByRole('group', { name: 'Human decision' })).toBeInTheDocument();
    expect(resolveCalls()).toHaveLength(1);
  });
});

describe('a contradicted decision', () => {
  it('reports the conflict, re-reads, and sends nothing further', async () => {
    const { user } = await renderPending();
    serveResolve(errorResponse('HUMAN_REVIEW_CONTRADICTION', 409));

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByText('The review API rejected this decision.')).toBeInTheDocument();
    });
    expect(
      screen.getByText(/conflicts with review state already recorded for these records/i),
    ).toBeInTheDocument();

    expect(resolveCalls()).toHaveLength(1);
    await waitFor(() => {
      expect(refreshQueue).toHaveBeenCalledTimes(1);
    });
    expect(requestsTo(detailUrl(PENDING_CASE_ID))).toBe(2);
    // No alternative decision was chosen on the reviewer's behalf.
    expect(resolveCalls()).toHaveLength(1);
    expect(screen.queryByText(/graph|component|union/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Operational failures
// ---------------------------------------------------------------------------

describe('an operational failure', () => {
  it.each([
    ['AUTHORIZATION_CONTEXT_UNAVAILABLE', 503, /cannot authorize decisions right now/i],
    ['AUTHORIZATION_CONFIG_UNAVAILABLE', 503, /cannot authorize decisions right now/i],
    ['REVIEW_QUEUE_NOT_READY', 503, /not ready to accept decisions/i],
  ] as const)('reports %s safely and sends nothing further', async (code, status, copy) => {
    const { user } = await renderPending();
    serveResolve(errorResponse(code, status, 'sqlite3 error reading /etc/review/config.yaml'));

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByText(copy)).toBeInTheDocument();
    });

    expect(resolveCalls()).toHaveLength(1);
    const notice = screen.getByRole('alert');
    for (const leak of ['sqlite', '/etc', '503', 'Traceback', '{']) {
      expect(notice.textContent).not.toContain(leak);
    }
    // Readiness is an operator concern; nothing here offers to fix it.
    for (const name of [/register/i, /bootstrap/i, /force/i, /retry decision/i]) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
    // Nothing was recorded and nothing changed, so nothing is re-read.
    expect(requestsTo(detailUrl(PENDING_CASE_ID))).toBe(1);
  });

  it.each([
    ['REVIEW_STORAGE_UNAVAILABLE', 503],
    ['REVIEW_STORAGE_CORRUPT', 500],
  ] as const)('treats %s as an unconfirmed write', async (code, status) => {
    const { user } = await renderPending();
    serveResolve(errorResponse(code, status));

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByText('This decision could not be confirmed.')).toBeInTheDocument();
    });
    expect(resolveCalls()).toHaveLength(1);
    await waitFor(() => {
      expect(refreshQueue).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByRole('button', { name: /retry decision/i })).toBeNull();
    expect(resolveCalls()).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Lost responses
// ---------------------------------------------------------------------------

describe('a write whose result never came back', () => {
  it('converges on the server’s answer when the decision did land', async () => {
    const { user } = await renderPending();

    // The classic lost response: the server applied it, the client never
    // learned so.
    serveResolveFailure(new TypeError('Failed to fetch'), () => {
      serve(detailUrl(PENDING_CASE_ID), { ...matchedCaseDetail, review_case_id: PENDING_CASE_ID });
      serve(eventsUrl(PENDING_CASE_ID), [caseCreatedEvent, matchResolutionEvent]);
    });

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByText('This decision could not be confirmed.')).toBeInTheDocument();
    });
    // Reconciliation, not repetition.
    await waitFor(() => {
      expect(screen.getByRole('region', { name: 'Recorded decision' })).toBeInTheDocument();
    });
    expect(resolveCalls()).toHaveLength(1);
    expect(screen.queryByRole('group', { name: 'Human decision' })).toBeNull();
    expect(screen.queryByRole('button', { name: /retry decision/i })).toBeNull();
  });

  it('leaves the case decidable again, but only by a deliberate new decision', async () => {
    const { user } = await renderPending();
    serveResolveFailure(new TypeError('Failed to fetch'));

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByText('This decision could not be confirmed.')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByRole('group', { name: 'Human decision' })).toBeInTheDocument();
    });

    // The authoritative read still says PENDING v1. Nothing resent it.
    expect(resolveCalls()).toHaveLength(1);
    expect(screen.queryByRole('region', { name: 'Confirm this decision' })).toBeNull();

    serveResolve(jsonResponse(matchResolveResponse));
    await decide(user, 'Defer');
    await waitFor(() => {
      expect(resolveCalls()).toHaveLength(2);
    });
    expect(resolveBody(1)).toMatchObject({ decision: 'DEFER', expected_version: 1 });
  });

  it('says the state could not be re-read when reconciliation itself fails', async () => {
    const { user } = await renderPending();

    serveResolveFailure(new TypeError('Failed to fetch'), () => {
      serveNetworkFailure(detailUrl(PENDING_CASE_ID));
    });

    await decide(user, 'Match');

    await waitFor(() => {
      expect(screen.getByText(/could not be re-read/i)).toBeInTheDocument();
    });
    expect(resolveCalls()).toHaveLength(1);
    expect(screen.queryByRole('group', { name: 'Human decision' })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The case moving underneath an open confirmation
// ---------------------------------------------------------------------------

describe('a confirmation that outlived its case', () => {
  it('cannot be submitted once the authoritative version moves', async () => {
    const { user } = await renderPending();

    await user.click(decisionButton('Match'));
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument();

    // A refresh lands a newer version while the confirmation is open.
    serve(detailUrl(PENDING_CASE_ID), { ...pendingCaseDetail, version: 2 });
    await user.click(screen.getByRole('button', { name: 'Refresh case' }));

    await waitFor(() => {
      expect(screen.getByText('That confirmation was cancelled.')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
    expect(resolveCalls()).toHaveLength(0);
  });

  it('does not follow the reviewer to another case', async () => {
    const { user } = await renderPending();
    serve(detailUrl(DEFERRED_CASE_ID), deferredCaseDetail);
    serve(eventsUrl(DEFERRED_CASE_ID), []);
    serve(suggestionsUrl(DEFERRED_CASE_ID), []);

    await user.click(decisionButton('Match'));
    select(DEFERRED_CASE_ID);

    await waitFor(() => {
      expect(screen.getByText('REC-A-0002')).toBeInTheDocument();
    });
    expect(screen.queryByRole('region', { name: 'Confirm this decision' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
    expect(resolveCalls()).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Phase boundary
// ---------------------------------------------------------------------------

describe('phase boundary', () => {
  it('uses only the published resolve endpoint, and only on confirmation', async () => {
    const { user } = await renderPending();
    serveResolve(jsonResponse(matchResolveResponse), () => {
      serve(detailUrl(PENDING_CASE_ID), { ...matchedCaseDetail, review_case_id: PENDING_CASE_ID });
    });

    await user.type(screen.getByLabelText('Reviewer label (optional)'), 'desk-3');
    await decide(user, 'Match');
    await waitFor(() => {
      expect(resolveCalls()).toHaveLength(1);
    });

    for (const [url, init] of http.mock.mock.calls) {
      const path = String(url);
      const method = (init?.method ?? 'GET').toUpperCase();
      expect(path.startsWith('/api/v1/review-cases')).toBe(true);
      expect(path).not.toMatch(/auth|login|token|session|tenant|org|register|bootstrap|generate/i);
      // The one non-GET is the published resolution endpoint.
      expect(method === 'GET' || (method === 'POST' && path.endsWith('/resolve'))).toBe(true);
    }
  });

  it('polls nothing and schedules nothing once a decision has settled', async () => {
    const { user } = await renderPending();
    serveResolve(jsonResponse(matchResolveResponse));
    await decide(user, 'Defer');
    await waitFor(() => {
      expect(screen.getByText('Decision recorded.')).toBeInTheDocument();
    });
    const settled = http.callCount();

    // Fake timers only now, so nothing has to interleave with user-event.
    vi.useFakeTimers();
    try {
      await vi.advanceTimersByTimeAsync(120_000);
    } finally {
      vi.useRealTimers();
    }

    expect(http.callCount()).toBe(settled);
    expect(resolveCalls()).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// A7: confirmation-open lock, and remount while a mutation is in flight
// ---------------------------------------------------------------------------

const PENDING_CASE_B_ID = 'RC-0000000000000009';
const pendingCaseBDetail = {
  ...pendingCaseDetail,
  review_case_id: PENDING_CASE_B_ID,
  record_a_id: 'REC-A-0009',
  record_b_id: 'REC-B-0009',
} satisfies ReviewCaseDetail;

describe('decision controls while a confirmation is open', () => {
  it.each(['Match', 'No match', 'Defer'] as const)(
    'disables the other decisions when %s confirmation is open',
    async (name) => {
      const { user } = await renderPending();

      await user.click(decisionButton(name));
      expect(screen.getByRole('region', { name: 'Confirm this decision' })).toBeInTheDocument();

      expect(decisionButton('Match')).toBeDisabled();
      expect(decisionButton('No match')).toBeDisabled();
      expect(decisionButton('Defer')).toBeDisabled();

      await user.click(decisionButton('Match'));
      await user.click(decisionButton('No match'));
      await user.click(decisionButton('Defer'));

      expect(resolveCalls()).toHaveLength(0);
      expect(
        within(screen.getByRole('region', { name: 'Confirm this decision' })).getByText(name),
      ).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Confirm' })).toBeInTheDocument();
    },
  );
});

describe('an in-flight resolution across a case switch', () => {
  it('does not auto-retry after remount; a second POST needs a new confirmation', async () => {
    const { user } = await renderPending();
    holdResolve();

    await user.click(decisionButton('Match'));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => {
      expect(screen.getByText('Recording decision…')).toBeInTheDocument();
    });
    expect(resolveCalls()).toHaveLength(1);

    serve(detailUrl(PENDING_CASE_B_ID), pendingCaseBDetail);
    serve(eventsUrl(PENDING_CASE_B_ID), [caseCreatedEvent]);
    serve(suggestionsUrl(PENDING_CASE_B_ID), [advisorySuggestion]);
    select(PENDING_CASE_B_ID);
    await waitFor(() => {
      expect(screen.getByRole('group', { name: 'Human decision' })).toBeInTheDocument();
    });
    expect(resolveCalls()).toHaveLength(1);

    serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    serve(eventsUrl(PENDING_CASE_ID), [caseCreatedEvent]);
    serve(suggestionsUrl(PENDING_CASE_ID), [advisorySuggestion]);
    select(PENDING_CASE_ID);
    await waitFor(() => {
      expect(screen.getByRole('group', { name: 'Human decision' })).toBeInTheDocument();
    });
    // The panel is keyed by case id, so the component-local in-flight ref is
    // gone. That is not a durable-write guarantee; the backend CAS is.
    expect(resolveCalls()).toHaveLength(1);
    expect(screen.queryByText('Recording decision…')).toBeNull();

    await user.click(decisionButton('No match'));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => {
      expect(resolveCalls()).toHaveLength(2);
    });
    expect(resolveBody(1)).toMatchObject({
      decision: 'NO_MATCH',
      expected_version: 1,
    });
  });
});
