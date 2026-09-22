/**
 * Build, local runtime, and test configuration for the reviewer UI.
 *
 * The proxy below is the whole reason this file matters to the frontend
 * architecture. The review API installs no CORS middleware, deliberately:
 * authenticated state-changing requests require an exact allowed Origin, and
 * there is no origin this local UI could safely advertise as a cross-origin
 * caller. `POST .../resolve` is an authoritative write.
 *
 * A browser talking straight to 127.0.0.1:8000 from a page served on :5173
 * would be cross-origin and blocked. Rather than weaken the API, the dev and
 * preview servers proxy `/api` and `/health` to it. The browser therefore only
 * ever issues same-origin requests, no preflight happens, and the API keeps
 * its no-CORS boundary untouched.
 *
 * That is also why every request path in `src/api/client.ts` is relative and
 * why there is no `VITE_API_URL`: an absolute URL in browser source would
 * bypass the proxy and reintroduce the cross-origin problem this solves.
 *
 * This UI still does not implement login or tenant selection. The API does.
 */
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

/** The Sprint 11 runner's default bind address. `python -m review_api` refuses
 *  any non-loopback host, so a non-loopback target here could never be right. */
const API_ORIGIN = 'http://127.0.0.1:8000';

/** Loopback, never 0.0.0.0. Vite's proxy would otherwise republish the
 *  decision endpoint to the local network, which is exactly what
 *  `review_api.__main__.is_loopback` exists to prevent on the API side. */
const LOOPBACK_HOST = '127.0.0.1';

const DEV_PORT = 5173;
const PREVIEW_PORT = 4173;

/** `changeOrigin: false` keeps the forwarded Host header as the loopback
 *  origin the browser used. Nothing in the API reads it; rewriting it would
 *  only hide which origin the request actually came from. */
const apiProxy = {
  '/api': { target: API_ORIGIN, changeOrigin: false },
  '/health': { target: API_ORIGIN, changeOrigin: false },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: LOOPBACK_HOST,
    port: DEV_PORT,
    strictPort: true,
    proxy: apiProxy,
  },
  // Preview serves the production build, and it gets the same two constraints:
  // loopback only, and the same proxy, so a built UI is exercised against the
  // real API without the API learning about a second origin.
  preview: {
    host: LOOPBACK_HOST,
    port: PREVIEW_PORT,
    strictPort: true,
    proxy: apiProxy,
  },
  test: {
    environment: 'jsdom',
    // No injected globals: every test imports `describe`/`it`/`expect` from
    // vitest explicitly, so a test file's dependencies are visible in it.
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
});
