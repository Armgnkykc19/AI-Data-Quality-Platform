/**
 * A `fetch` stub, shared by the hook and integration tests.
 *
 * Tests stub `fetch` rather than the API client, so the URL the queue actually
 * requests -- including whether `status` was omitted and which `offset` was
 * sent -- is under test rather than assumed. The client's own construction
 * logic stays in the loop.
 *
 * `defer` is what makes request ordering testable: it hands back a response
 * the test resolves by hand, so two requests can be completed in the opposite
 * order to the one they were issued in.
 */

import { vi } from 'vitest';

export interface Deferred {
  /** Complete this request with a JSON body. */
  resolve: (body: unknown, status?: number) => void;
  /** Complete this request with a transport-level failure. */
  reject: (reason: unknown) => void;
}

/** A response stub carrying only the members the API client reads. */
export function jsonResponse(body: unknown, status = 200): Response {
  return textResponse(JSON.stringify(body), status);
}

export function textResponse(text: string, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(text),
  } as unknown as Response;
}

/** The Sprint 11 error envelope, for driving a specific public error code. */
export function errorResponse(code: string, status: number, message = 'Something failed.') {
  return jsonResponse({ error: { code, message, details: null } }, status);
}

export function createFetchStub() {
  const mock = vi.fn<typeof fetch>();
  vi.stubGlobal('fetch', mock);

  return {
    mock,

    /** Every request URL, in the order they were issued. */
    urls(): string[] {
      return mock.mock.calls.map((call) => String(call[0]));
    },

    lastUrl(): string {
      const urls = this.urls();
      const last = urls[urls.length - 1];
      if (last === undefined) {
        throw new Error('fetch was never called.');
      }
      return last;
    },

    callCount(): number {
      return mock.mock.calls.length;
    },

    /** Answer every request with this body until told otherwise. */
    alwaysRespond(body: unknown, status = 200): void {
      mock.mockResolvedValue(jsonResponse(body, status));
    },

    /** Answer only the next request with this body. */
    respondOnce(body: unknown, status = 200): void {
      mock.mockResolvedValueOnce(jsonResponse(body, status));
    },

    /** Answer only the next request with this already-built response. */
    respondOnceWith(response: Response): void {
      mock.mockResolvedValueOnce(response);
    },

    /** Fail the next request the way an unreachable server does. */
    failOnce(reason: unknown = new TypeError('Failed to fetch')): void {
      mock.mockRejectedValueOnce(reason);
    },

    failAlways(reason: unknown = new TypeError('Failed to fetch')): void {
      mock.mockRejectedValue(reason);
    },

    /**
     * Leave the next request hanging, and return the handle that completes it.
     *
     * The signal is watched so an aborted request rejects the way a real
     * `fetch` does, which is what lets a test exercise cancellation instead of
     * merely asserting that `abort()` was called.
     */
    defer(): Deferred {
      let settle: (value: Response) => void = () => undefined;
      let fail: (reason: unknown) => void = () => undefined;

      mock.mockImplementationOnce((_input, init) => {
        return new Promise<Response>((resolvePromise, rejectPromise) => {
          settle = resolvePromise;
          fail = rejectPromise;
          init?.signal?.addEventListener('abort', () => {
            const abortError = new Error('The operation was aborted.');
            abortError.name = 'AbortError';
            rejectPromise(abortError);
          });
        });
      });

      return {
        resolve: (body: unknown, status = 200) => {
          settle(jsonResponse(body, status));
        },
        reject: (reason: unknown) => {
          fail(reason);
        },
      };
    },
  };
}

export type FetchStub = ReturnType<typeof createFetchStub>;
