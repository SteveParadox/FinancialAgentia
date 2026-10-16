# Recovery and diagnosis

Locate an execution by its UUID in authorized history/status. Inspect state, current_phase, failure_code, generation, attempt_count, timestamps, model_version and report limitations. Structured worker logs include worker ID/job/generation/error category without raw financial prompts. Persisted stream entries include attempt/generation at lifecycle boundaries. Provider/agent durations and model reservation metadata are separate from billed usage.

If the API is alive but not ready, inspect `/ready`: Redis unavailable, missing heartbeat, missing model configuration or unregistered financial tools are distinct conditions. Do not fix a storage outage by enabling an in-memory fallback. Restore Redis connectivity; workers resume polling and expired leases reconcile. On worker crash, keep the original deadline; another worker claims the next generation, reuses completed-plan checkpoints and repeats only uncommitted work. Three unsuccessful claims terminate rather than loop forever.

For FAILED/TIMED_OUT jobs, inspect the error before choosing an explicit authorized retry. Retry creates a linked successor and may make new billed provider/model calls. Incorrect credentials are not fixed by unlimited retries. MODEL_CONFIGURATION_CHANGED requires an operator to restore the intended model or the user to create an explicit new attempt.

After Redis disaster recovery, restore AOF/snapshot to an isolated instance and verify owner indexes, active jobs and retention. Run the additive history migration dry-run. Do not flush production data or manually change a terminal state to RUNNING. Cancel/retry through fenced APIs. Rollback requires stopping incompatible workers, preserving keys/backups and retaining schema-compatible readers; do not delete new history to satisfy old code.

For browser connectivity, reopen the job URL and authenticate again. SSE replays monotonic IDs and polling retrieves terminal truth if events were trimmed. Disconnecting a browser never cancels a job. Expired report TTLs cannot be recovered without an operator archive/backup; the UI does not pretend browser memory is durable storage.
