import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { QueuePanel } from './QueuePanel';
import type { ApiFailure } from '../../api/errors';
import type { ReviewCaseListResponse } from '../../api/types';
import {
  DEFERRED_CASE_ID,
  PENDING_CASE_ID,
  caseListResponse,
  emptyCaseListResponse,
} from '../../test/fixtures/sprint11';

type PanelProps = Parameters<typeof QueuePanel>[0];

function renderPanel(overrides: Partial<PanelProps> = {}) {
  const props: PanelProps = {
    data: caseListResponse,
    isInitialLoading: false,
    isUpdating: false,
    failure: null,
    statusFilter: 'PENDING',
    selectedReviewCaseId: null,
    onStatusFilterChange: vi.fn(),
    onOffsetChange: vi.fn(),
    onSelect: vi.fn(),
    onRefresh: vi.fn(),
    ...overrides,
  };
  render(<QueuePanel {...props} />);
  return props;
}

/** The queue rows, as the accessible tree exposes them. */
function rows() {
  return within(screen.getByRole('list')).getAllByRole('button');
}

describe('loading', () => {
  it('announces the first load and shows no rows yet', () => {
    renderPanel({ data: null, isInitialLoading: true });

    expect(screen.getByRole('status')).toHaveTextContent('Loading review queue');
    expect(screen.queryByRole('list')).toBeNull();
  });

  it('keeps the loaded rows visible while an update is in flight', () => {
    renderPanel({ isUpdating: true });

    expect(screen.getByRole('status')).toHaveTextContent('Updating review queue');
    expect(rows()).toHaveLength(2);
  });

  it('says nothing in the live region when the queue is settled', () => {
    renderPanel();

    expect(screen.getByRole('status')).toHaveTextContent('');
  });
});

describe('rows', () => {
  it('shows the record pair, status, score and update time from the summary', () => {
    renderPanel();
    const [first] = rows();

    expect(first).toHaveTextContent('REC-A-0001');
    expect(first).toHaveTextContent('REC-B-0001');
    expect(first).toHaveTextContent('Pending');
    expect(first).toHaveTextContent('0.81');
    expect(first).toHaveTextContent(PENDING_CASE_ID);
  });

  it('shows no field the list endpoint does not publish', () => {
    // ReviewCaseSummary carries no evidence, thresholds, machine reason or
    // resolution, and none of them may be inferred from what it does carry.
    renderPanel();
    const list = screen.getByRole('list');

    for (const absent of ['threshold', 'evidence', 'blocking', 'resolution', 'suggest']) {
      expect(list.textContent?.toLowerCase()).not.toContain(absent);
    }
  });

  it('renders the status as text rather than colour alone', () => {
    renderPanel();

    expect(within(rows()[0] as HTMLElement).getByText('Pending')).toBeInTheDocument();
    expect(within(rows()[1] as HTMLElement).getByText('Deferred')).toBeInTheDocument();
  });

  it('keeps the exact UTC instant reachable behind the relative time', () => {
    renderPanel();

    expect(screen.getByTitle('2026-09-18 09:00:00 UTC')).toBeInTheDocument();
  });
});

describe('selection', () => {
  it('reports the activated case id', async () => {
    const user = userEvent.setup();
    const props = renderPanel();

    await user.click(rows()[1] as HTMLElement);

    expect(props.onSelect).toHaveBeenCalledWith(DEFERRED_CASE_ID);
  });

  it('is operable from the keyboard', async () => {
    const user = userEvent.setup();
    const props = renderPanel();

    await user.tab();
    while (document.activeElement !== rows()[0]) {
      await user.tab();
    }
    await user.keyboard('{Enter}');

    expect(props.onSelect).toHaveBeenCalledWith(PENDING_CASE_ID);
  });

  it('marks the selected row semantically and in words', () => {
    renderPanel({ selectedReviewCaseId: PENDING_CASE_ID });
    const [first, second] = rows();

    expect(first).toHaveAttribute('aria-current', 'true');
    expect(second).toHaveAttribute('aria-current', 'false');
    expect(first).toHaveTextContent('Selected');
  });

  it('explains a selection that is not in the current view without claiming it is gone', () => {
    renderPanel({ selectedReviewCaseId: 'RC-not-on-this-page' });

    expect(screen.getByText(/not in the current queue view/i)).toBeInTheDocument();
    expect(screen.queryByText(/deleted|does not exist|not found/i)).toBeNull();
  });

  it('says nothing when the selected case is on screen', () => {
    renderPanel({ selectedReviewCaseId: PENDING_CASE_ID });

    expect(screen.queryByText(/not in the current queue view/i)).toBeNull();
  });
});

describe('filters', () => {
  it('offers All plus every published status, spelled for a reader', () => {
    renderPanel();
    const group = screen.getByRole('group', { name: 'Filter by status' });

    expect(within(group).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'All',
      'Pending',
      'Match',
      'No match',
      'Deferred',
    ]);
  });

  it('marks the active filter with pressed state', () => {
    renderPanel({ statusFilter: 'PENDING' });

    expect(screen.getByRole('button', { name: 'Pending' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('reports a chosen status', async () => {
    const user = userEvent.setup();
    const props = renderPanel();

    await user.click(screen.getByRole('button', { name: 'No match' }));

    expect(props.onStatusFilterChange).toHaveBeenCalledWith('NO_MATCH');
  });

  it('reports All as null, never as a status token', async () => {
    const user = userEvent.setup();
    const props = renderPanel();

    await user.click(screen.getByRole('button', { name: 'All' }));

    expect(props.onStatusFilterChange).toHaveBeenCalledWith(null);
  });
});

describe('empty states', () => {
  it('distinguishes an empty queue from an empty filter', () => {
    renderPanel({ data: emptyCaseListResponse, statusFilter: null });

    expect(screen.getByText('No review cases in this queue.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Clear filter' })).toBeNull();
  });

  it('names the filtered status and offers to clear it', async () => {
    const user = userEvent.setup();
    const props = renderPanel({ data: emptyCaseListResponse, statusFilter: 'DEFERRED' });

    expect(screen.getByText('No cases with status Deferred.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear filter' }));

    expect(props.onStatusFilterChange).toHaveBeenCalledWith(null);
  });

  it('does not claim the queue is unregistered', () => {
    // A valid but never-registered database answers this endpoint with an
    // ordinary empty list. REVIEW_QUEUE_NOT_READY surfaces on resolve, not
    // here, so asserting an operator action is required would be a guess.
    renderPanel({ data: emptyCaseListResponse, statusFilter: null });

    expect(screen.queryByText(/register|bootstrap|operator|not ready/i)).toBeNull();
  });

  it('hides pagination when nothing matches', () => {
    renderPanel({ data: emptyCaseListResponse, statusFilter: null });

    expect(screen.queryByRole('navigation', { name: 'Queue pages' })).toBeNull();
  });
});

describe('refresh', () => {
  it('runs the current query again', async () => {
    const user = userEvent.setup();
    const props = renderPanel();

    await user.click(screen.getByRole('button', { name: 'Refresh queue' }));

    expect(props.onRefresh).toHaveBeenCalledTimes(1);
  });

  it('is disabled while a request is already in flight', () => {
    renderPanel({ isUpdating: true });

    expect(screen.getByRole('button', { name: 'Refresh queue' })).toBeDisabled();
  });
});

describe('failures', () => {
  const unavailable: ApiFailure = {
    kind: 'api',
    code: 'REVIEW_STORAGE_UNAVAILABLE',
    httpStatus: 503,
    message: 'The review queue is currently unavailable.',
    details: null,
  };
  const corrupt: ApiFailure = {
    kind: 'api',
    code: 'REVIEW_STORAGE_CORRUPT',
    httpStatus: 500,
    message: 'The review queue returned inconsistent stored state.',
    details: null,
  };

  it('announces a storage outage and offers a manual retry', async () => {
    const user = userEvent.setup();
    const props = renderPanel({ failure: unavailable });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('The review queue is unavailable.');
    await user.click(within(alert).getByRole('button', { name: 'Retry' }));

    expect(props.onRefresh).toHaveBeenCalledTimes(1);
  });

  it('offers no retry for corrupt stored state', () => {
    // Not a transient condition: the same rows would be read again.
    renderPanel({ failure: corrupt });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/inconsistent stored state/i);
    expect(within(alert).queryByRole('button')).toBeNull();
  });

  it('names an unreachable API distinctly from a server failure', () => {
    renderPanel({ failure: { kind: 'network' } });

    expect(screen.getByRole('alert')).toHaveTextContent('Cannot reach the local review API.');
  });

  it('reports a non-contract response as unexpected', () => {
    renderPanel({ failure: { kind: 'malformed', httpStatus: 502 } });

    expect(screen.getByRole('alert')).toHaveTextContent(/unexpected response/i);
  });

  it('keeps already-loaded rows beside the failure', () => {
    renderPanel({ failure: { kind: 'network' } });

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(rows()).toHaveLength(2);
  });

  it('exposes no response body, status line or internal detail', () => {
    renderPanel({ failure: { kind: 'malformed', httpStatus: 502 } });

    const alert = screen.getByRole('alert');
    for (const leak of ['502', 'sqlite', 'traceback', '{', 'Error:']) {
      expect(alert.textContent).not.toContain(leak);
    }
  });
});

describe('pagination', () => {
  const page = (overrides: Partial<ReviewCaseListResponse>): ReviewCaseListResponse => ({
    ...caseListResponse,
    ...overrides,
  });

  it('reports the range the server described', () => {
    renderPanel({ data: page({ count: 2, total: 137, limit: 50, offset: 0 }) });

    expect(screen.getByText('Showing 1–2 of 137')).toBeInTheDocument();
  });

  it('disables Previous on the first page', () => {
    renderPanel({ data: page({ offset: 0, total: 137 }) });

    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled();
  });

  it('disables Next on the last page', () => {
    renderPanel({ data: page({ count: 2, total: 52, limit: 50, offset: 50 }) });

    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled();
  });

  it('steps forward by the limit the server echoed', async () => {
    const user = userEvent.setup();
    const props = renderPanel({ data: page({ count: 2, total: 137, limit: 25, offset: 25 }) });

    await user.click(screen.getByRole('button', { name: 'Next page' }));

    expect(props.onOffsetChange).toHaveBeenCalledWith(50);
  });

  it('never steps back below zero', async () => {
    const user = userEvent.setup();
    const props = renderPanel({ data: page({ count: 2, total: 137, limit: 50, offset: 20 }) });

    await user.click(screen.getByRole('button', { name: 'Previous page' }));

    expect(props.onOffsetChange).toHaveBeenCalledWith(0);
  });
});
