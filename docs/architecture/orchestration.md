# Authoritative orchestration

`app.execution_worker.ExecutionWorker` is the only production research job runner. `dexter_py.agent.orchestrator.Orchestrator` owns reasoning; the Redis registry owns lifecycle state. The exported TypeScript `Agent` delegates to the execution API. Legacy Python local-agent endpoints reject requests when the durable registry is configured and are unavailable in production without Redis; `/query` is a separate, explicitly non-research chat endpoint.

A request gets an isolated execution context. Understanding and Plan are Pydantic validated. Plans reject duplicate IDs, missing prerequisites, cycles, unknown tools and more than ten tasks/five tools per task. DAG levels run in parallel with a five-task semaphore. Dependencies must complete before a child executes. Task retries reuse successful calls within that task. Provider adapters own bounded HTTP retry policies so task retries do not multiply exhausted provider retries.

There is no LangGraph integration to retain. A second workflow framework is unnecessary. Specialist roles (`market`, `fundamental`, `quantitative`, `risk`, `news`, `research`) are typed records of real task execution through the allowlisted financial tools, not separate unbounded conversational loops.

## Recovery boundary

A generation-fenced checkpoint stores understanding, completed plans, current plan, specialist results and model/schema versions. A replacement worker validates the snapshot and restores completed plan results. It reruns reflection and resumes at the next plan or synthesis boundary. An interrupted, uncommitted plan may repeat read-only provider/model calls; this is at-least-once execution, not an exactly-once billing guarantee. Checkpoints are not a substitute for fences. No stale generation may save progress or finalize.

A workflow ID combines durable job ID and generation. Each attempt has bounded model-call, reserved-output and input-character budgets, phase timeouts and the original Redis deadline. Model configuration changes fail visibly instead of silently switching the provider.
