# Contracts

`Admission` + `ResearchScope` define the request; `Understanding`, `Plan`, `PlanTask`, `ToolCall` and `ReflectionOutput` define reasoning handoffs. `AgentExecution` is the authoritative specialist output record, exported to `contracts/agent-execution.v1.schema.json`. `ExecutionContext` isolates in-memory reasoning; a fenced checkpoint serializes meaningful boundaries. Persisted execution records carry lifecycle failure codes and attempts; SSE is a version-1 committed-state notification contract.

`FinancialSource`, `FinancialEvidence`, `FinancialClaim` and `ResearchReport` define report version 1, exported to `contracts/research-report.v1.schema.json`. New fields are optional/defaulted for old reports. The report validator rejects duplicate identities, unresolved sources/input lineage and claims with inconsistent values. Calculated metrics require methods and inputs. Specialist records reject extra fields and record tools, timings, failures and evidence IDs.

`src/services/research-contract.ts` validates browser/terminal reports with Zod. The generated-schema drift check plus real Python → HTTP → Bun/React report tests protect both ends. Regenerate using `PYTHONPATH=python-backend python python-backend/scripts/export_contracts.py`; CI uses `--check`.

Shared model-call accounting reserves configured output ceilings and prompt characters before each transport attempt/JSON repair. These are budget reservations, explicitly not measured token usage or estimated billed cost. Provider/model ID and attempt metadata accompany the report. Missing SDK usage is not invented.
