import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import type { ReviewCaseListResponse } from './api/types';
import {
  DEFERRED_CASE_ID,
  PENDING_CASE_ID,
  deferredCaseSummary,
  pendingCaseSummary,
} from './test/fixtures/sprint11';
import { createFetchStub, type FetchStub } from './test/http';

let http: FetchStub;

beforeEach(() => {
  http = createFetchStub();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

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

function rows() {
  return within(screen.getByRole('list')).getAllByRole('button');
}

async function renderWorkspace() {
  const user = userEvent.setup();
  render(<App />);
  await waitFor(() => {
    expect(screen.getByRole('list')).toBeInTheDocument();
  });
  return user;
}

describe('the workspace shell', () => {
  it('states that the tool is local and unauthenticated', async () => {
    http.alwaysRespond(FIRST_PAGE);
    await renderWorkspace();

    const notice = screen.getByRole('note');
    expect(notice).toHaveTextContent(/not authenticated/i);
    expect(notice).toHaveTextContent(/not internet-ready/i);
  });

  it('gives the queue and the workspace their own labelled regions', async () => {
    http.alwaysRespond(FIRST_PAGE);
    await renderWorkspace();

    expect(screen.getByRole('heading', { level: 1, name: 'Reviewer UI' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Review queue' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Review workspace' })).toBeInTheDocument();
  });

  it('opens on the pending queue, because that is the work', async () => {
    http.alwaysRespond(FIRST_PAGE);
    await renderWorkspace();

    expect(http.urls()[0]).toBe('/api/v1/review-cases?status=PENDING&limit=50&offset=0');
    expect(screen.getByRole('button', { name: 'Pending' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});

describe('selection', () => {
  it('invites a selection before one is made', async () => {
    http.alwaysRespond(FIRST_PAGE);
    await renderWorkspace();

    expect(screen.getByText('Select a review case to inspect its details.')).toBeInTheDocument();
  });

  it('reports the selected case and nothing about it', async () => {
    http.alwaysRespond(FIRST_PAGE);
    const user = await renderWorkspace();

    await user.click(rows()[0] as HTMLElement);

    const workspace = screen.getByRole('region', { name: 'Review workspace' });
    expect(workspace).toHaveTextContent(`Case selected: ${PENDING_CASE_ID}`);
    // No detail is fetched in this phase, so no detail may be shown. Anything
    // here would be either copied out of a queue row or invented.
    expect(workspace).not.toHaveTextContent(/0\.81|Pending|REC-A-0001/);
  });

  it('replaces the selection when another row is activated', async () => {
    http.alwaysRespond(page({ items: [pendingCaseSummary, deferredCaseSummary], count: 2, total: 2 }));
    const user = await renderWorkspace();

    await user.click(rows()[0] as HTMLElement);
    await user.click(rows()[1] as HTMLElement);

    expect(screen.getByRole('region', { name: 'Review workspace' })).toHaveTextContent(
      `Case selected: ${DEFERRED_CASE_ID}`,
    );
  });

  it('survives a filter change and explains where the case went', async () => {
    http.respondOnce(FIRST_PAGE);
    const user = await renderWorkspace();
    await user.click(rows()[0] as HTMLElement);

    http.respondOnce(page({ items: [deferredCaseSummary], count: 1, total: 1 }));
    await user.click(screen.getByRole('button', { name: 'Deferred' }));

    await waitFor(() => {
      expect(screen.getByText(/not in the current queue view/i)).toBeInTheDocument();
    });
    // The case is still selected -- only the view moved away from it.
    expect(screen.getByRole('region', { name: 'Review workspace' })).toHaveTextContent(
      `Case selected: ${PENDING_CASE_ID}`,
    );
    // And the UI must not claim something it cannot know.
    expect(screen.queryByText(/deleted|no longer exists|not found/i)).toBeNull();
  });

  it('survives a page change', async () => {
    http.respondOnce(FIRST_PAGE);
    const user = await renderWorkspace();
    await user.click(rows()[0] as HTMLElement);

    http.respondOnce(SECOND_PAGE);
    await user.click(screen.getByRole('button', { name: 'Next page' }));

    await waitFor(() => {
      expect(screen.getByText(/not in the current queue view/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('region', { name: 'Review workspace' })).toHaveTextContent(
      `Case selected: ${PENDING_CASE_ID}`,
    );
  });
});

describe('filter and page coordination', () => {
  it('returns to the first page when the filter changes', async () => {
    // Page four of the deferred cases is not page four of the pending ones.
    http.respondOnce(FIRST_PAGE);
    const user = await renderWorkspace();

    http.respondOnce(SECOND_PAGE);
    await user.click(screen.getByRole('button', { name: 'Next page' }));
    await waitFor(() => {
      expect(http.lastUrl()).toBe('/api/v1/review-cases?status=PENDING&limit=50&offset=1');
    });

    http.respondOnce(page({ items: [deferredCaseSummary], count: 1, total: 1 }));
    await user.click(screen.getByRole('button', { name: 'Deferred' }));

    await waitFor(() => {
      expect(http.lastUrl()).toBe('/api/v1/review-cases?status=DEFERRED&limit=50&offset=0');
    });
  });

  it('drops the status parameter for All rather than sending a token', async () => {
    http.respondOnce(FIRST_PAGE);
    const user = await renderWorkspace();

    http.respondOnce(FIRST_PAGE);
    await user.click(screen.getByRole('button', { name: 'All' }));

    await waitFor(() => {
      expect(http.lastUrl()).toBe('/api/v1/review-cases?limit=50&offset=0');
    });
    expect(http.urls().some((url) => /status=(&|$|ALL|null)/.test(url))).toBe(false);
  });
});

describe('a page that has fallen off the end of the queue', () => {
  it('returns to the first page with exactly one recovery request', async () => {
    // The queue can shrink between two requests. The server answers honestly
    // with an empty page that still counts the whole filtered set.
    http.respondOnce(FIRST_PAGE);
    const user = await renderWorkspace();

    http.respondOnce(page({ items: [], count: 0, total: 2, offset: 1 }));
    await user.click(screen.getByRole('button', { name: 'Next page' }));

    http.respondOnce(FIRST_PAGE);

    await waitFor(() => {
      expect(http.lastUrl()).toBe('/api/v1/review-cases?status=PENDING&limit=50&offset=0');
    });
    // Initial load, the empty page, and one recovery. No loop.
    expect(http.callCount()).toBe(3);
    expect(rows()).toHaveLength(1);
    expect(screen.getByText('Showing 1–1 of 2')).toBeInTheDocument();
  });

  it('leaves a legitimately empty queue alone', async () => {
    // total == 0 is a real answer, not a stale page, so nothing is recovered.
    http.respondOnce(FIRST_PAGE);
    const user = await renderWorkspace();

    http.respondOnce(page({ items: [], count: 0, total: 0, offset: 1 }));
    await user.click(screen.getByRole('button', { name: 'Next page' }));

    await waitFor(() => {
      expect(screen.getByText('No cases with status Pending.')).toBeInTheDocument();
    });
    expect(http.callCount()).toBe(2);
  });
});

describe('phase boundary', () => {
  it('requests the list endpoint and nothing else, even after a selection', async () => {
    http.alwaysRespond(FIRST_PAGE);
    const user = await renderWorkspace();

    await user.click(rows()[0] as HTMLElement);
    await user.click(screen.getByRole('button', { name: 'Refresh queue' }));
    await waitFor(() => {
      expect(http.callCount()).toBeGreaterThan(1);
    });

    for (const url of http.urls()) {
      expect(url.startsWith('/api/v1/review-cases?')).toBe(true);
      expect(url).not.toMatch(/\/events|\/semantic-suggestions|\/resolve/);
    }
  });

  it('issues no write request at all', async () => {
    http.alwaysRespond(FIRST_PAGE);
    const user = await renderWorkspace();

    await user.click(rows()[0] as HTMLElement);

    for (const call of http.mock.mock.calls) {
      expect((call[1]?.method ?? 'GET').toUpperCase()).toBe('GET');
    }
  });
});
