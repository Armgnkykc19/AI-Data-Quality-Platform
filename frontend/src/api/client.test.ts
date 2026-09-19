import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  getHealth,
  getReviewCase,
  getReviewEvents,
  getSemanticSuggestions,
  listReviewCases,
  resolveReviewCase,
} from './client';
import { isApiRequestError, type ApiFailure } from './errors';
import {
  PENDING_CASE_ID,
  advisorySuggestion,
  caseListResponse,
  eventHistory,
  healthResponse,
  pendingCaseDetail,
  resolveRequestWithReviewer,
  resolveRequestWithoutReviewer,
  resolveResponse,
} from '../test/fixtures/sprint11';

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** A response stub: only the three members the client actually touches. */
function respond(status: number, bodyText: string): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(bodyText),
  } as unknown as Response;
}

function jsonResponse(status: number, body: unknown): Response {
  return respond(status, JSON.stringify(body));
}

function okWith(body: unknown): void {
  fetchMock.mockResolvedValue(jsonResponse(200, body));
}

/** The URL the single recorded call was made to. */
function requestedUrl(): string {
  const call = fetchMock.mock.calls[0];
  if (call === undefined) {
    throw new Error('fetch was never called.');
  }
  return String(call[0]);
}

function requestedInit(): RequestInit {
  const call = fetchMock.mock.calls[0];
  if (call === undefined) {
    throw new Error('fetch was never called.');
  }
  return call[1] ?? {};
}

function headerValue(name: string): string | undefined {
  const headers = requestedInit().headers;
  if (headers === undefined) {
    return undefined;
  }
  return (headers as Record<string, string>)[name];
}

/** The failure a rejected client call carried. */
async function failureOf(call: Promise<unknown>): Promise<ApiFailure> {
  try {
    await call;
  } catch (error) {
    if (!isApiRequestError(error)) {
      throw error;
    }
    return error.failure;
  }
  throw new Error('The call resolved where a failure was expected.');
}

// ---------------------------------------------------------------------------

describe('request targets', () => {
  it('addresses every operation with a relative path', async () => {
    // Absolute URLs would make each request cross-origin against an API that
    // installs no CORS middleware, and would bypass the Vite proxy that keeps
    // that boundary intact.
    const calls: Array<[string, () => Promise<unknown>]> = [
      ['health', () => getHealth()],
      ['list', () => listReviewCases()],
      ['detail', () => getReviewCase(PENDING_CASE_ID)],
      ['events', () => getReviewEvents(PENDING_CASE_ID)],
      ['suggestions', () => getSemanticSuggestions(PENDING_CASE_ID)],
      ['resolve', () => resolveReviewCase(PENDING_CASE_ID, resolveRequestWithReviewer)],
    ];

    for (const [label, invoke] of calls) {
      fetchMock.mockReset();
      fetchMock.mockResolvedValue(jsonResponse(200, { placeholder: true }));
      await invoke().catch(() => undefined);

      const url = requestedUrl();
      expect(url.startsWith('/'), `${label} used ${url}`).toBe(true);
      expect(url).not.toMatch(/^https?:/);
      expect(url).not.toContain('127.0.0.1');
      expect(url).not.toContain('localhost');
    }
  });

  it('reads health from /health', async () => {
    okWith(healthResponse);

    await expect(getHealth()).resolves.toEqual(healthResponse);
    expect(requestedUrl()).toBe('/health');
    expect(requestedInit().method).toBe('GET');
    expect(headerValue('Accept')).toBe('application/json');
  });

  it('reads one case from its own path', async () => {
    okWith(pendingCaseDetail);

    await expect(getReviewCase(PENDING_CASE_ID)).resolves.toEqual(pendingCaseDetail);
    expect(requestedUrl()).toBe(`/api/v1/review-cases/${PENDING_CASE_ID}`);
  });

  it('reads history and suggestions from their sub-paths', async () => {
    okWith(eventHistory);
    await expect(getReviewEvents(PENDING_CASE_ID)).resolves.toEqual(eventHistory);
    expect(requestedUrl()).toBe(`/api/v1/review-cases/${PENDING_CASE_ID}/events`);

    fetchMock.mockReset();
    okWith([advisorySuggestion]);
    await expect(getSemanticSuggestions(PENDING_CASE_ID)).resolves.toEqual([advisorySuggestion]);
    expect(requestedUrl()).toBe(`/api/v1/review-cases/${PENDING_CASE_ID}/semantic-suggestions`);
  });

  it('encodes a path identifier instead of concatenating it', async () => {
    // Sprint 08 ids need no escaping, but an unexpected `/` or `?` in a value
    // read from application state would otherwise become a different request.
    okWith(pendingCaseDetail);

    await getReviewCase('RC-1/../2?x=1');

    expect(requestedUrl()).toBe('/api/v1/review-cases/RC-1%2F..%2F2%3Fx%3D1');
  });
});

describe('list query construction', () => {
  it('sends no query string when no parameter is given', async () => {
    // `?status=` is a 422: the backend matches the status enum exactly and
    // does not read an empty value as "no filter".
    okWith(caseListResponse);

    await listReviewCases();

    expect(requestedUrl()).toBe('/api/v1/review-cases');
  });

  it('sends only the parameters that were supplied', async () => {
    okWith(caseListResponse);

    await listReviewCases({ status: 'PENDING' });

    expect(requestedUrl()).toBe('/api/v1/review-cases?status=PENDING');
  });

  it('sends limit and offset when paging', async () => {
    okWith(caseListResponse);

    await listReviewCases({ status: 'DEFERRED', limit: 25, offset: 50 });

    expect(requestedUrl()).toBe('/api/v1/review-cases?status=DEFERRED&limit=25&offset=50');
  });

  it('sends an explicit zero offset rather than dropping it', async () => {
    okWith(caseListResponse);

    await listReviewCases({ offset: 0 });

    expect(requestedUrl()).toBe('/api/v1/review-cases?offset=0');
  });
});

describe('resolution requests', () => {
  it('posts JSON with the declared content type', async () => {
    okWith(resolveResponse);

    await resolveReviewCase(PENDING_CASE_ID, resolveRequestWithReviewer);

    expect(requestedUrl()).toBe(`/api/v1/review-cases/${PENDING_CASE_ID}/resolve`);
    expect(requestedInit().method).toBe('POST');
    expect(headerValue('Content-Type')).toBe('application/json');
  });

  it('transmits expected_version exactly as given, as a number', async () => {
    // The version the reviewer was shown. Never defaulted, never incremented,
    // and never coerced to a string -- the backend rejects `"7"` outright.
    okWith(resolveResponse);

    await resolveReviewCase(PENDING_CASE_ID, { decision: 'MATCH', expected_version: 7 });

    expect(JSON.parse(String(requestedInit().body))).toEqual({
      decision: 'MATCH',
      expected_version: 7,
    });
  });

  it('preserves an omitted reviewer_id as omitted', async () => {
    okWith(resolveResponse);

    await resolveReviewCase(PENDING_CASE_ID, resolveRequestWithoutReviewer);

    const body = JSON.parse(String(requestedInit().body)) as Record<string, unknown>;
    expect('reviewer_id' in body).toBe(false);
  });

  it('preserves an explicit null reviewer_id', async () => {
    okWith(resolveResponse);

    await resolveReviewCase(PENDING_CASE_ID, {
      decision: 'DEFER',
      expected_version: 1,
      reviewer_id: null,
    });

    expect(JSON.parse(String(requestedInit().body))).toEqual({
      decision: 'DEFER',
      expected_version: 1,
      reviewer_id: null,
    });
  });

  it('sends a reviewer_id verbatim, including an empty string', async () => {
    // The domain writes reviewer_id into an append-only audit row unchanged,
    // so normalising it here would alter recorded audit identity.
    okWith(resolveResponse);

    await resolveReviewCase(PENDING_CASE_ID, {
      decision: 'NO_MATCH',
      expected_version: 2,
      reviewer_id: '  Spaced Name  ',
    });

    const body = JSON.parse(String(requestedInit().body)) as Record<string, unknown>;
    expect(body['reviewer_id']).toBe('  Spaced Name  ');
  });
});

describe('cancellation', () => {
  it('forwards the caller’s signal to fetch', async () => {
    const controller = new AbortController();
    okWith(pendingCaseDetail);

    await getReviewCase(PENDING_CASE_ID, { signal: controller.signal });

    expect(requestedInit().signal).toBe(controller.signal);
  });

  it('omits the signal property entirely when none is given', async () => {
    okWith(pendingCaseDetail);

    await getReviewCase(PENDING_CASE_ID);

    expect('signal' in requestedInit()).toBe(false);
  });

  it('reports an abort as aborted, not as a failure to show a reviewer', async () => {
    const controller = new AbortController();
    controller.abort();
    const abortError = new Error('The operation was aborted.');
    abortError.name = 'AbortError';
    fetchMock.mockRejectedValue(abortError);

    const failure = await failureOf(getReviewCase(PENDING_CASE_ID, { signal: controller.signal }));

    expect(failure).toEqual({ kind: 'aborted' });
  });
});

describe('failure decoding', () => {
  it('decodes a contract error body into its public code', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, {
        error: {
          code: 'REVIEW_CASE_VERSION_CONFLICT',
          message: 'The review case changed after it was loaded.',
          details: { expected_version: 1 },
        },
      }),
    );

    const failure = await failureOf(
      resolveReviewCase(PENDING_CASE_ID, { decision: 'MATCH', expected_version: 1 }),
    );

    expect(failure).toEqual({
      kind: 'api',
      code: 'REVIEW_CASE_VERSION_CONFLICT',
      httpStatus: 409,
      message: 'The review case changed after it was loaded.',
      details: { kind: 'versionConflict', expectedVersion: 1 },
    });
  });

  it('treats a non-contract error body as malformed, not as an api failure', async () => {
    fetchMock.mockResolvedValue(respond(502, '<html>Bad Gateway</html>'));

    const failure = await failureOf(listReviewCases());

    expect(failure).toEqual({ kind: 'malformed', httpStatus: 502 });
  });

  it('reports an unreachable API as a network failure', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

    expect(await failureOf(getHealth())).toEqual({ kind: 'network' });
  });

  it('refuses an unparseable success body instead of resolving undefined', async () => {
    // A malformed 200 must never flow into the UI as an empty case.
    fetchMock.mockResolvedValue(respond(200, 'not json at all'));

    expect(await failureOf(getReviewCase(PENDING_CASE_ID))).toEqual({
      kind: 'malformed',
      httpStatus: 200,
    });
  });

  it('refuses an empty success body', async () => {
    fetchMock.mockResolvedValue(respond(200, ''));

    expect(await failureOf(getHealth())).toEqual({ kind: 'malformed', httpStatus: 200 });
  });

  it('refuses a success body of the wrong top-level shape', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { items: [] }));

    expect(await failureOf(getReviewEvents(PENDING_CASE_ID))).toEqual({
      kind: 'malformed',
      httpStatus: 200,
    });
  });
});

describe('the no-retry guarantee', () => {
  // One call from a caller is one request, whatever comes back. A retry would
  // re-run Sprint 08 authorization against a queue state the reviewer never
  // saw, which is precisely what optimistic concurrency exists to prevent.
  // A user-triggered Retry button in a later phase is a second, deliberate call.

  it.each([
    ['a 409 conflict', () => fetchMock.mockResolvedValue(jsonResponse(409, {}))],
    ['a 422 refusal', () => fetchMock.mockResolvedValue(jsonResponse(422, {}))],
    ['a 503 outage', () => fetchMock.mockResolvedValue(jsonResponse(503, {}))],
    ['a 500 error', () => fetchMock.mockResolvedValue(jsonResponse(500, {}))],
    ['a network failure', () => fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))],
  ])('makes exactly one resolve attempt after %s', async (_label, arrange) => {
    arrange();

    await failureOf(resolveReviewCase(PENDING_CASE_ID, { decision: 'MATCH', expected_version: 1 }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('makes exactly one attempt for every read as well', async () => {
    const reads: Array<() => Promise<unknown>> = [
      () => getHealth(),
      () => listReviewCases({ status: 'PENDING' }),
      () => getReviewCase(PENDING_CASE_ID),
      () => getReviewEvents(PENDING_CASE_ID),
      () => getSemanticSuggestions(PENDING_CASE_ID),
    ];

    for (const read of reads) {
      fetchMock.mockReset();
      fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
      await failureOf(read());
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
  });
});
