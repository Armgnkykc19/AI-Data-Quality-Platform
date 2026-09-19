/**
 * Drift detection between the handwritten frontend contract and Sprint 11.
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
 */

import { describe, expect, it } from 'vitest';

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

describe('published operations', () => {
  it('are exactly the six the client implements', () => {
    const operations = Object.entries(schema.paths)
      .flatMap(([path, methods]) => Object.keys(methods).map((method) => `${method.toUpperCase()} ${path}`))
      .sort();

    expect(operations).toEqual([
      'GET /api/v1/review-cases',
      'GET /api/v1/review-cases/{review_case_id}',
      'GET /api/v1/review-cases/{review_case_id}/events',
      'GET /api/v1/review-cases/{review_case_id}/semantic-suggestions',
      'GET /health',
      'POST /api/v1/review-cases/{review_case_id}/resolve',
    ]);
  });

  it('contain exactly one write, and it is the resolution endpoint', () => {
    const writes = Object.entries(schema.paths).flatMap(([path, methods]) =>
      Object.keys(methods)
        .filter((method) => method.toLowerCase() !== 'get')
        .map((method) => `${method.toUpperCase()} ${path}`),
    );

    expect(writes).toEqual(['POST /api/v1/review-cases/{review_case_id}/resolve']);
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
    ['ResolveReviewCaseRequest', resolveRequestWithReviewer],
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

  it('accepts only three fields on a resolution request', () => {
    // The smallness is the security property: every authorization input the
    // Sprint 08 authority reads is loaded server-side, and the request model
    // rejects unrecognised keys rather than ignoring them.
    expect(publishedProperties('ResolveReviewCaseRequest')).toEqual([
      'decision',
      'expected_version',
      'reviewer_id',
    ]);
  });
});
