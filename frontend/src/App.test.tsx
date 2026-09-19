import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import type { ReviewCaseListResponse } from './api/types';
import {
  DEFERRED_CASE_ID,
  PENDING_CASE_ID,
  advisorySuggestion,
  caseBEventHistory,
  caseBSuggestion,
  deferredCaseDetail,
  deferredCaseSummary,
  eventHistory,
  pendingCaseDetail,
  pendingCaseSummary,
} from './test/fixtures/sprint11';
import { createFetchStub, jsonResponse, type FetchStub } from './test/http';

const LIST_PREFIX = '/api/v1/review-cases?';
const detailUrl = (id: string) => `/api/v1/review-cases/${id}`;

let http: FetchStub;
/** Queue pages, answered in order; the last one repeats once the queue runs dry. */
let listPages: ReviewCaseListResponse[];

beforeEach(() => {
  http = createFetchStub();
  listPages = [];

  // Routed by URL rather than by call order. Selecting a case fires three
  // workspace reads alongside the queue's, so a sequential stub would hand a
  // queue page to a detail request depending on which started first.
  http.routeBy((url) => {
    if (url.startsWith(LIST_PREFIX)) {
      return jsonResponse(nextListPage());
    }
    if (url.endsWith('/events')) {
      return jsonResponse(url.includes(DEFERRED_CASE_ID) ? caseBEventHistory : eventHistory);
    }
    if (url.endsWith('/semantic-suggestions')) {
      return jsonResponse(url.includes(DEFERRED_CASE_ID) ? [caseBSuggestion] : [advisorySuggestion]);
    }
    if (url === detailUrl(DEFERRED_CASE_ID)) {
      return jsonResponse(deferredCaseDetail);
    }
    return jsonResponse(pendingCaseDetail);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function nextListPage(): ReviewCaseListResponse {
  const next = listPages.length > 1 ? listPages.shift() : listPages[0];
  return next ?? FIRST_PAGE;
}

/**
 * A page as the server would describe it.
 *
 * `limit` is part of the response on purpose: these tests use a server page
 * size of 1 so paging is reachable without inventing fifty cases, and the
 * controls are expected to follow the limit the server echoed rather than the
 * one the workspace asked for.
 */
function page(overrides: Partial<ReviewCaseListResponse>): ReviewCaseListResponse {
  return { items: [], count: 0, total: 0, limit: 1, offset: 0, ...overrides };
}

const FIRST_PAGE = page({ items: [pendingCaseSummary], count: 1, total: 2, offset: 0 });
const SECOND_PAGE = page({ items: [deferredCaseSummary], count: 1, total: 2, offset: 1 });

/**
 * The queue's own rows.
 *
 * Scoped to the queue region: once a case is open the workspace renders lists
 * of its own -- evidence, reason codes, the timeline -- and an unscoped list
 * query would pick one of those instead.
 */
function queueRegion(): HTMLElement {
  return screen.getByRole('region', { name: 'Review queue' });
}

function rows() {
  return within(within(queueRegion()).getByRole('list')).getAllByRole('button');
}

/** Only the queue's own requests, so workspace reads never skew a page count. */
function listUrls(): string[] {
  return http.urls().filter((url) => url.startsWith(LIST_PREFIX));
}

async function renderWorkspace(pages: ReviewCaseListResponse[] = [FIRST_PAGE]) {
  listPages = [...pages];
  const user = userEvent.setup();
  render(<App />);
  await waitFor(() => {
    expect(within(queueRegion()).getByRole('list')).toBeInTheDocument();
  });
  return user;
}

async function selectFirstRow(user: Awaited<ReturnType<typeof renderWorkspace>>) {
  await user.click(rows()[0] as HTMLElement);
  await waitFor(() => {
    expect(screen.getByRole('heading', { name: 'Machine assessment' })).toBeInTheDocument();
  });
}

describe('the workspace shell', () => {
  it('states that the tool is local and unauthenticated', async () => {
    await renderWorkspace();

    const notice = screen.getByRole('note');
    expect(notice).toHaveTextContent(/not authenticated/i);
    expect(notice).toHaveTextContent(/not internet-ready/i);
  });

  it('gives the queue and the workspace their own labelled regions', async () => {
    await renderWorkspace();

    expect(screen.getByRole('heading', { level: 1, name: 'Reviewer UI' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Review queue' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Review workspace' })).toBeInTheDocument();
  });

  it('opens on the pending queue, because that is the work', async () => {
    await renderWorkspace();

    expect(listUrls()[0]).toBe('/api/v1/review-cases?status=PENDING&limit=50&offset=0');
    expect(screen.getByRole('button', { name: 'Pending' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('requests nothing for the workspace until a case is selected', async () => {
    await renderWorkspace();

    expect(screen.getByText('Select a review case to inspect its details.')).toBeInTheDocument();
    expect(http.urls().every((url) => url.startsWith(LIST_PREFIX))).toBe(true);
  });
});

describe('selecting a case', () => {
  it('loads the case, its advisory and its history', async () => {
    const user = await renderWorkspace();

    await selectFirstRow(user);

    expect(screen.getByText('REC-A-0001')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'AI advisory' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Event history' })).toBeInTheDocument();
    expect(http.urls()).toContain(detailUrl(PENDING_CASE_ID));
    expect(http.urls()).toContain(`${detailUrl(PENDING_CASE_ID)}/events`);
    expect(http.urls()).toContain(`${detailUrl(PENDING_CASE_ID)}/semantic-suggestions`);
  });

  it('replaces the workspace when another row is activated', async () => {
    listPages = [page({ items: [pendingCaseSummary, deferredCaseSummary], count: 2, total: 2 })];
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => {
      expect(within(queueRegion()).getByRole('list')).toBeInTheDocument();
    });

    await selectFirstRow(user);
    await user.click(rows()[1] as HTMLElement);

    await waitFor(() => {
      expect(screen.getByText('REC-A-0002')).toBeInTheDocument();
    });
    expect(screen.queryByText('REC-A-0001')).toBeNull();
  });

  it('keeps the case open when the queue view moves away from it', async () => {
    const user = await renderWorkspace([FIRST_PAGE, SECOND_PAGE]);
    await selectFirstRow(user);

    await user.click(screen.getByRole('button', { name: 'Next page' }));

    await waitFor(() => {
      expect(screen.getByText(/not in the current queue view/i)).toBeInTheDocument();
    });
    // The selection is still real and the workspace still shows it.
    expect(screen.getByText('REC-A-0001')).toBeInTheDocument();
    expect(screen.queryByText(/deleted|no longer exists|not found/i)).toBeNull();
  });

  it('keeps the case open across a filter change', async () => {
    const user = await renderWorkspace([
      FIRST_PAGE,
      page({ items: [deferredCaseSummary], count: 1, total: 1 }),
    ]);
    await selectFirstRow(user);

    await user.click(screen.getByRole('button', { name: 'Deferred' }));

    await waitFor(() => {
      expect(screen.getByText(/not in the current queue view/i)).toBeInTheDocument();
    });
    expect(screen.getByText('REC-A-0001')).toBeInTheDocument();
  });
});

describe('filter and page coordination', () => {
  it('returns to the first page when the filter changes', async () => {
    const user = await renderWorkspace([FIRST_PAGE, SECOND_PAGE, FIRST_PAGE]);

    await user.click(screen.getByRole('button', { name: 'Next page' }));
    await waitFor(() => {
      expect(listUrls().at(-1)).toBe('/api/v1/review-cases?status=PENDING&limit=50&offset=1');
    });

    await user.click(screen.getByRole('button', { name: 'Deferred' }));

    await waitFor(() => {
      expect(listUrls().at(-1)).toBe('/api/v1/review-cases?status=DEFERRED&limit=50&offset=0');
    });
  });

  it('drops the status parameter for All rather than sending a token', async () => {
    const user = await renderWorkspace();

    await user.click(screen.getByRole('button', { name: 'All' }));

    await waitFor(() => {
      expect(listUrls().at(-1)).toBe('/api/v1/review-cases?limit=50&offset=0');
    });
    expect(listUrls().some((url) => /status=(&|$|ALL|null)/.test(url))).toBe(false);
  });
});

describe('a page that has fallen off the end of the queue', () => {
  it('returns to the first page with exactly one recovery request', async () => {
    // The queue can shrink between two requests. The server answers honestly
    // with an empty page that still counts the whole filtered set.
    const user = await renderWorkspace([
      FIRST_PAGE,
      page({ items: [], count: 0, total: 2, offset: 1 }),
      FIRST_PAGE,
    ]);

    await user.click(screen.getByRole('button', { name: 'Next page' }));

    await waitFor(() => {
      expect(listUrls().at(-1)).toBe('/api/v1/review-cases?status=PENDING&limit=50&offset=0');
    });
    // Initial load, the empty page, and one recovery. No loop.
    expect(listUrls()).toHaveLength(3);
    expect(rows()).toHaveLength(1);
    expect(screen.getByText('Showing 1–1 of 2')).toBeInTheDocument();
  });

  it('leaves a legitimately empty queue alone', async () => {
    // total == 0 is a real answer, not a stale page, so nothing is recovered.
    const user = await renderWorkspace([
      FIRST_PAGE,
      page({ items: [], count: 0, total: 0, offset: 1 }),
    ]);

    await user.click(screen.getByRole('button', { name: 'Next page' }));

    await waitFor(() => {
      expect(screen.getByText('No cases with status Pending.')).toBeInTheDocument();
    });
    expect(listUrls()).toHaveLength(2);
  });
});

describe('refresh scopes stay separate', () => {
  it('refreshes only the queue from the queue control', async () => {
    const user = await renderWorkspace();
    await selectFirstRow(user);
    const listBefore = listUrls().length;
    const totalBefore = http.callCount();

    await user.click(screen.getByRole('button', { name: 'Refresh queue' }));

    await waitFor(() => {
      expect(listUrls()).toHaveLength(listBefore + 1);
    });
    expect(http.callCount()).toBe(totalBefore + 1);
  });

  it('refreshes only the three case reads from the case control', async () => {
    const user = await renderWorkspace();
    await selectFirstRow(user);
    const listBefore = listUrls().length;
    const totalBefore = http.callCount();

    await user.click(screen.getByRole('button', { name: 'Refresh case' }));

    await waitFor(() => {
      expect(http.callCount()).toBe(totalBefore + 3);
    });
    expect(listUrls()).toHaveLength(listBefore);
  });
});

describe('phase boundary', () => {
  it('uses only the four published read endpoints', async () => {
    const user = await renderWorkspace();
    await selectFirstRow(user);
    await user.click(screen.getByRole('button', { name: 'Refresh case' }));
    await waitFor(() => {
      expect(http.callCount()).toBeGreaterThan(4);
    });

    for (const url of http.urls()) {
      const isRead =
        url.startsWith(LIST_PREFIX) ||
        /^\/api\/v1\/review-cases\/[^/]+$/.test(url) ||
        /^\/api\/v1\/review-cases\/[^/]+\/events$/.test(url) ||
        /^\/api\/v1\/review-cases\/[^/]+\/semantic-suggestions$/.test(url);
      expect(isRead, `unexpected request to ${url}`).toBe(true);
      expect(url).not.toContain('/resolve');
    }
  });

  it('issues no write request at all', async () => {
    const user = await renderWorkspace();
    await selectFirstRow(user);
    await user.click(screen.getByRole('button', { name: 'Refresh case' }));

    for (const call of http.mock.mock.calls) {
      expect((call[1]?.method ?? 'GET').toUpperCase()).toBe('GET');
    }
  });

  it('offers no decision control anywhere on the screen', async () => {
    // Phase D owns MATCH, NO_MATCH and DEFER. Nothing here may resolve a case.
    const user = await renderWorkspace();
    await selectFirstRow(user);

    // Scoped to the workspace: "Match" and "No match" exist in the queue as
    // status filters, which are not decision controls.
    const workspace = within(screen.getByRole('region', { name: 'Review workspace' }));
    for (const name of [/^match$/i, /^no match$/i, /^defer$/i, /resolve/i]) {
      expect(workspace.queryByRole('button', { name })).toBeNull();
    }
    expect(workspace.getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Refresh case',
    ]);
  });
});
