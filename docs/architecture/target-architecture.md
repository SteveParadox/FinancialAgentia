# Target architecture

Retain FastAPI, Redis, the dedicated Python worker, and the Python custom Orchestrator. React DOM extends the existing React/Bun project because Ink cannot render browser controls. Bun bundles the browser; FastAPI serves it on the API origin, avoiding another deployed service or CORS-dependent authentication path.

The job registry exclusively owns admission, ownership, idempotency, attempts, deadlines, fencing, cancellation, persistence and terminal transitions. The orchestrator exclusively owns plans, DAG validation, specialist tool execution, reflection and draft synthesis. Reports pass deterministic evidence validation before a worker can commit them. A partial report has a successful execution state and an explicit `PARTIAL` report status; these are separate concepts.

Redis Lua makes admission/queue publication and result/history/events transactional. Generation fencing protects every checkpoint, event and finish. Recovery reuses complete plan boundaries; interrupted provider calls may run again under at-least-once semantics. User retry creates a linked new job and preserves the failed attempt. Browser polling and authenticated fetch streaming read committed state only. Disconnecting never cancels a job.

Provider configuration is server controlled. The browser displays the configured model; it cannot select an unauthorized arbitrary model or receive API secrets. JWT subjects isolate users; shared API key mode is explicitly one shared account. Browser credentials remain in memory; re-authentication is required after a full refresh.
