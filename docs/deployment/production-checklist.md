# Production release gates

The branch is **not declared production-ready**. Local fixture tests demonstrate connected functionality; they do not establish licensed live data, production capacity or deployment resilience.

- Require successful mandatory hosted checks on the published commit. PR #10 code-commit Actions runs were inspected: jobs did not start because the account is locked due to billing. Restore execution and rerun mandatory checks.
- Run opt-in OpenAI and Ollama checks with actual configured models; validate invalid credentials, real endpoint timeouts and account limits without exposing keys.
- Run licensed live financial-provider journeys and verify original filing dates, prices, currencies, expected reporting coverage, pagination, and adjustment policy against sources. Do not certify point-in-time fundamental backtests.
- Confirm commercial use, intended users, retention and export rights. No independent cross-source verification is available from a single provider.
- Validate Docker image/Compose startup, TLS ingress, restricted CORS, real JWT issuer/audience and expiry, Redis ACL/TLS/AOF/backups, process restarts, restore drills and retention.
- Review dependency audit outputs, base-image scanning, lockfiles and the dependency versions chosen for security remediation. No automated scan proves absence of vulnerabilities.
- Soak/load-test expected users, worker throughput, queue latency, event replay after trimming, report sizes and provider quotas. Aggregate tenant billing/role-based model budgets and long-term archives need separate deployment decisions.
- Review richer qualitative synthesis, instrument/exchange identity resolution and multi-provider reconciliation before claiming broader analytical completeness.
