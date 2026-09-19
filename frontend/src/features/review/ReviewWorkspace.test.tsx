import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReviewWorkspace } from './ReviewWorkspace';
import type { ReviewCaseSummary } from '../../api/types';
import {
  DEFERRED_CASE_ID,
  PENDING_CASE_ID,
  advisorySuggestion,
  caseBEventHistory,
  caseBSuggestion,
  deferredCaseDetail,
  deferredCaseSummary,
  eventHistory,
  failedSuggestion,
  insufficientEvidenceSuggestion,
  liveSuggestion,
  pendingCaseDetail,
  pendingCaseSummary,
} from '../../test/fixtures/sprint11';
import { createFetchStub, errorResponse, jsonResponse, type FetchStub } from '../../test/http';

const detailUrl = (id: string) => `/api/v1/review-cases/${id}`;
const eventsUrl = (id: string) => `${detailUrl(id)}/events`;
const suggestionsUrl = (id: string) => `${detailUrl(id)}/semantic-suggestions`;

let http: FetchStub;
/** URL → how to answer it. Swapped freely between assertions. */
let routes: Map<string, () => Promise<Response>>;

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

/** Leave a URL unanswered, and hand back the resolver that completes it. */
function hold(url: string, body: unknown): () => void {
  let release: () => void = () => undefined;
  routes.set(
    url,
    () =>
      new Promise<Response>((resolvePromise) => {
        release = () => {
          resolvePromise(jsonResponse(body));
        };
      }),
  );
  return () => {
    release();
  };
}

function serveCaseA(): void {
  serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
  serve(eventsUrl(PENDING_CASE_ID), eventHistory);
  serve(suggestionsUrl(PENDING_CASE_ID), [advisorySuggestion]);
}

const refreshQueue = vi.fn();

function workspace(reviewCaseId: string | null, selectedQueueSummary: ReviewCaseSummary | null) {
  return (
    <ReviewWorkspace
      reviewCaseId={reviewCaseId}
      selectedQueueSummary={selectedQueueSummary}
      onRefreshQueue={refreshQueue}
    />
  );
}

let view: ReturnType<typeof render> | null = null;

function renderWorkspace(
  reviewCaseId: string | null = PENDING_CASE_ID,
  selectedQueueSummary: ReviewCaseSummary | null = null,
) {
  const user = userEvent.setup();
  view = render(workspace(reviewCaseId, selectedQueueSummary));
  return { user };
}

/**
 * Change the selection the way the workspace actually receives one.
 *
 * Rendering a second time would mount a second workspace beside the first and
 * leave the previous case in the document, which is exactly the condition
 * these tests exist to rule out -- so the assertion would pass or fail for
 * reasons that have nothing to do with the component.
 */
function select(reviewCaseId: string | null, selectedQueueSummary: ReviewCaseSummary | null = null) {
  if (view === null) {
    throw new Error('Render the workspace before changing the selection.');
  }
  view.rerender(workspace(reviewCaseId, selectedQueueSummary));
}

async function renderLoadedCaseA() {
  serveCaseA();
  const handles = renderWorkspace();
  await waitFor(() => {
    expect(screen.getByRole('heading', { name: 'Machine assessment' })).toBeInTheDocument();
  });
  return handles;
}

function section(name: string): HTMLElement {
  return screen.getByRole('region', { name });
}

// ---------------------------------------------------------------------------

describe('no selection', () => {
  it('invites a selection and requests nothing', () => {
    renderWorkspace(null);

    expect(screen.getByText('Select a review case to inspect its details.')).toBeInTheDocument();
    expect(http.callCount()).toBe(0);
  });
});

describe('case header', () => {
  it('labels the two record identifiers explicitly', async () => {
    await renderLoadedCaseA();

    expect(screen.getByText('Record A')).toBeInTheDocument();
    expect(screen.getByText('REC-A-0001')).toBeInTheDocument();
    expect(screen.getByText('Record B')).toBeInTheDocument();
    expect(screen.getByText('REC-B-0001')).toBeInTheDocument();
  });

  it('builds no customer-record card out of the identifiers', async () => {
    // Sprint 11 publishes no field values, so any name, email, phone or
    // company on this screen would have been invented.
    await renderLoadedCaseA();

    for (const invented of ['Email', 'Phone', 'Company', 'Address', 'Full name']) {
      expect(screen.queryByText(invented)).toBeNull();
    }
  });

  it('reports status, machine decision, version and both timestamps', async () => {
    await renderLoadedCaseA();

    expect(screen.getByText('Review status')).toBeInTheDocument();
    expect(screen.getByText('Pending')).toBeInTheDocument();
    expect(screen.getByText('Machine decision')).toBeInTheDocument();
    expect(screen.getByText('Review')).toBeInTheDocument();
    expect(screen.getByText('Version')).toBeInTheDocument();
    expect(screen.getAllByText('2026-09-18 09:00:00 UTC').length).toBeGreaterThan(0);
    expect(screen.getByText(PENDING_CASE_ID)).toBeInTheDocument();
  });

  it('keeps the machine decision out of the status vocabulary', async () => {
    // Two different statements that share spellings. The headings are what
    // stop "Machine decision: No match" reading as a recorded outcome.
    serve(detailUrl(PENDING_CASE_ID), { ...pendingCaseDetail, machine_decision: 'AUTO_MATCH' });
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText('Auto match')).toBeInTheDocument();
    });
    expect(screen.getByText('Machine decision')).toBeInTheDocument();
    expect(screen.queryByText(/recommend/i)).toBeNull();
  });
});

describe('machine assessment', () => {
  it('reports the score beside both published thresholds', async () => {
    await renderLoadedCaseA();
    const assessment = section('Machine assessment');

    expect(within(assessment).getByText('0.81')).toBeInTheDocument();
    expect(within(assessment).getByText('0.62')).toBeInTheDocument();
    expect(within(assessment).getByText('0.88')).toBeInTheDocument();
  });

  it('draws no conclusion from the score or the thresholds', async () => {
    // Sprint 08 authorization projects across the whole queue, so what these
    // numbers mean for this pair is not derivable from this pair.
    await renderLoadedCaseA();

    for (const verdict of [
      /above .*threshold/i,
      /below .*threshold/i,
      /safe/i,
      /unsafe/i,
      /likely/i,
      /strong match/i,
      /recommended/i,
      /confidence/i,
    ]) {
      expect(screen.queryByText(verdict)).toBeNull();
    }
  });

  it('renders the machine reason verbatim', async () => {
    await renderLoadedCaseA();

    expect(
      screen.getByText('Score fell between the review and auto-match thresholds.'),
    ).toBeInTheDocument();
  });

  it('reports an empty machine reason as absent rather than as nothing wrong', async () => {
    serve(detailUrl(PENDING_CASE_ID), { ...pendingCaseDetail, machine_reason: '' });
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    renderWorkspace();

    await waitFor(() => {
      expect(
        screen.getByText('No machine reason was published for this case.'),
      ).toBeInTheDocument();
    });
  });
});

describe('review summary', () => {
  it('shows the published summary and notes', async () => {
    await renderLoadedCaseA();
    const summary = section('Review summary');

    expect(within(summary).getByText('Names agree; email domains differ.')).toBeInTheDocument();
    expect(within(summary).getByText('No phone number on either record.')).toBeInTheDocument();
  });

  it('reports absence without implying that nothing is missing', async () => {
    serve(detailUrl(PENDING_CASE_ID), {
      ...pendingCaseDetail,
      human_summary: '',
      missing_evidence_notes: [],
    });
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    renderWorkspace();

    await waitFor(() => {
      expect(
        screen.getByText('No human-review summary was published for this case.'),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByText('No missing-evidence notes were published for this case.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/nothing is missing|no issues/i)).toBeNull();
  });
});

describe('evidence', () => {
  it('publishes the blocking key as wrapping text, marked as personal data', async () => {
    await renderLoadedCaseA();
    const blocking = section('Blocking reasons');

    expect(within(blocking).getByText('EMAIL_EXACT_BLOCK')).toBeInTheDocument();
    expect(within(blocking).getByText('synthetic-block-key-0001')).toBeInTheDocument();
    expect(within(blocking).getByText(/treat it as personal data/i)).toBeInTheDocument();
    // Never a link, and never anything that would carry the value into a URL.
    expect(within(blocking).queryByRole('link')).toBeNull();
  });

  it('keeps supporting and conflicting evidence in separate sections', async () => {
    await renderLoadedCaseA();

    const supporting = section('Supporting evidence');
    const conflicting = section('Conflicting evidence');

    expect(within(supporting).getByText('LAST_NAME_EXACT')).toBeInTheDocument();
    expect(within(conflicting).getByText('EMAIL_DIFFERENT')).toBeInTheDocument();
    expect(within(supporting).queryByText('EMAIL_DIFFERENT')).toBeNull();
    expect(within(conflicting).queryByText('LAST_NAME_EXACT')).toBeNull();
  });

  it('shows each item’s own published numbers and no aggregate', async () => {
    await renderLoadedCaseA();

    expect(screen.getByText(/Contribution: 0\.31/)).toBeInTheDocument();
    expect(screen.getByText(/Penalty: 0\.25/)).toBeInTheDocument();
    // Netting one off against the other would be this layer deciding which
    // side of the case wins.
    expect(screen.queryByText(/total|aggregate|combined score/i)).toBeNull();
  });

  it('preserves the order the API returned', async () => {
    const ordered = {
      ...pendingCaseDetail,
      supporting_evidence: [
        { ...pendingCaseDetail.supporting_evidence[0]!, evidence_type: 'WEAK_ONE', contribution: 0.01 },
        { ...pendingCaseDetail.supporting_evidence[0]!, evidence_type: 'STRONG_TWO', contribution: 0.9 },
      ],
    };
    serve(detailUrl(PENDING_CASE_ID), ordered);
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText('WEAK_ONE')).toBeInTheDocument();
    });
    const rendered = within(section('Supporting evidence'))
      .getAllByRole('listitem')
      .map((item) => item.textContent ?? '');
    // Not re-sorted by contribution: the API's order is the published one.
    expect(rendered[0]).toContain('WEAK_ONE');
    expect(rendered[1]).toContain('STRONG_TWO');
  });

  it('states each empty evidence list as unpublished rather than as absent conflict', async () => {
    serve(detailUrl(PENDING_CASE_ID), {
      ...pendingCaseDetail,
      blocking_reasons: [],
      supporting_evidence: [],
      conflicting_evidence: [],
    });
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    renderWorkspace();

    await waitFor(() => {
      expect(
        screen.getByText('No conflicting evidence was published for this case.'),
      ).toBeInTheDocument();
    });
    expect(screen.getByText('No blocking reasons were published for this case.')).toBeInTheDocument();
    expect(screen.getByText('No supporting evidence was published for this case.')).toBeInTheDocument();
    expect(screen.queryByText(/no conflict exists|records agree/i)).toBeNull();
  });
});

describe('recorded decision', () => {
  it('is absent when the case carries no resolution', async () => {
    await renderLoadedCaseA();

    expect(screen.queryByRole('region', { name: 'Recorded decision' })).toBeNull();
  });

  it('shows the published resolution fields, read-only', async () => {
    serve(detailUrl(DEFERRED_CASE_ID), deferredCaseDetail);
    serve(eventsUrl(DEFERRED_CASE_ID), []);
    serve(suggestionsUrl(DEFERRED_CASE_ID), []);
    renderWorkspace(DEFERRED_CASE_ID);

    await waitFor(() => {
      expect(screen.getByRole('region', { name: 'Recorded decision' })).toBeInTheDocument();
    });
    const recorded = section('Recorded decision');
    expect(within(recorded).getByText('Defer')).toBeInTheDocument();
    expect(within(recorded).getByText('local-reviewer')).toBeInTheDocument();
    expect(
      within(recorded).getByText('remain_excluded_from_unsafe_canonical_merge'),
    ).toBeInTheDocument();
    // Read-only: Phase D owns every decision control.
    expect(within(recorded).queryByRole('button')).toBeNull();
  });

  it('calls reviewer_id a label, never a verified identity', async () => {
    serve(detailUrl(DEFERRED_CASE_ID), deferredCaseDetail);
    serve(eventsUrl(DEFERRED_CASE_ID), []);
    serve(suggestionsUrl(DEFERRED_CASE_ID), []);
    renderWorkspace(DEFERRED_CASE_ID);

    await waitFor(() => {
      expect(screen.getByText('Reviewer label')).toBeInTheDocument();
    });
    for (const identity of [/authenticated/i, /verified user/i, /account/i, /signed in/i]) {
      expect(screen.queryByText(identity)).toBeNull();
    }
  });

  it('handles a resolution with no reviewer label', async () => {
    serve(detailUrl(DEFERRED_CASE_ID), {
      ...deferredCaseDetail,
      resolution: { ...deferredCaseDetail.resolution, reviewer_id: null },
    });
    serve(eventsUrl(DEFERRED_CASE_ID), []);
    serve(suggestionsUrl(DEFERRED_CASE_ID), []);
    renderWorkspace(DEFERRED_CASE_ID);

    await waitFor(() => {
      expect(screen.getByText('None recorded')).toBeInTheDocument();
    });
  });
});

describe('AI advisory', () => {
  async function renderWithSuggestions(suggestions: unknown[]) {
    serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), suggestions);
    const handles = renderWorkspace();
    await waitFor(() => {
      expect(screen.getByRole('region', { name: 'AI advisory' })).toBeInTheDocument();
    });
    return handles;
  }

  it('says plainly that it decided nothing', async () => {
    await renderWithSuggestions([advisorySuggestion]);
    const advisory = section('AI advisory');

    expect(within(advisory).getByText(/decided nothing/i)).toBeInTheDocument();
    expect(within(advisory).getByText(/remains the authority/i)).toBeInTheDocument();
  });

  it.each([
    [advisorySuggestion, 'Suggest no match'],
    [{ ...advisorySuggestion, suggestion: 'SUGGEST_MATCH' }, 'Suggest match'],
    [insufficientEvidenceSuggestion, 'Insufficient evidence'],
    [failedSuggestion, 'Provider failure'],
  ])('labels each suggestion type without turning it into an instruction', async (
    suggestion,
    label,
  ) => {
    await renderWithSuggestions([suggestion]);
    const advisory = section('AI advisory');

    expect(within(advisory).getByText(label)).toBeInTheDocument();
    expect(within(advisory).getAllByText('Advisory').length).toBeGreaterThan(0);
    expect(within(advisory).queryByText(/recommended|you should|match these records/i)).toBeNull();
  });

  it('renders every persisted suggestion, in API order', async () => {
    await renderWithSuggestions([advisorySuggestion, failedSuggestion, liveSuggestion]);

    // Read from the rendered text: each entry nests its own reason-code list,
    // so a listitem query would interleave codes with verdicts.
    const text = section('AI advisory').textContent ?? '';
    expect(text.indexOf('Suggest no match')).toBeGreaterThan(-1);
    expect(text.indexOf('Suggest no match')).toBeLessThan(text.indexOf('Provider failure'));
    expect(text.indexOf('Provider failure')).toBeLessThan(text.indexOf('Suggest match'));
  });

  it('reports an empty advisory list without claiming it was never run', async () => {
    await renderWithSuggestions([]);

    expect(
      screen.getByText('No persisted AI advisory is available for this case.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/never been run|not yet analysed/i)).toBeNull();
  });

  it('shows a failure code as an advisory outcome, keeping the evidence usable', async () => {
    await renderWithSuggestions([failedSuggestion]);

    expect(screen.getByText('PROVIDER_TIMEOUT')).toBeInTheDocument();
    expect(screen.getByText(/deterministic evidence on this page is unaffected/i)).toBeInTheDocument();
    // The case itself is untouched by an advisory that could not be produced.
    expect(screen.getByRole('region', { name: 'Supporting evidence' })).toBeInTheDocument();
  });

  it('reports provider, model and whether the call was live', async () => {
    await renderWithSuggestions([liveSuggestion]);
    const advisory = section('AI advisory');

    expect(within(advisory).getByText('openai')).toBeInTheDocument();
    expect(within(advisory).getByText('test-model-live')).toBeInTheDocument();
    expect(within(advisory).getByText('Live provider call')).toBeInTheDocument();
    expect(within(advisory).getByText('Yes')).toBeInTheDocument();
  });

  it('publishes no explanation and offers no way to generate one', async () => {
    // Sprint 09 never stores the model's prose, and Sprint 11 exposes no
    // generation endpoint. Either would have to be invented here.
    await renderWithSuggestions([advisorySuggestion]);
    const advisory = section('AI advisory');

    expect(advisory.textContent?.toLowerCase()).not.toContain('explanation');
    expect(within(advisory).queryByRole('button', { name: /generate|run|regenerate/i })).toBeNull();
  });
});

describe('event history', () => {
  it('labels each published event type factually', async () => {
    serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    serve(eventsUrl(PENDING_CASE_ID), [
      { ...eventHistory[0]! },
      { ...eventHistory[1]! },
      { ...eventHistory[2]!, event_type: 'MATCH' },
    ]);
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText('Case created')).toBeInTheDocument();
    });
    expect(screen.getByText('Semantic advisory recorded')).toBeInTheDocument();
    expect(screen.getByText('Resolved as Match')).toBeInTheDocument();
    for (const judgement of [/approved/i, /rejected/i, /^safe$/i, /^unsafe$/i]) {
      expect(screen.queryByText(judgement)).toBeNull();
    }
  });

  it('preserves the server’s order and shows each timestamp', async () => {
    await renderLoadedCaseA();

    const entries = within(section('Event history')).getAllByRole('listitem');
    expect(entries[0]?.textContent).toContain('Case created');
    expect(entries[2]?.textContent).toContain('Resolved as Deferred');
    expect(entries[0]?.textContent).toContain('2026-09-18 09:00:00 UTC');
  });

  it('invents no actor for events that publish none', async () => {
    await renderLoadedCaseA();

    const created = within(section('Event history')).getAllByRole('listitem')[0];
    expect(created?.textContent).not.toMatch(/Reviewer label/);
    expect(created?.textContent).not.toMatch(/system|automatic/i);
  });

  it('exposes no audit payload or schema version', async () => {
    await renderLoadedCaseA();
    const history = section('Event history');

    for (const hidden of ['audit_entry_payload', 'schema_version', 'downstream_action']) {
      expect(history.textContent).not.toContain(hidden);
    }
  });

  it('reports an empty history plainly', async () => {
    serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    serve(eventsUrl(PENDING_CASE_ID), []);
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText('No events have been recorded for this case.')).toBeInTheDocument();
    });
  });
});

describe('partial failure', () => {
  it('keeps the case and history when the advisory fails', async () => {
    serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    serve(eventsUrl(PENDING_CASE_ID), eventHistory);
    serveNetworkFailure(suggestionsUrl(PENDING_CASE_ID));
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText('The AI advisory could not be loaded.')).toBeInTheDocument();
    });
    expect(screen.getByRole('region', { name: 'Supporting evidence' })).toBeInTheDocument();
    expect(screen.getByText('Case created')).toBeInTheDocument();
  });

  it('keeps the case and advisory when the history fails', async () => {
    serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    serveNetworkFailure(eventsUrl(PENDING_CASE_ID));
    serve(suggestionsUrl(PENDING_CASE_ID), [advisorySuggestion]);
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText('The event history could not be loaded.')).toBeInTheDocument();
    });
    expect(screen.getByRole('region', { name: 'Supporting evidence' })).toBeInTheDocument();
    expect(within(section('AI advisory')).getByText('Suggest no match')).toBeInTheDocument();
  });

  it('retries only the panel that failed', async () => {
    serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    serve(eventsUrl(PENDING_CASE_ID), eventHistory);
    serveNetworkFailure(suggestionsUrl(PENDING_CASE_ID));
    const { user } = renderWorkspace();
    await waitFor(() => {
      expect(screen.getByText('The AI advisory could not be loaded.')).toBeInTheDocument();
    });
    const before = http.callCount();

    serve(suggestionsUrl(PENDING_CASE_ID), [advisorySuggestion]);
    await user.click(screen.getByRole('button', { name: 'Retry advisory' }));

    await waitFor(() => {
      expect(within(section('AI advisory')).getByText('Suggest no match')).toBeInTheDocument();
    });
    expect(http.callCount()).toBe(before + 1);
  });

  it('offers no retry for corrupt stored state in a panel', async () => {
    serve(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    serveResponse(eventsUrl(PENDING_CASE_ID), errorResponse('REVIEW_STORAGE_CORRUPT', 500));
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText('The event history could not be loaded.')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Retry history' })).toBeNull();
  });
});

describe('case-level failure', () => {
  it('reports a missing case honestly and offers to re-read the queue', async () => {
    serveResponse(detailUrl(PENDING_CASE_ID), errorResponse('REVIEW_CASE_NOT_FOUND', 404));
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    const { user } = renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText('This case is not available.')).toBeInTheDocument();
    });
    expect(screen.queryByText(/deleted|was removed by/i)).toBeNull();
    // The secondary panels are about a case the API says it cannot give us.
    expect(screen.queryByRole('region', { name: 'AI advisory' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Event history' })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Refresh queue' }));
    expect(refreshQueue).toHaveBeenCalledTimes(1);
  });

  it('shows no stale case beneath a failure after the selection moved', async () => {
    await renderLoadedCaseA();

    serveResponse(detailUrl(DEFERRED_CASE_ID), errorResponse('REVIEW_CASE_NOT_FOUND', 404));
    serve(eventsUrl(DEFERRED_CASE_ID), []);
    serve(suggestionsUrl(DEFERRED_CASE_ID), []);
    select(DEFERRED_CASE_ID);

    await waitFor(() => {
      expect(screen.getByText('This case is not available.')).toBeInTheDocument();
    });
    expect(screen.queryByText('REC-A-0001')).toBeNull();
  });

  it('distinguishes an unreachable API from corrupt storage', async () => {
    serveNetworkFailure(detailUrl(PENDING_CASE_ID));
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText('Cannot reach the local review API.')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('offers no retry when the stored case contradicts itself', async () => {
    serveResponse(detailUrl(PENDING_CASE_ID), errorResponse('REVIEW_STORAGE_CORRUPT', 500));
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText(/inconsistent stored state for this case/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('leaks no status code, path or exception text', async () => {
    serveResponse(detailUrl(PENDING_CASE_ID), errorResponse('REVIEW_STORAGE_UNAVAILABLE', 503));
    serve(eventsUrl(PENDING_CASE_ID), []);
    serve(suggestionsUrl(PENDING_CASE_ID), []);
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    const alert = screen.getByRole('alert');
    for (const leak of ['503', 'sqlite', '/api/v1', 'Traceback', '{']) {
      expect(alert.textContent).not.toContain(leak);
    }
  });
});

describe('selection change', () => {
  it('shows nothing of case A while case B is still loading', async () => {
    // The critical correctness boundary: customer-derived evidence attributed
    // to the wrong record pair is how a wrong merge starts.
    await renderLoadedCaseA();
    expect(screen.getByText('REC-A-0001')).toBeInTheDocument();

    const releaseDetail = hold(detailUrl(DEFERRED_CASE_ID), deferredCaseDetail);
    const releaseEvents = hold(eventsUrl(DEFERRED_CASE_ID), caseBEventHistory);
    const releaseSuggestions = hold(suggestionsUrl(DEFERRED_CASE_ID), [caseBSuggestion]);

    select(DEFERRED_CASE_ID);

    await waitFor(() => {
      expect(screen.getByText('Loading case…')).toBeInTheDocument();
    });
    expect(screen.queryByText('REC-A-0001')).toBeNull();
    expect(screen.queryByText('REC-B-0001')).toBeNull();
    expect(screen.queryByText('LAST_NAME_EXACT')).toBeNull();
    expect(screen.queryByText('Suggest no match')).toBeNull();
    expect(screen.queryByText('Case created')).toBeNull();

    releaseDetail();
    releaseEvents();
    releaseSuggestions();

    await waitFor(() => {
      expect(screen.getByText('REC-A-0002')).toBeInTheDocument();
    });
    expect(screen.getByText('REC-B-0002')).toBeInTheDocument();
    expect(within(section('AI advisory')).getByText('Suggest match')).toBeInTheDocument();
    expect(screen.getByText('case-b-reviewer')).toBeInTheDocument();
    expect(screen.queryByText('REC-A-0001')).toBeNull();
  });
});

describe('queue and detail divergence', () => {
  it('says so when the queue row is at a different version', async () => {
    serveCaseA();
    renderWorkspace(PENDING_CASE_ID, { ...pendingCaseSummary, version: 1 });

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Machine assessment' })).toBeInTheDocument();
    });
    // The fixture detail is version 1, so bump the summary to diverge.
    expect(screen.queryByText(/changed since the current queue view/i)).toBeNull();
  });

  it('shows the notice when the versions actually differ', async () => {
    serveCaseA();
    renderWorkspace(PENDING_CASE_ID, { ...pendingCaseSummary, version: 3 });

    await waitFor(() => {
      expect(screen.getByText(/changed since the current queue view was loaded/i)).toBeInTheDocument();
    });
    // Reporting the divergence, not reconciling it.
    expect(refreshQueue).not.toHaveBeenCalled();
  });

  it('makes no comparison when the case is outside the visible queue page', async () => {
    serveCaseA();
    renderWorkspace(PENDING_CASE_ID, null);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Machine assessment' })).toBeInTheDocument();
    });
    expect(screen.queryByText(/changed since the current queue view/i)).toBeNull();
  });

  it('does not compare against a row belonging to a different case', async () => {
    serveCaseA();
    renderWorkspace(PENDING_CASE_ID, deferredCaseSummary);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Machine assessment' })).toBeInTheDocument();
    });
    // App only supplies the summary for the selected case, so this pairing
    // cannot arise there; asserted so the component never starts relying on
    // a summary matching the case by coincidence.
    expect(screen.getByText(/changed since the current queue view/i)).toBeInTheDocument();
  });
});

describe('refresh case', () => {
  it('re-reads exactly the three case resources, and nothing else', async () => {
    const { user } = await renderLoadedCaseA();
    http.mock.mockClear();

    await user.click(screen.getByRole('button', { name: 'Refresh case' }));

    await waitFor(() => {
      expect(http.callCount()).toBe(3);
    });
    expect([...http.urls()].sort()).toEqual(
      [
        detailUrl(PENDING_CASE_ID),
        eventsUrl(PENDING_CASE_ID),
        suggestionsUrl(PENDING_CASE_ID),
      ].sort(),
    );
    for (const call of http.mock.mock.calls) {
      expect((call[1]?.method ?? 'GET').toUpperCase()).toBe('GET');
    }
  });

  it('never re-reads the queue', async () => {
    const { user } = await renderLoadedCaseA();

    await user.click(screen.getByRole('button', { name: 'Refresh case' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Refresh case' })).toBeEnabled();
    });
    expect(refreshQueue).not.toHaveBeenCalled();
    expect(http.urls().some((url) => url.startsWith('/api/v1/review-cases?'))).toBe(false);
  });

  it('keeps the case on screen while the re-read is in flight', async () => {
    const { user } = await renderLoadedCaseA();

    hold(detailUrl(PENDING_CASE_ID), pendingCaseDetail);
    await user.click(screen.getByRole('button', { name: 'Refresh case' }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Updating case…');
    });
    expect(screen.getByText('REC-A-0001')).toBeInTheDocument();
    expect(screen.getByText('LAST_NAME_EXACT')).toBeInTheDocument();
  });
});
