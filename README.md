# AI Data Quality Platform

AI Data Quality Platform is an AI-assisted data quality and transformation system focused initially on CRM migration and customer data cleanup.

The platform is designed to transform inconsistent Excel and CSV customer data into a canonical, traceable, and import-ready dataset through deterministic validation, normalization, entity resolution, confidence-based decision making, and human review.

## Current Development Stage

The project is currently in its evaluation-first engineering phase.

Before building the production API, frontend, automation workflows, or CRM integrations, the project establishes a reproducible benchmark infrastructure for measuring:

- entity resolution quality,
- candidate recall,
- automatic merge precision,
- false merge rate,
- schema mapping accuracy,
- normalization accuracy,
- review routing quality,
- hard-gate acceptance criteria.

## Engineering Principles

- Evaluation before productization
- Deterministic rules before probabilistic AI
- No irreversible LLM-only merge decisions
- Source data preservation
- Explainable confidence and evidence
- Human review for ambiguous decisions
- Reproducible benchmarks
- Regression-tested development

## Sprint 01

Sprint 01 establishes the initial engineering and evaluation foundation.

Current capabilities include:

- centralized evaluation configuration,
- reusable classification metrics,
- configurable hard gates,
- PASS / FAIL evaluation decisions,
- JSON and Markdown report generation,
- CLI-based evaluation execution,
- deterministic process exit codes,
- automated unit tests,
- Ruff static analysis,
- GitHub Actions continuous integration.

Run the evaluation harness (fixture smoke — not product quality):

```bash
python -m evaluation.run
```

The harness runs in `FIXTURE_SMOKE` mode until real engine metrics exist. Hard-gate PASS validates infrastructure wiring only; product quality evaluation is `NOT_YET_AVAILABLE`.

## Sprint 02

Sprint 02 adds the golden dataset and controlled corruption engine.

Current capabilities include:

- deterministic canonical clean-base generation,
- modular corruption families with auditable history,
- Source A / B / C variants,
- hard positives and hard negatives,
- malformed input fixtures,
- isolated ground truth and person-level splits,
- dataset manifest with SHA-256 hashes,
- dataset build/validate CLI commands.

Build and validate the golden dataset:

```bash
python scripts/build_golden_dataset.py --config configs/dataset.yaml
python scripts/validate_dataset.py --dataset datasets/golden/v0.1.0
python -m evaluation.run --dataset datasets/golden/v0.1.0
```

Run the test suite:

```bash
pytest
```

Run static analysis:

```bash
ruff check .
```

## Project Status

Early development — Sprint 08 (Human Review & Ambiguity Resolution) is complete, Sprint 09 adds optional advisory semantic review, Sprint 10 persists the review queue, Sprint 11 serves it over a localhost REST API, Sprint 12 adds the local Reviewer UI, and Sprint 13 is adding multi-tenancy: the persistence foundation, authentication, and now tenant-scoped authorization on every review endpoint. The LLM is never an authority, and human review stays backend-authoritative.

## Sprint 13 (in progress)

Sprint 13 adds authentication, organizations and tenant isolation. **Identity and authorization both work; the browser does not yet.** Every review endpoint now requires a session and names its organization and review queue explicitly in the URL, and access is decided from current organization membership. The Sprint 12 UI still speaks the old unscoped API and is therefore temporarily incompatible — the security boundary was closed first on purpose, and frontend integration follows.

What does exist is the ownership graph everything above it will hang from:

```
Organization
  └── ReviewQueue
        ├── ReviewWorkflowContext   (exactly one per queue)
        └── ReviewCase
              ├── ReviewCaseEvent
              └── SemanticSuggestion

User ──< OrganizationMembership >── Organization      (role: REVIEWER | VIEWER)
```

The review queue rather than the organization is the direct owner of review data, because the queue was already the boundary of the stored workflow context, of Sprint 08 MATCH authorization, of review-case identity and of registration idempotence. An organization may own several queues; each carries its own authorization graph, and a decision recorded in one never constrains another.

**A review case id is unique within a queue, not globally.** `stable_review_case_id` derives it from the reviewed record pair, and record identifiers come from customer data, so two organizations produce the same `RC-...` routinely. Review tables are keyed by `(review_queue_id, review_case_id)`, and child tables reference that pair through composite foreign keys — so an event or a suggestion cannot point at a case in another queue.

Identity lives in the `identity/` package: `User`, `Organization`, `OrganizationMembership` and `MembershipRole`, framework-free and unknown to `human_review`.

### Authentication core

Passwords are verified with **Argon2id** (`argon2-cffi`), and the encoded hash format belongs to the library — there are no salt or parameter columns of our own. Credentials live in their own table keyed by `user_id`, never on `User`, so reading a display name never carries a verifier. A user is created with no way to log in until an operator sets a password; there is no default credential.

Sessions are **opaque server-side tokens**, not JWTs. A token is 256 random bits and carries nothing — no user id, no organization, no role, no claims — so every fact about a session is read from its row at the moment it is used. The database stores only a SHA-256 digest of the token, so a stolen copy of the file yields no usable credential. Revocation therefore takes effect immediately, and because a session holds no role, removing someone's access does too.

The timing policy is fixed server-side: **60 minutes idle**, **8 hours absolute**, with a 55-minute threshold the frontend will later use to warn. Activity extends the idle window and never the absolute one — that bound exists precisely so a stolen token cannot be kept alive by using it.

### Signing in

Authentication is now reachable over HTTP. Four endpoints, and nothing else:

```
POST /api/v1/auth/login             credentials in, session cookie out
GET  /api/v1/auth/session           who is signed in, and how long is left
POST /api/v1/auth/session/continue  the explicit "I am still here"
POST /api/v1/auth/logout            revoke the session, clear the cookie
```

The session token travels in **one opaque cookie** and appears in no response body. It is `HttpOnly` so script cannot read it, `SameSite=Strict` so a cross-site request does not carry it, scoped to `Path=/` with no `Domain`, and has no `Max-Age` — it is gone when the browser closes, whatever the server-side session still allows.

**Every authentication failure answers the same `401 UNAUTHENTICATED`.** An unknown address, a wrong password, a disabled account, a missing credential, a revoked session, an idle-expired session, an absolutely-expired one, and a session whose owner was just disabled are one public answer. Telling them apart would say whether an account exists, whether it is worth pursuing, or whether an operator had noticed something.

Unsafe requests — login, logout, continue — are refused unless they carry an `Origin` the configuration lists, matched whole. A **missing** `Origin` is refused too: every browser sends it on a POST, so its absence means the caller is not the browser this API is built for. That is the CSRF control alongside `SameSite`; no CORS middleware exists and none is needed, because the browser reaches the API same-origin through the Vite proxy.

Any successful authenticated request counts as activity and pushes the idle window forward, so a reviewer working steadily is never signed out for failing to press a button. Nothing extends the eight-hour absolute bound.

### Creating a login

```bash
python scripts/manage_human_review.py create-user \
    --email reviewer@example.com --display-name "Reviewer One"
Password:
Confirm password:
```

There is no `--password` and there will not be: an argument is written to shell history and visible in the process list. The account is created together with its password in one transaction, so a failure cannot leave a user who exists but cannot sign in. It gets no organization, no membership and no role — granting access is a separate decision, made with `add-membership` below. A user with no membership signs in successfully and finds nothing to read: every review URL answers the same `404` a stranger would get.

Cookie and origin policy lives in `configs/auth_http.yaml`. `session_cookie_secure` defaults to **true**; the committed file sets it to false for the documented local HTTP runtime, and the loader refuses that setting unless every allowed origin is loopback.

Still not implemented, and deliberately: frontend login, "remember me", public registration, password reset, email verification, and a double-submit CSRF token.

### Tenant authorization

Every review operation names its organization and its review queue in the path, and requires a session:

```
GET  /api/v1/organizations/{organization_id}/review-queues/{review_queue_id}/review-cases
GET  .../review-cases/{review_case_id}
GET  .../review-cases/{review_case_id}/events
GET  .../review-cases/{review_case_id}/semantic-suggestions
POST .../review-cases/{review_case_id}/resolve
```

The old unscoped `/api/v1/review-cases` paths are **gone**, not authenticated in place. An authenticated route that still had to choose a queue would choose one the caller never named, which is the failure explicit scope exists to prevent.

Scope is never inferred. Not from the session, not from the cookie, not from the request body, and not from an installation that happens to hold one queue. Four facts are read from storage on every request: the organization exists, the queue exists **and belongs to it**, the caller holds a membership, and the membership's role carries the capability the route needs.

Two roles, two capabilities, and no RBAC framework:

| | `READ_REVIEW_QUEUE` | `RESOLVE_REVIEW_CASE` |
|---|---|---|
| `VIEWER` | yes | no |
| `REVIEWER` | yes | yes |

**404 hides existence; 403 admits only what the caller already knows.** An unknown organization, an unknown queue, a queue owned by someone else, and a missing membership are one `404 NOT_FOUND` with one body — telling them apart would let anyone with an account enumerate tenants. `403 FORBIDDEN` is reachable only once membership is proven, so a `VIEWER` posting a resolution gets 403 while a non-member gets 404. A mismatched organization/queue pairing is refused and never silently corrected to the queue's real owner.

**Membership is read fresh, never cached in the session.** A session carries identity and nothing else — no organization, no queue, no role, no capability, in the cookie or in the row. Granting, changing or removing a membership therefore takes effect on the next request, in the same session, rather than in up to eight hours.

**Reviewer identity is server-derived.** The resolution request carries `decision` and `expected_version`, and nothing else; the server supplies the authenticated user's id to the domain, so the durable audit event names the session that was actually used. A body carrying `reviewer_id` is rejected with `422` rather than ignored — an ignored field would look accepted.

**Tenant authorization is not human-review authorization.** It answers whether a subject may *attempt* a resolution. Whether a particular `MATCH` is safe remains Sprint 08's answer, reached from review evidence, and a `REVIEWER` role does not influence it: a refused merge is still `422 MATCH_NOT_AUTHORIZED`, never a 403. Optimistic concurrency is untouched — a stale `expected_version` is still `409`.

`POST .../resolve` is state-changing and cookie-authenticated, so it uses the same strict trusted-`Origin` check as the authentication routes; a missing or forged `Origin` is refused before any session work happens. `GET` routes have no `Origin` requirement.

### Database schema 2.0.0

The review database is now schema **2.0.0**, and a 1.0.0 database cannot be opened: its review data belongs to no organization and no queue, so the process refuses to start rather than inventing an owner. Nothing is migrated automatically, no table is dropped, and no file is rewritten. The explicit operator migration command belongs to a later phase; a development database can be recreated instead.

### Provisioning a tenant

Organizations and review queues are created by an operator, never by an HTTP request and never as a side effect of registering a workflow. Provisioning a tenant resource and generating a workflow are separate decisions, so they are separate commands:

```bash
python scripts/manage_human_review.py create-organization --slug acme --name "Acme Inc."
python scripts/manage_human_review.py list-organizations
python scripts/manage_human_review.py create-review-queue --organization acme --name production-review
python scripts/manage_human_review.py add-membership \
    --user reviewer@example.com --organization acme --role REVIEWER
```

`add-membership` is what makes a tenant's queue reachable by a person, so every part of it is explicit: the user, the organization and the role are all required, there is no default role, and neither the user nor the organization is created for you. A user already holding a membership in that organization is refused — a user has exactly one role per organization — leaving the stored role exactly as it was.

**There is no HTTP equivalent, and there must not be.** A membership endpoint would let whoever holds a session widen their own reach; granting access to customer review evidence stays an operator action at a terminal.

Registering a workflow then names the organization and the queue explicitly, and creates neither — see Sprint 11 below.

## Sprint 12

Sprint 12 adds the Reviewer UI: a local browser interface over the Sprint 11 review API, in `frontend/`.

It is a client and nothing more. The queue, the evidence, the advisory observations, the history and every decision rule stay behind the API — the browser renders what is published, submits one human decision, and re-reads the result. Human review remains backend-authoritative: Sprint 08 decides whether a `MATCH` is permitted, and the UI has no field, request or code path through which it could evaluate, weaken, or bypass that. Semantic suggestions are displayed as advisory observations and can never become a decision.

**Temporarily incompatible with the API, and not internet-ready.** The UI was written against the Sprint 11 unscoped, unauthenticated review routes, which Sprint 13 replaced with authenticated tenant-scoped ones. Nothing in `frontend/src/` was changed in that phase apart from the generated `openapi.snapshot.json`: closing the server-side boundary and migrating the browser are separate pieces of work, and doing them together would have meant judging a security change by whether a screen still rendered. There is no login screen, no tenant or queue selector, and no session handling in the browser yet; that migration, production serving and browser end-to-end testing arrive next.

### Prerequisites

- Python 3.11 and the backend dependencies (see Sprint 11 below).
- Node.js 22.13 or newer. CI pins the 22 LTS line because it is the oldest major every locked dependency accepts; Node 24 works too.

### 1. Install the frontend

```bash
cd frontend
npm ci
```

`npm ci` rather than `npm install`: it installs exactly the locked tree.

### 2. Register a review queue and start the API

Follow Sprint 11 below — generate with `--register-review-queue`, then:

```bash
python -m review_api
```

Serves `http://127.0.0.1:8000`, loopback only.

### 3. Start the reviewer UI

```bash
cd frontend
npm run dev
```

Serves `http://127.0.0.1:5173`, loopback only.

The browser requests `/health` and `/api/v1/...` as **relative, same-origin paths**; the Vite dev server proxies them to the API on port 8000. That is why no CORS configuration exists or is needed: the API never sees a second origin, and it installs no CORS middleware precisely because CORS exists to *permit* cross-origin requests — there are none to permit here, only forged ones to refuse, which the `Origin` check does. There is no `VITE_API_URL` and no absolute backend URL in browser source — adding one would make every request cross-origin and reintroduce the problem the proxy avoids. `npm run preview` serves the production build under the same proxy and the same loopback constraint.

### 4. Verify

Open `http://127.0.0.1:5173`. `http://127.0.0.1:5173/health` should answer `{"status": "ok"}` through the proxy, and the pending queue should list the cases that were registered. The API does not need to be reachable from anywhere else, and should not be made so.

### What a reviewer should know before deciding

**Every decision is terminal.** `MATCH`, `NO_MATCH` and `DEFER` each move the case out of `PENDING` for good under the current workflow, and `DEFER` is no exception — it records the `DEFERRED` status rather than postponing anything. There is no reopen, no undo, no edit, and no second decision, in this interface or in the API behind it. That is why the UI asks for an explicit confirmation before every write.

**A case that changed must be reviewed again.** Each decision is submitted against the exact case version the reviewer was shown. If the case moved in between, the API refuses the decision with a version conflict and records nothing; the UI then re-reads the case and the reviewer has to look at the refreshed state and choose again. Nothing is retried automatically, and the browser never substitutes the newer version into a decision that was made against the old one.

**A refusal is not a failure to reach the server.** The API also refuses a `MATCH` that its Sprint 08 safety rules do not permit, and a decision that contradicts review state already recorded for the same records. In both cases nothing was written, and no alternative decision is chosen on the reviewer's behalf.

### Frontend checks

```bash
cd frontend
npm run lint
npm run typecheck
npm run test -- --run
npm run build
```

### Contract drift

`frontend/src/api/openapi.snapshot.json` is the schema FastAPI publishes, committed verbatim. Two tests keep it honest in both directions: `tests/review_api/test_frontend_openapi_snapshot.py` (Python) fails when the backend's schema no longer matches the committed file, and `frontend/src/api/contract.test.ts` (Vitest) fails when the handwritten TypeScript types no longer match it. Regenerate the snapshot only when an API change is intended:

```bash
python -c "import json; from review_api import create_app; print(json.dumps(create_app().openapi(), indent=2, sort_keys=True))" > frontend/src/api/openapi.snapshot.json
```

## Sprint 11

Sprint 11 puts a read/resolve REST API in front of the Sprint 10 persistent review queue, and gives the queue one official way to be populated and one official way to be served.

**Still not internet-ready, for different reasons than before.** Sprint 13 added authentication, tenant-scoped URLs, membership-based authorization and server-derived reviewer identity — so the routes described below are the Sprint 13 ones, and the unscoped paths Sprint 11 published no longer exist. What is still missing is rate limiting, TLS termination and a deployment configuration; the API is bound to localhost and is a local reviewer tool. "Production path" here means the real durable application path rather than fixtures or golden evaluation — it does not mean deployable.

### 1. Register a review queue

Trusted workflow registration is an operator action, never an HTTP request: the context it writes is what Sprint 08 MATCH authorization is later evaluated against. There is no `POST /register`, no bootstrap endpoint, and no way to upload a workflow.

```bash
python scripts/manage_human_review.py create-organization --slug acme --name "Acme Inc."
python scripts/manage_human_review.py create-review-queue --organization acme --name production-review
python scripts/manage_human_review.py generate input.csv --report-dir human_review/reports/demo \
    --register-review-queue --organization acme --review-queue production-review
```

`--register-review-queue` is opt-in; without it `generate` writes the report and nothing durable. With it, both `--organization` and `--review-queue` are required and both must already exist: **this command provisions nothing.** A missing argument is a usage error (exit code 1), and a name that resolves to nothing — a misspelled slug, a misspelled queue, or a queue that belongs to a different organization — is refused with exit code 6, leaving the database exactly as it was. Nothing is created to satisfy the request. `--review-db PATH` overrides the target database for that run. The queue lives at `storage/review_queue.db`, configured in `configs/review_persistence.yaml`.

Re-running the command with the same input is an idempotent no-op **within that queue**: no case is duplicated, no history event is appended, and a case a reviewer has already resolved keeps its status, version and resolution. A changed record set, a changed AUTO_MATCH snapshot, or a different entity-resolution config path is refused with exit code 5, leaving the stored queue exactly as it was. Registering the same workflow into a different queue is independent of all of that.

### 2. Serve the reviewer API

```bash
python -m review_api
```

Needs `fastapi`, `pydantic` and `uvicorn`, declared together as the `api` extra in `pyproject.toml`. This repository is run from a source checkout rather than installed as a distribution, so install them with `pip install -r requirements-dev.txt` and run the command from the repository root.

Defaults to `http://127.0.0.1:8000` with one worker and no reloader; `--host` accepts loopback addresses only (`127.0.0.1`, `localhost`, `::1`) and `--port` sets the port. Register a queue first — the runner refuses to start against a database that does not exist rather than creating an empty one that would look like a fully reviewed queue.

The server binds **no queue at startup**. It reads no queue count, serves as many organizations and queues as an operator created, and starts happily against a database holding none — every request names the queue it wants, so there is nothing to disambiguate at boot. The transitional sole-queue resolution that stood here while the routes had no authenticated caller is gone.

A queue that exists but has never had a workflow registered still serves an empty page; it refuses every decision with `503 REVIEW_QUEUE_NOT_READY` rather than authorizing against a context that was never stored.

Liveness is `GET /health`, which returns `{"status": "ok"}`, reports nothing about storage, and needs no credential. The reviewer endpoints are tenant-scoped (see "Tenant authorization" under Sprint 13 above), and the generated OpenAPI schema is served at `/openapi.json`:

```
GET  /api/v1/organizations/{organization_id}/review-queues/{review_queue_id}/review-cases
GET  .../review-cases/{review_case_id}
GET  .../review-cases/{review_case_id}/events
GET  .../review-cases/{review_case_id}/semantic-suggestions
POST .../review-cases/{review_case_id}/resolve
```

`POST .../resolve` is a thin adapter over `ReviewQueueService.resolve_case`: it accepts `MATCH` / `NO_MATCH` / `DEFER` with an `expected_version`, supplies the authenticated user as the reviewer, and Sprint 08 remains the only authority on whether the decision is allowed. Semantic suggestions are read-only — Sprint 09 generation is not reachable over HTTP.

## Sprint 08

Sprint 08 adds a deterministic human-review domain layer for ambiguous entity-resolution decisions. REVIEW is not treated as failure — it is the safe routing path for pairs that must not be auto-merged.

Highlights:

- `human_review/` package with `ReviewCase`, evidence, explanations, workflow, and audit trail,
- machine vs human decision separation (`AUTO_MATCH`/`REVIEW`/`NO_MATCH` vs `MATCH`/`NO_MATCH`/`DEFER`),
- stable review case IDs and order-invariant case generation,
- human-confirmed `MATCH` integration into canonical entity construction via `HR-*` clusters,
- unresolved `REVIEW` / `DEFER` records remain excluded from unsafe merging,
- transitive `MATCH`/`NO_MATCH` contradiction detection (fail closed),
- human review CLI (`scripts/manage_human_review.py`),
- validated `human_review_report.json` (`schema_version` 1.0.0) consumed by the canonical CLI,
- authorization context persisted in the report and applied on `resolve MATCH`,
- review safety invariants as product hard gates,
- real review benchmark with safety invariants (`evaluation/review_benchmark.py`).

Manage review cases:

```bash
python scripts/manage_human_review.py generate input.csv --report-dir human_review/reports/demo
python scripts/manage_human_review.py list human_review/reports/demo/human_review_report.json
python scripts/manage_human_review.py inspect human_review/reports/demo/human_review_report.json RC-rec-a--rec-b
python scripts/manage_human_review.py resolve human_review/reports/demo/human_review_report.json RC-rec-a--rec-b --decision MATCH --reviewer-id reviewer-1 --output-report-dir human_review/reports/demo-resolved
```

Build canonical entities, optionally applying a validated human-review outcome:

```bash
python scripts/build_canonical_entities.py input.csv
python scripts/build_canonical_entities.py input.csv --human-review-report human_review/reports/demo-resolved/human_review_report.json
```

See `docs/development-reports/SPRINT_08_HUMAN_REVIEW_AND_AMBIGUITY_RESOLUTION.md` and `docs/development-reports/ACCEPTANCE_THRESHOLDS.md`.

## Sprint 09

Sprint 09 adds controlled, **advisory** LLM semantic review for unresolved `REVIEW` cases. Human Review remains the only writer of `MATCH` / `NO_MATCH` / `DEFER`.

- Disabled by default (`configs/semantic_review.yaml`)
- Default demo model: GPT-5.6 Luna via OpenAI Responses API
- Provider/model configurable; Sprint 09 implements one real adapter (`OpenAIProvider`)
- Fake/scripted providers for tests and CI — these do **not** claim Luna quality
- Live calls require explicit `--live`, `enabled: true`, `OPENAI_API_KEY`, and a demo budget
- Semantic benchmark is validation-split only and is not a product hard gate

```bash
python scripts/suggest_human_review.py suggest --report PATH --case-id RC-... --fake
python scripts/suggest_human_review.py inspect --suggestion-id LS-...
python scripts/suggest_human_review.py benchmark --scripted
python scripts/suggest_human_review.py benchmark --dataset datasets/golden/v0.1.0 --split validation --fake
```

See `docs/development-reports/SPRINT_09_CONTROLLED_LLM_SEMANTIC_INTELLIGENCE.md`.

## Sprint 7B

Sprint 7B hardens evaluation and acceptance across Sprints 01–07 without adding new product features.

Highlights:

- real product hard gates when running `evaluation.run --dataset ...` (separate from fixture smoke),
- row accounting audit (`discovered = accepted + rejected`, zero silent row loss),
- independent Source B benchmark fixture (`evaluation/fixtures/source_b_expected_mappings.json`),
- four-way dataset splits including locked `final_holdout`,
- atomic hard-negative pair split assignment,
- validation-only threshold sweep (recommendation only, no auto-write),
- critical-field schema mapping recall,
- index-based blocking recall improvements (`phone_last7`, `first_name`+`last_name`).

See `docs/development-reports/SPRINT_07B_RELIABILITY_AND_ACCEPTANCE.md` and `docs/SPRINT_NUMBERING.md`.

## Sprint 07

Sprint 07 adds deterministic survivorship and canonical entity construction on top of Sprint 06 entity resolution clusters.

Current capabilities include:

- `survivorship/` package with field rules, provenance, conflict preservation, and reporting,
- config-driven survivorship policy (`configs/survivorship.yaml`),
- quality-first field selection using Sprint 04 validation evidence (`survivorship/candidate_quality.py`),
- non-destructive canonical entity construction from AUTO_MATCH clusters,
- singleton entity generation for unmatched non-review records,
- field-level provenance and preserved conflict metadata,
- quality-first field selection with source priority as late tie-breaker,
- real survivorship benchmark on golden dataset ground truth (`evaluation/survivorship_benchmark.py`),
- survivorship CLI (`scripts/build_canonical_entities.py`).

Build canonical entities:

```bash
python scripts/build_canonical_entities.py datasets/generated/ci-smoke/validation-positive-smoke.csv
python scripts/build_canonical_entities.py source_a.csv source_b.csv --inspect-entity CE-C-000001
```

Run evaluation with real survivorship metrics:

```bash
python -m evaluation.run --dataset datasets/generated/ci-smoke/v0.1.0 --malformed-fixtures datasets/golden/v0.1.0/malformed
```

Fixture hard gates remain infrastructure smoke; real product hard gates and benchmark metrics are evaluated separately when running evaluation with `--dataset`.

## Sprint 06

Sprint 06 adds deterministic, explainable entity resolution on canonical mapped and normalized records.

Current capabilities include:

- `entity_resolution/` package with blocking, evidence, scoring, decisions, and clustering,
- config-driven weights and thresholds (`configs/entity_resolution.yaml`),
- deterministic candidate generation via indexed blocking (email, phone, name+city, surname+company, company+city),
- AUTO_MATCH / REVIEW / NO_MATCH decisions with false-match safety guards,
- explainable pair evidence and conflict modeling,
- deterministic entity clustering with transitive conflict guard,
- source record immutability (no destructive merge),
- structured review queue contract for future UI/API,
- real entity resolution benchmark on golden dataset ground truth (`evaluation/entity_resolution_benchmark.py`),
- entity resolution CLI (`scripts/resolve_entities.py`).

Resolve entities:

```bash
python scripts/resolve_entities.py datasets/generated/ci-smoke/validation-positive-smoke.csv
python scripts/resolve_entities.py input.csv --inspect-pair source_a-000001 source_a-000002
```

Run evaluation with real entity resolution metrics:

```bash
python -m evaluation.run --dataset datasets/generated/ci-smoke/v0.1.0 --malformed-fixtures datasets/golden/v0.1.0/malformed
```

Fixture `auto_merge_precision`, `false_merge_rate`, and `candidate_recall` remain infrastructure smoke; real entity resolution metrics are reported separately when running evaluation with `--dataset`.

## Sprint 05

Sprint 05 adds deterministic, explainable source-to-canonical schema mapping.

Current capabilities include:

- `schema_mapping/` package with separated decision and application layers,
- config-driven aliases and evidence weights (`configs/schema_mapping.yaml`),
- canonical schema integration via `configs/canonical_schema.yaml`,
- header preprocessing with Turkish Unicode support,
- evidence-based scoring (alias, lexical, type, pattern, profile),
- AUTO_MAP / REVIEW / UNMAPPED / CONFLICT decisions with ambiguity margin and collision handling,
- `MappingPlan` JSON reports with evidence and alternatives,
- mapping application preserving source lineage and unmapped columns,
- integration with Sprint 04 validation/normalization pipelines,
- real schema mapping benchmark (`evaluation/schema_mapping_benchmark.py`),
- schema mapping CLI (`scripts/map_schema.py`),
- XLSX extra-column silent truncation fixed (rows rejected instead).

Map source columns:

```bash
python scripts/map_schema.py datasets/golden/v0.1.0/malformed/utf8_turkish.csv
python scripts/map_schema.py input.csv --apply
```

Run evaluation with real schema mapping metrics:

```bash
python -m evaluation.run --dataset datasets/generated/ci-smoke/v0.1.0 --malformed-fixtures datasets/golden/v0.1.0/malformed
```

Fixture `schema_mapping_accuracy` remains infrastructure smoke; real mapping metrics are reported separately.

## Sprint 04

Sprint 04 adds deterministic validation and normalization on top of parsed datasets.

Current capabilities include:

- modular validation rules with severity taxonomy (`validation/`),
- modular normalization rules with transformation audit trail (`normalization/`),
- validate → normalize → revalidate pipeline (`record_quality/`),
- original-value preservation and idempotent normalizers,
- Turkish Unicode preservation (no ASCII folding),
- TR phone normalization to E.164,
- email/company/location/address deterministic rules,
- validation and normalization CLIs with JSON reports,
- real normalization benchmark on golden dataset corruption log (partial product quality).

Validate records:

```bash
python scripts/validate_records.py datasets/golden/v0.1.0/malformed/utf8_turkish.csv
```

Normalize records (does not overwrite source):

```bash
python scripts/normalize_records.py datasets/golden/v0.1.0/malformed/utf8_turkish.csv
```

Hard gates for entity resolution remain fixture smoke. Real `normalization_accuracy` is reported separately when running evaluation with `--dataset`.

## Sprint 03

Sprint 03 adds CSV/XLSX ingestion, row accounting, and deterministic profiling.

Current capabilities include:

- centralized ingestion configuration (`configs/ingestion.yaml`),
- structured ingestion error taxonomy,
- CSV parsing with delimiter/encoding detection,
- XLSX parsing via openpyxl with explicit worksheet metadata,
- zero silent row loss via row accounting invariant,
- column and dataset profiling (completeness, uniqueness, type inference, patterns),
- JSON/Markdown profiling reports,
- CLI dataset profiling,
- real ingestion smoke checks in the evaluation harness (opt-in via `--malformed-fixtures`).

Profile an input file:

```bash
python scripts/profile_dataset.py datasets/golden/v0.1.0/malformed/utf8_turkish.csv
python scripts/profile_dataset.py path/to/file.xlsx --worksheet SheetName
```

Run evaluation with dataset sanity and ingestion smoke:

```bash
python -m evaluation.run --dataset datasets/generated/ci-smoke/v0.1.0 --malformed-fixtures datasets/golden/v0.1.0/malformed
```

The harness still runs in `FIXTURE_SMOKE` mode for hard gates. Ingestion smoke checks are real Sprint 03 signals but are not product-quality entity-resolution metrics.
