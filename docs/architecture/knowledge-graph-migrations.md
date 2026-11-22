# Graph schema migrations

Migrations are ordered, nondestructive, checksummed definitions in `dexter_py.graph.migrations.MIGRATIONS`. Run explicitly before starting graph-enabled API/worker:

```bash
cd python-backend
GRAPH_ENABLED=true python -m dexter_py.graph.migrations
```

Provision NEO4J_URI/USERNAME/PASSWORD/DATABASE through your environment first. In Docker the dedicated `graph-migrate` service runs this command and API/worker depend on its successful completion. Manual launches must export settings: this command reads environment variables and does not implicitly load `.env`. API and research workers never run schema creation.

The runner bootstraps one IF NOT EXISTS uniqueness constraint for a database-wide GraphMigrationLock. A write to its fixed `schema` row takes a database transaction lock before inspecting history or issuing migration DDL. Concurrent processes serialize on that lock; it is held until checksum history commits. A disconnected/crashed process releases its transaction lock without an expiring lease that could admit a second migrator during a slow migration. Lock transaction timeout is 120 seconds; schema statements have 60-second timeouts. Migrations should remain small; split large future backfills into separately reviewed resumable tooling.

Neo4j forbids mixed data and schema writes in one transaction. Therefore DDL runs in separate short schema sessions while the data transaction holds the mutex and records metadata. Every initial schema statement uses IF NOT EXISTS. If a crash/failure follows DDL but precedes metadata commit, the next run safely replays the DDL; it does not claim the failed migration succeeded. Constraints fail visibly on incompatible existing data, leaving old data intact. Applied history must be a contiguous prefix of local ordered versions, with exactly matching SHA-256 checksums. Unknown versions, gaps, reorderings and changed checksums stop migration rather than guessing.

Do not edit an applied migration. Add a higher ordered version with additive idempotent statements, test upgrade from the old schema, and back up before deployment. There is no automatic destructive rollback or DROP. Roll forward with a new reviewed migration; if rollback is essential, restore an offline backup into a separate volume and verify it before switching writers. Idempotent DDL is a migration author requirement, not an assumption that arbitrary data changes can be replayed.

Tests exercise replay, concurrent migration sessions, checksum/order rejection and a DDL failure after a successful step with uncommitted metadata. Runtime startup checks required constraint names. Database users must not alter schema/history outside this migration process. For hard separation, operators should supply distinct schema-deployment and runtime credentials; Community's access-control granularity is limited.
