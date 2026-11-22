# Living Financial Knowledge Graph: Phase 1–2 delivery

Date: 2026-11-22. Base commit: `4c20c5b41ca027b1ced732798b8f907302d2cf2f`. Branch: `feature/living-financial-graph-phase1-phase2`. The initial checkout was clean. No existing edits were overwritten. No main-branch push, merge or deployment is part of this work.

## Delivered scope

Phase 1 implements fourteen entity types, fifteen allowlisted predicate definitions with endpoint validation, strict Pydantic v1.0 graph contracts, namespaced authoritative identifiers, provenance/evidence contracts, distinct observation/valid/knowledge times and proposed/verified/contradicted/retracted/superseded lifecycle semantics. Confidence never grants verification. Serialization normalizes aware timestamps to UTC, rejects unknown schemas, and preserves null effective dates. Separate graph JSON contracts leave all existing research/platform schemas unchanged.

Phase 2 implements an official-driver async Neo4j repository; scoped entity create/replay and compare-and-swap metadata history; permanent authoritative identifier ownership and deterministic collision handling; immutable source snapshots; immutable evidence-backed assertions; server-owned ingestion time; append-only lifecycle revisions; historical status retrieval; bounded one-hop queries; atomic source/endpoint links; uniqueness constraints and indexes; explicit checksummed serialized migration execution; and cancellation-safe transactions. A scoped semantic fingerprint also prevents duplicate observations with different UUIDs.

The optional Neo4j 5.26.33 Community Compose overlay adds a durable volume, development memory settings, health/restart configuration, no published database ports and a separate migration job. Disabled graph deployments never connect. Enabled startup checks connectivity/schema and fails clearly on missing infrastructure; it does not silently return synthetic data. Driver clients are closed through the existing application lifespan shared by API and standalone worker. Bounded transaction outcome/duration metrics use the existing authenticated `/metrics`; labels contain no principals or source content.

Read-only graph endpoints reuse existing principal hashing, JWT/API-key authentication, platform rate limiting and Redis workspace membership checks. There is no graph mutation API, arbitrary Cypher interface or graph tool registered with the orchestrator. Existing financial providers, report validation, research execution, Redis contracts and frontend are unchanged.

## Research decisions

The code-level research report records URLs, exact inspected revisions, files, licenses and decisions for neo4j-python-driver, Graphiti, neo4j-graphrag-python, neomodel, neo4j-python-migrations, neo4j-python-fastapi and a financial supply-chain graph. Adopt the official driver; adapt temporal/provenance, schema and lifecycle concepts. Reject Graphiti/GraphRAG orchestration/extraction dependencies, OGM duplication, shared sessions and the inspected migration runner's insufficient cross-process locking. Reject reuse of all-rights-reserved financial graph source/data. No third-party source was copied. The small original migration runner is justified by the required database-held mutex and recoverable DDL/history boundary.

Neo4j provides ACID persistence and Community-compatible composite uniqueness. Authoritative Assertion nodes retain evidence and history; no direct financial edges become a conflicting fact store. Security, Exchange and Currency are distinct entity types to preserve issuer/instrument/venue/currency semantics. The exact LTS patch was verified against the official deployment center; no floating image tag is used.

## Verification results

Python 3.12.14, Bun 1.4.3, Neo4j Community 5.26.33, driver 5.28.2 and Redis 7.4.2 were used. Both databases were disposable loopback-only development instances. No live financial provider calls or production database mutations were made.

| Check | Executed command / setup | Result |
| --- | --- | --- |
| Pre-edit Python baseline | `PYTHONPATH=python-backend /tmp/fa-venv/bin/python -m pytest -q python-backend` without Redis/graph/live flags | 143 passed, 61 skipped, 140 warnings |
| Full final Python suite | Same pytest command with `TEST_REDIS_URL=redis://127.0.0.1:16379/15`, `TEST_NEO4J_URI=bolt://127.0.0.1:17687`, `GRAPH_TEST_DISPOSABLE=yes`, `NEO4J_TEST_HOME=/tmp/neo4j-community-5.26.33` | 251 passed, 14 skipped; 397 legacy/dependency warnings |
| Dedicated database suite (initial) | `pytest -q python-backend/tests/test_graph_repository.py` with disposable Neo4j flags | 18 passed before adding duplicate-content/cancellation/partial-migration cases; all 21 included in final full suite |
| Restart recovery | `pytest -q python-backend/tests/test_graph_restart.py` with disposable distribution flags | 1 passed standalone; also included in final full suite |
| Ruff | `/tmp/fa-venv/bin/ruff check python-backend` | Passed |
| Strict graph typing | `PYTHONPATH=python-backend /tmp/fa-venv/bin/mypy --strict --follow-imports=silent python-backend/dexter_py/graph` | Passed, six source files |
| Bytecode compilation | `/tmp/fa-venv/bin/python -m compileall -q python-backend/app python-backend/dexter_py python-backend/tests` | Passed |
| Wire contract drift | `PYTHONPATH=python-backend /tmp/fa-venv/bin/python python-backend/scripts/export_contracts.py --check` | Passed; existing schemas unchanged |
| Bun dependencies | `bun install --frozen-lockfile` | Passed |
| TypeScript / web types | `bun run typecheck`; `bun run typecheck:web` | Both passed |
| Cross-runtime HTTP/Bun/Ink integration | `pytest -q python-backend/tests/test_runtime_roundtrip.py` with disposable Redis and Bun on PATH | 1 passed separately; this case was skipped in the full Python run because Bun was not on that command's PATH |
| Bun unit tests | `bun test` | 17 passed, 0 failed |
| CLI / browser builds | `bun run build`; `bun run build:web` | Both passed |
| Compose configuration | Docker Compose v2.39.4 `config --quiet`, base+graph, base+graph+local, base+local with example env and ephemeral fixture password | All three passed |
| Browser E2E | `bun run test:web` with disposable Redis, `PYTHON=/tmp/fa-venv/bin/python`, `CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium` | 11 failed at browser launch; Chromium socket operation denied by environment; browser assertions not exercised |
| Whitespace | `git diff --check` | Passed |

The full Python suite contains 61 graph cases: 37 model, 21 repository, two API and one restart case. The full run skipped thirteen credential-gated live-provider cases and one HTTP/Bun/Ink cross-runtime case. The latter was then executed separately with Bun on PATH and passed (1 passed in 2.82 seconds). Across these runs, 252 unique Python cases passed and thirteen live-provider cases remain unverified; no skipped case is counted as passing without that separate execution. Existing runtime integration tests exercise research jobs, Redis fencing/recovery, worker processing, provider adapters, evidence-backed reports and API auth using fixtures at external provider boundaries.

The initial database attempt failed because services launched in separate sandbox command namespaces could not communicate. The harness was corrected to start services and tests together. A first combined run exposed a test-setup error: the graph-enabled API test ran before explicit deployment migrations. The test now runs the explicit migration itself, independent of test ordering. Neither failure was hidden by disabling tests. Neo4j 5.26.0 was used for the first persistence run, then replaced by verified current LTS patch 5.26.33 for final verification.

The Playwright browser download also returned HTTP 403, so the installed Chromium was tried. That browser launch was blocked by `socket() failed: Operation not permitted`. No sandbox policy was weakened and no browser test was disabled. Docker Compose syntax was verified using a standalone Compose binary; no Docker daemon was available for image build/container startup. Native disposable Neo4j persistence and restart tests do not substitute for an actual container deployment check.

## Security review

Resolved risks include raw-ID cross-tenant access (scope required everywhere), tenant selection by untrusted payload (server-derived scope plus current workspace ACL), shared async session races (short sessions/pool), ticker ambiguity (Security+MIC+assignment), identifier races (unique ownership records/transaction rollback), mutable evidence (immutable snapshots/assertions), verification overwrite (audited expected-revision append-only transitions), duplicate UUID evasions (semantic uniqueness), Cypher injection (fixed queries/parameters), unbounded traversal (one hop/limit/timeout), embedded URI credential leakage (rejected URI credentials/hidden validation inputs), and graph startup drift (explicit migrations/schema checks).

Remaining boundaries: trusted database writers can bypass application authorization in Community; identifier validation does not query authoritative registries; trusted reviewer code must authenticate evidence before marking sources verified; source retention and workspace deletion are not synchronized automatically; host clocks must be synchronized for knowledge timestamps. No assertion is promoted by an LLM, confidence score or automatic research execution.

## Remaining verification and Phase 3

Before production release, run the Docker image/container smoke test, browser E2E in a host that supports Chromium, TLS/private-network and credential-rotation checks, and a backup restore drill. Live financial-provider tests remain independently gated. This is a working Phase 1–2 foundation with those release checks outstanding, not a claim that every production readiness gate passed.

Start Phase 3 with one permissioned authoritative ingestion adapter and a separately authorized source-review workflow. Reuse the existing financial-data stack; normalize legal identifiers from source metadata, attach licensed immutable source snapshots and page evidence, and propose assertions with stable IDs. Add explicit handling for unknown validity and identity reassignment, auditable retention/physical deletion, bitemporal entity lookup, pagination/degree performance baselines and reviewed contradiction workflows. Do not add another orchestrator, automatic verification or trading.

## Changed files

- `README.md`
- `contracts/knowledge-graph.v1.schema.json`
- `docker-compose.graph.yml`
- `docs/README.md`
- `docs/architecture/knowledge-graph-migrations.md`
- `docs/architecture/knowledge-graph-ontology.md`
- `docs/architecture/knowledge-graph-persistence.md`
- `docs/architecture/knowledge-graph-research.md`
- `docs/architecture/knowledge-graph-security.md`
- `docs/implementation/knowledge-graph-phase1-phase2.md`
- `docs/operations/runbook.md`
- `docs/testing/verification.md`
- `python-backend/.env.example`
- `python-backend/app/graph_api.py`
- `python-backend/app/main.py`
- `python-backend/dexter_py/graph/__init__.py`
- `python-backend/dexter_py/graph/config.py`
- `python-backend/dexter_py/graph/metrics.py`
- `python-backend/dexter_py/graph/migrations.py`
- `python-backend/dexter_py/graph/models.py`
- `python-backend/dexter_py/graph/repository.py`
- `python-backend/requirements-dev.txt`
- `python-backend/requirements.txt`
- `python-backend/scripts/export_contracts.py`
- `python-backend/tests/test_graph_api.py`
- `python-backend/tests/test_graph_models.py`
- `python-backend/tests/test_graph_repository.py`
- `python-backend/tests/test_graph_restart.py`


## Follow-up integrity revision (2026-11-22)

The separate review branch `fix/financial-graph-integrity-review-20261122` makes targeted changes without changing the Phase 1–2 ownership boundary:

- Schema verification checks both required temporal indexes and exact applied migration version/checksum history, not merely constraint names. Schema drift now blocks graph-enabled application startup.
- `GraphRepository.get_entity(..., known_at=...)` and `GET /api/v1/graph/entities/{uuid}?known_at=...` expose recorded entity metadata at knowledge time. Historical identifier ownership and valid-time entity revisions are still outside the current scope.
- The assertion semantic fingerprint sorts evidence references before hashing, making duplicate detection order-independent without changing persisted evidence ordering.
- Entity and source model validation rejects timestamps more than five minutes in the future. This is a bounded clock-skew allowance, not a claim of independently verified source provenance.
- New unit, disposable Neo4j integration, and API regression cases cover these corrections. A separate GitHub Actions job provisions disposable Neo4j and Redis for graph tests rather than skipping all database-backed cases.

**Verification distinction:** The original 251-pass full Python suite and other results above describe the initial implementation commit, not this revision. Do not attribute those historical results to the review branch. The review revision requires fresh CI and/or local test execution before it can be considered verified.
