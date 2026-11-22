# Knowledge graph research and repository audit

Inspected 2026-11-22. Baseline main: `4c20c5b41ca027b1ced732798b8f907302d2cf2f`; fresh clean checkout, no pre-existing edits. Baseline Python suite: 143 passed, 61 skipped, 140 warnings. No Redis URL or external credentials enabled.

## Existing application

README, docs/architecture, app/main.py, execution_api.py, platform_api.py, platform_store.py, execution_worker.py, dexter_py/agent, tools, tests, contracts, dependency/environment files and both Compose files were inspected. FastAPI lifespan owns provider clients and orchestrator; standalone worker enters that same lifespan. Redis ExecutionRegistry is the durable source of truth for research lifecycle, and the existing custom Python Orchestrator owns reasoning. Reports are validated by financial_evidence/verification. No new graph writes enter this research pipeline in these phases. Requirements are pinned in requirements.txt (Python >=3.11), Ruff and pytest in requirements-dev.txt; Bun builds browser and legacy CLI. JWT issuer/subject becomes a hashed registry principal; shared API keys represent one account. PlatformStore.workspace checks membership/role in Redis. Graph workspace routes must reuse these checks for every request.

## Code inspected before implementation

### neo4j/neo4j-python-driver

- Repository: https://github.com/neo4j/neo4j-python-driver
- Revision: `72e1297303d579ad81d0640f9d5662061b53ac1a`
- Inspected code: `src/neo4j/_async/driver.py; _async/work/session.py; tests/integration/mixed/test_async_driver.py`.
- License/reuse: Apache-2.0 plus Python notices; retain notices if copying substantial code.
- Decision: **Adopt dependency**. One pooled driver, short sessions, execute_read/write retryable callbacks, verify_connectivity; never share sessions. Pin 5.28.2 with Neo4j 5.26 LTS; inspect upgrades before changing major versions.

### getzep/graphiti

- Repository: https://github.com/getzep/graphiti
- Revision: `9b8a4630092084734b080f2e6f409c2c23bdcc3a`
- Inspected code: `graphiti_core/edges.py; nodes.py`.
- License/reuse: Apache-2.0; retain notices if copying substantial code.
- Decision: **Adapt concepts only**. Separate created/valid/invalid times, provenance episodes and group partitions. No extraction, embeddings, orchestration or automatic contradiction resolution; group IDs alone do not authorize access.

### neo4j/neo4j-graphrag-python

- Repository: https://github.com/neo4j/neo4j-graphrag-python
- Revision: `b6a9c06417d2fe504f7fa4761de2fef875c2e297`
- Inspected code: `src/neo4j_graphrag/components/schema.py; experimental/pipeline/types/schema.py`.
- License/reuse: Apache-2.0 plus Python notices; retain notices if copying substantial code.
- Decision: **Adapt schema principles; reject dependency**. Typed endpoint constraints and structured schema validation. Experimental pipeline imports remain experimental; generated extraction and Cypher are outside scope.

### neo4j-contrib/neomodel

- Repository: https://github.com/neo4j-contrib/neomodel
- Revision: `2f4a9044ef93a115daa5cd712e401d343607911c`
- Inspected code: `neomodel/async_/connection.py; node.py`.
- License/reuse: MIT; retain notices if copying substantial code.
- Decision: **Reject dependency**. Process-wide driver pooling is useful, but an OGM duplicates Pydantic and hides transaction/identity boundaries needed for evidence and tenant checks.

### booqoffsky/neo4j-python-migrations

- Repository: https://github.com/booqoffsky/neo4j-python-migrations
- Revision: `a4e059a2b37b7b1f0316633e1547535e2b93f22d`
- Inspected code: `neo4j_python_migrations/executor.py; dao.py; analyzer.py; pyproject.toml`.
- License/reuse: MIT; retain notices if copying substantial code.
- Decision: **Adapt ordering/checksum principles; reject runner**. Executor applies schema then records metadata separately. Baseline creation is read-then-create; inspected runner has no cross-process migration mutex. A narrow runner with a held database lock and idempotent DDL addresses the requirements without importing a second CLI stack.

### prrao87/neo4j-python-fastapi

- Repository: https://github.com/prrao87/neo4j-python-fastapi
- Revision: `36a18f76385f227c854e5db9ee6180ae8883497f`
- Inspected code: `src/api/main.py; routers/query.py; tests/test_crud.py`.
- License/reuse: MIT; retain notices if copying substantial code.
- Decision: **Adapt lifecycle concept only**. Async lifespan and typed query results are useful. Reject shared application session: Neo4j sessions are not concurrency safe. No authorization pattern reusable for this app.

### JanviChitroda24/financial-knowledge-graph

- Repository: https://github.com/JanviChitroda24/financial-knowledge-graph
- Revision: `0ae50ed69a384e21d270fefe06296a4903439d1f`
- Inspected code: `src/ids.py; schema.py; ingest_supply_chain.py`.
- License/reuse: All rights reserved (source headers); retain notices if copying substantial code.
- Decision: **Reject code reuse**. Corporate suppliers/executives/filings are useful domain comparison. Name-based truncated MD5 identity is inappropriate for legal identity; curated dataset is not evidence of production readiness. No code/data copied or redistributed.

## Search coverage and decision

GitHub/web searches covered financial and supply-chain graphs, corporate relationships, temporal databases, provenance, FastAPI/Pydantic, integration testing, entity identity and graph access control. Results were treated as discovery rather than trusted engineering claims. The actual code above drove selection. Driver integration tests provide a real-server testing pattern; Graphiti edges cover temporal facts and source episodes. No third-party source was copied; implementations are original, using documented APIs and general patterns. Redis and the research worker stay unchanged.

Neo4j Community 5.26 LTS provides durable ACID transactions and composite uniqueness without an Enterprise-only schema requirement. It does not provide tenant row-level security: server-side scope checks and fixed queries are mandatory. Assertions are authoritative nodes; no redundant direct financial edges. Security, Exchange and Currency are included as distinct ontology types because current financial instrument contracts already separate instrument, venue and currency. Tickers identify securities with MIC plus assignment epoch, never globally identify companies.
