/**
 * Drift detection between the handwritten frontend contract and the backend.
 *
 * `openapi.snapshot.json` is the schema FastAPI publishes, committed verbatim.
 * Regenerate it from the repository root with the project's own Python:
 *
 *     python -c "import json; from review_api import create_app; \
 *       print(json.dumps(create_app().openapi(), indent=2, sort_keys=True))" \
 *       > frontend/src/api/openapi.snapshot.json
 *
 * `create_app()` attaches no lifespan, so building the schema opens no
 * database, starts no server, contacts no provider and reads no dataset --
 * the same property `tests/review_api/test_api_models.py` relies on. The only
 * normalisation applied is `sort_keys` and two-space indentation, which makes
 * the file diffable; no path, method, schema, property, required list, enum
 * value or status is altered or removed.
 *
 * The comparison below closes a loop that neither half could close alone.
 * Fixtures are checked against the TypeScript DTOs at compile time by
 * `satisfies`, so their runtime keys *are* the DTOs' fields. Comparing those
 * keys against the schema's properties therefore tests the handwritten types
 * against the real contract, without a code generator and without a second
 * copy of the field lists maintained by hand.
 *
 * Drift fails here in both directions: a field added to the backend and not
 * to `types.ts`, and a field invented in `types.ts` that the API does not
 * publish.
 *
 * Sprint 13 Phase E moved every review operation under an explicit
 * organization and review queue, required a session on all of them, and made
 * reviewer identity server-derived. The browser was deliberately not migrated
 * in that phase, so the published contract and the runtime client now
 * genuinely disagree. That disagreement is asserted explicitly in the
 * `deferred frontend migration` block at the bottom rather than smoothed over:
 * this file has to describe both sides truthfully, and "the UI is not migrated
 * yet" is a fact about one of them.
 *
 * Two of the operation assertions here were also stale before Phase E -- they
 * still listed the six Sprint 11 operations after Sprint 13 Phase D published
 * four authentication endpoints -- and are corrected to the surface the
 * backend actually publishes.
 */

import { describe, expect, it, vi } from 'vitest';

import { listReviewCases } from './client';
import snapshotRaw from './openapi.snapshot.json?raw';
import {
  HUMAN_REVIEW_DECISIONS,
  MATCH_DECISION_TYPES,
  REVIEW_EVENT_TYPES,
  REVIEW_STATUSES,
  SEMANTIC_FAILURE_CODES,
  SEMANTIC_SUGGESTION_TYPES,
} from './types';
import {
  advisorySuggestion,
  caseListResponse,
  caseCreatedEvent,
  deferredCaseDetail,
  healthResponse,
  pendingCaseDetail,
  pendingCaseSummary,
  resolveRequestWithReviewer,
  resolveResponse,
} from '../test/fixtures/sprint11';

interface SchemaObject {
  enum?: string[];
  properties?: Record<string, unknown>;
}

interface OpenApiSnapshot {
  openapi: string;
  info: { title: string; version: string };
  paths: Record<string, Record<string, unknown>>;
  components: { schemas: Record<string, SchemaObject> };
}

const schema = JSON.parse(snapshotRaw) as OpenApiSnapshot;

function schemaFor(name: string): SchemaObject {
  const entry = schema.components.schemas[name];
  if (entry === undefined) {
    throw new Error(`The published schema has no component named ${name}.`);
  }
  return entry;
}

function publishedEnum(name: string): string[] {
  const values = schemaFor(name).enum;
  if (values === undefined) {
    throw new Error(`Component ${name} is not an enum in the published schema.`);
  }
  return values;
}

function publishedProperties(name: string): string[] {
  const properties = schemaFor(name).properties;
  if (properties === undefined) {
    throw new Error(`Component ${name} publishes no properties.`);
  }
  return Object.keys(properties).sort();
}

/** The first element of a fixture array, as a value rather than `T | undefined`. */
function first<T>(items: readonly T[], label: string): T {
  const item = items[0];
  if (item === undefined) {
    throw new Error(`Fixture ${label} is empty.`);
  }
  return item;
}

/** The one prefix every review operation lives under after Sprint 13 Phase E. */
const TENANT_CASES =
  '/api/v1/organizations/{organization_id}/review-queues/{review_queue_id}/review-cases';

describe('published operations', () => {
  it('are exactly the ten the backend publishes', () => {
    const operations = Object.entries(schema.paths)
      .flatMap(([path, methods]) => Object.keys(methods).map((method) => `${method.toUpperCase()} ${path}`))
      .sort();

    expect(operations).toEqual(
      [
        'GET /health',
        'GET /api/v1/auth/session',
        'POST /api/v1/auth/login',
        'POST /api/v1/auth/logout',
        'POST /api/v1/auth/session/continue',
        `GET ${TENANT_CASES}`,
        `GET ${TENANT_CASES}/{review_case_id}`,
        `GET ${TENANT_CASES}/{review_case_id}/events`,
        `GET ${TENANT_CASES}/{review_case_id}/semantic-suggestions`,
        `POST ${TENANT_CASES}/{review_case_id}/resolve`,
      ].sort(),
    );
  });

  it('scope every review operation to an explicit organization and queue', () => {
    // The Phase E security boundary, stated positively so a future unscoped
    // route cannot be added without failing here. Sprint 13 removed the old
    // `/api/v1/review-cases` surface rather than authenticating it in place:
    // an authenticated route that still had to choose a queue would choose one
    // the caller never named.
    const reviewPaths = Object.keys(schema.paths).filter((path) => path.includes('review-cases'));

    expect(reviewPaths.length).toBeGreaterThan(0);
    for (const path of reviewPaths) {
      expect(path).toContain('{organization_id}');
      expect(path).toContain('{review_queue_id}');
    }
  });

  it('publish no unscoped review path at all', () => {
    const unscoped = Object.keys(schema.paths).filter((path) => path.startsWith('/api/v1/review'));

    expect(unscoped).toEqual([]);
  });

  it('contain exactly one review write, and it is the resolution endpoint', () => {
    // Narrowed to review data on purpose. The authentication writes change a
    // session and cannot reach a review case; what must never appear is a
    // second way to alter a human decision.
    const writes = Object.entries(schema.paths)
      .flatMap(([path, methods]) =>
        Object.keys(methods)
          .filter((method) => method.toLowerCase() !== 'get')
          .map((method) => `${method.toUpperCase()} ${path}`),
      )
      .filter((operation) => operation.includes('review-cases'));

    expect(writes).toEqual([`POST ${TENANT_CASES}/{review_case_id}/resolve`]);
  });

  it('publish no membership-management endpoint', () => {
    // Granting a membership is what makes a tenant's review evidence reachable
    // by a person. It stays an operator CLI action; an endpoint for it would
    // let whoever holds a session widen their own reach.
    const paths = Object.keys(schema.paths);

    expect(paths.filter((path) => /member|role|grant|invite/i.test(path))).toEqual([]);
  });

  it('publish no live semantic generation endpoint', () => {
    // Reading stored suggestions is the whole semantic surface. A write beside
    // it would put a provider call and a spend decision in an unauthenticated
    // request path, which is why the client has no function for one either.
    const writePaths = Object.entries(schema.paths)
      .filter(([, methods]) => Object.keys(methods).some((method) => method.toLowerCase() !== 'get'))
      .map(([path]) => path);

    expect(writePaths.filter((path) => /semantic|suggest|generate/i.test(path))).toEqual([]);
  });

  it('publish no trusted registration endpoint', () => {
    // Registering a workflow writes the very authorization context that a
    // MATCH is judged against. It stays an operator CLI action.
    const paths = Object.keys(schema.paths);

    expect(paths.filter((path) => /register|bootstrap|workflow|admin/i.test(path))).toEqual([]);
  });
});

describe('published enums', () => {
  it.each([
    ['ReviewStatus', REVIEW_STATUSES],
    ['MatchDecisionType', MATCH_DECISION_TYPES],
    ['HumanReviewDecision', HUMAN_REVIEW_DECISIONS],
    ['ReviewEventType', REVIEW_EVENT_TYPES],
    ['SemanticSuggestionType', SEMANTIC_SUGGESTION_TYPES],
    ['SemanticFailureCode', SEMANTIC_FAILURE_CODES],
  ])('%s matches the frontend union exactly', (name, declared) => {
    expect([...declared].sort()).toEqual([...publishedEnum(name)].sort());
  });

  it('spells the terminal match status MATCH, not MATCHED', () => {
    // The single correction that most changes client code: the status shares
    // its spelling with the decision, and `?status=MATCHED` would be a 422.
    const statuses = publishedEnum('ReviewStatus');

    expect(statuses).toContain('MATCH');
    expect(statuses).not.toContain('MATCHED');
  });

  it('accepts DEFER as a decision while DEFERRED is only a status', () => {
    expect(publishedEnum('HumanReviewDecision')).toContain('DEFER');
    expect(publishedEnum('HumanReviewDecision')).not.toContain('DEFERRED');
    expect(publishedEnum('ReviewStatus')).toContain('DEFERRED');
    expect(publishedEnum('ReviewStatus')).not.toContain('DEFER');
  });
});

describe('published DTO field sets', () => {
  it.each([
    ['HealthResponse', healthResponse],
    ['ReviewCaseSummary', pendingCaseSummary],
    ['ReviewCaseDetail', pendingCaseDetail],
    ['ReviewCaseListResponse', caseListResponse],
    ['ReviewEventRead', caseCreatedEvent],
    ['SemanticSuggestionRead', advisorySuggestion],
    ['ResolveReviewCaseResponse', resolveResponse],
    ['BlockingReasonRead', first(pendingCaseDetail.blocking_reasons, 'blocking_reasons')],
    ['SupportingEvidenceRead', first(pendingCaseDetail.supporting_evidence, 'supporting_evidence')],
    [
      'ConflictingEvidenceRead',
      first(pendingCaseDetail.conflicting_evidence, 'conflicting_evidence'),
    ],
    ['ReviewResolutionRead', deferredCaseDetail.resolution],
  ])('%s is modelled with exactly the published properties', (name, fixture) => {
    expect(Object.keys(fixture).sort()).toEqual(publishedProperties(name));
  });
});

describe('deliberate omissions', () => {
  it('never publishes a semantic explanation, anywhere in the document', () => {
    // Sprint 09 does not store the model's free-form rationale, because model
    // prose can repeat values out of untrusted customer records. A frontend
    // type for it would invent data the database never held.
    expect(snapshotRaw).not.toContain('explanation');
  });

  it('publishes no provider cost or token telemetry on a suggestion', () => {
    const properties = publishedProperties('SemanticSuggestionRead');

    for (const leak of ['cost', 'tokens', 'latency', 'prompt_hash', 'returned_model']) {
      expect(properties).not.toContain(leak);
    }
  });

  it('publishes no authorization or persistence material on an event', () => {
    const properties = publishedProperties('ReviewEventRead');

    for (const leak of ['audit_entry_payload', 'schema_version', 'review_case_id']) {
      expect(properties).not.toContain(leak);
    }
  });

  it('accepts only two fields on a resolution request', () => {
    // The smallness is the security property: every authorization input the
    // Sprint 08 authority reads is loaded server-side, and the request model
    // rejects unrecognised keys rather than ignoring them.
    expect(publishedProperties('ResolveReviewCaseRequest')).toEqual([
      'decision',
      'expected_version',
    ]);
  });

  it('no longer publishes a client-supplied reviewer identity', () => {
    // Sprint 13 Phase E made reviewer identity server-derived: the backend
    // takes it from the authenticated session and hands that to the domain, so
    // the durable audit row names the session that was actually used. A body
    // carrying `reviewer_id` is now a 422 rather than an accepted value.
    expect(publishedProperties('ResolveReviewCaseRequest')).not.toContain('reviewer_id');
    expect(JSON.stringify(schemaFor('ResolveReviewCaseRequest'))).not.toContain('reviewer_id');
  });
});

/**
 * The gap Sprint 13 Phase E deliberately left open, pinned so it cannot be
 * mistaken for agreement.
 *
 * Phase E closed the server-side tenant boundary and did not migrate the
 * browser: closing a security boundary and rewriting a UI are separate pieces
 * of work, and doing them together would mean judging a security change by
 * whether a screen still rendered. So the published contract and the runtime
 * client genuinely disagree right now, and this block asserts the disagreement
 * rather than papering over it in either direction.
 *
 * Each expectation below fails the moment the browser *is* migrated, which is
 * the point: the migration must delete this block, not quietly outgrow it.
 */
describe('deferred frontend migration', () => {
  it('still declares a reviewer_id the backend no longer accepts', () => {
    // `types.ts` is shared by the contract layer and by the un-migrated
    // resolution hook, so it cannot yet be narrowed to the published shape
    // without rewriting the reviewer-label control that feeds it. The
    // divergence is recorded here instead of being hidden.
    expect(Object.keys(resolveRequestWithReviewer)).toContain('reviewer_id');
    expect(publishedProperties('ResolveReviewCaseRequest')).not.toContain('reviewer_id');
  });

  it('still targets an unscoped review path the backend no longer serves', async () => {
    // Observed from the client's own request rather than asserted against a
    // copied constant, so this states what the browser would really send. The
    // URL it builds is now a 404: the UI is non-functional against a Phase E
    // backend, which is accepted and documented rather than worked around with
    // an invented default organization or queue.
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify(caseListResponse)),
    } as unknown as Response);

    try {
      await listReviewCases();
    } finally {
      vi.unstubAllGlobals();
    }

    const requested = String(fetchMock.mock.calls[0]?.[0]);
    expect(requested).toBe('/api/v1/review-cases');
    expect(Object.keys(schema.paths)).not.toContain(requested);
  });
});
