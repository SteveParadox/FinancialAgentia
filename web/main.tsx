import React, {
  Component,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  ApiError,
  Job,
  Model,
  ResearchApi,
  Scope,
  pretty,
  terminal,
  timestamp,
} from "./api";
import { ReportView } from "./report";
import { Platform } from "./platform";

class ErrorBoundary extends Component<
  { children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="login">
        <h1>Workspace could not render</h1>
        <p>Your saved research remains on the server. Reload to reconnect.</p>
        <button onClick={() => location.reload()}>Reload workspace</button>
      </main>
    ) : (
      this.props.children
    );
  }
}

function initialJob() {
  return /^#job=([0-9a-f-]{36})$/.exec(location.hash)?.[1] || "";
}
function Badge({ job }: { job: Job }) {
  const text =
    job.state === "SUCCEEDED"
      ? job.result?.status === "PARTIAL"
        ? "Partial report"
        : "Report saved"
      : pretty(job.state);
  return (
    <span
      className={`badge ${job.state === "SUCCEEDED" ? "good" : job.state === "FAILED" || job.state === "TIMED_OUT" ? "danger" : ""}`}
    >
      {text}
    </span>
  );
}

function Connect({
  onConnect,
}: {
  onConnect: (api: ResearchApi, model: Model) => void;
}) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function connect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    controller.current?.abort();
    controller.current = new AbortController();
    try {
      const api = new ResearchApi(token.trim());
      const model = await api.model(controller.current.signal);
      setToken("");
      onConnect(api, model);
    } catch (err) {
      if (!controller.current.signal.aborted)
        setError(
          err instanceof ApiError && err.status === 401
            ? "Your access token was rejected or has expired. Use a valid JWT from your identity provider."
            : "Unable to connect. Check that the backend is running and try again.",
        );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-shell">
      <div className="login-art">
        <Brand />
        <div>
          <span className="eyebrow">FINANCIAL INTELLIGENCE, WITH RECEIPTS</span>
          <h1>
            Research beyond
            <br />
            the headline.
          </h1>
          <p>
            A focused workspace for financial questions, reproducible
            calculations, and evidence you can inspect.
          </p>
          <div className="login-principles">
            <span>01 &nbsp; Ask a precise question</span>
            <span>02 &nbsp; Follow the research</span>
            <span>03 &nbsp; Inspect the evidence</span>
          </div>
        </div>
        <span className="caption">
          FinancialAgentia · Evidence-led research
        </span>
      </div>
      <main className="login">
        <span className="eyebrow">YOUR RESEARCH WORKSPACE</span>
        <h2>Connect to FinancialAgentia</h2>
        <p>
          Use a bearer access token supplied by your organization’s identity
          provider. Credentials stay in this tab’s memory.
        </p>
        <form onSubmit={connect}>
          <label htmlFor="access-token">Access token</label>
          <input
            id="access-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Paste your JWT access token"
          />
          <p className="caption">
            Leave blank only when authentication is disabled for local
            development. Shared backend API keys are intended for server and
            terminal clients.
          </p>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button disabled={busy} type="submit">
            {busy ? "Connecting…" : "Open workspace"} <span>↗</span>
          </button>
        </form>
        <p className="disclaimer">
          Research is informational and does not guarantee an investment
          outcome.
        </p>
      </main>
    </div>
  );
}
function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        F<span>↗</span>
      </span>
      <span>
        Financial<span className="brand-light">Agentia</span>
        <small>RESEARCH WORKSPACE</small>
      </span>
    </div>
  );
}

function Progress({ job }: { job: Job }) {
  const stages = [
    "Queued",
    "Planning",
    "Retrieving & analyzing",
    "Reviewing coverage",
    "Generating report",
    "Validating evidence",
    "Report saved",
  ];
  const phaseIndex: Record<string, number> = {
    understand: 1,
    plan: 1,
    execute: 2,
    reflect: 3,
    answer: 4,
    validate: 5,
  };
  const index =
    job.state === "SUCCEEDED"
      ? 6
      : job.state === "QUEUED"
        ? 0
        : phaseIndex[job.current_phase || ""] || 1;
  return (
    <ol className="timeline" aria-label="Research progress">
      {stages.map((stage, i) => (
        <li
          key={stage}
          className={i < index ? "done" : i === index ? "current" : ""}
        >
          <span aria-hidden="true">
            {i < index ? "✓" : String(i + 1).padStart(2, "0")}
          </span>
          <div>
            {stage}
            {i === index && !terminal.has(job.state) && (
              <small>Current stage</small>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function ResearchForm({
  busy,
  model,
  onSubmit,
}: {
  busy: boolean;
  model: Model;
  onSubmit: (query: string, scope: Scope) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [symbols, setSymbols] = useState("");
  const [category, setCategory] = useState<Scope["category"]>("company");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    const tickers = symbols
      .split(/[\s,]+/)
      .filter(Boolean)
      .map((item) => item.toUpperCase());
    if (
      !query.trim() ||
      tickers.some((item) => !/^[A-Z0-9][A-Z0-9.^_-]{0,19}$/.test(item)) ||
      tickers.length > 5 ||
      new Set(tickers).size !== tickers.length
    ) {
      setError(
        "Enter a question and up to five distinct valid ticker symbols.",
      );
      return;
    }
    if (category === "company" && tickers.length !== 1) {
      setError("Company analysis requires one ticker symbol.");
      return;
    }
    if (category === "comparison" && tickers.length < 2) {
      setError("Comparison requires at least two ticker symbols.");
      return;
    }
    if (
      category === "historical_prices" &&
      (!tickers.length || !start || !end || start >= end)
    ) {
      setError(
        "Historical price analysis requires instruments and an increasing date range.",
      );
      return;
    }
    await onSubmit(query.trim(), {
      category,
      symbols: tickers,
      ...(category === "historical_prices"
        ? { start_date: start, end_date: end }
        : {}),
    });
  }
  return (
    <section className="request-card">
      <div className="section-heading">
        <div>
          <span className="eyebrow">START WITH A QUESTION</span>
          <h2>What would you like to understand?</h2>
        </div>
        <span className="request-symbol" aria-hidden="true">
          ✳
        </span>
      </div>
      <form onSubmit={submit}>
        <label htmlFor="research-question">Research question</label>
        <textarea
          id="research-question"
          maxLength={10000}
          required
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Analyze Microsoft’s latest reported profitability, financial health and market performance. Cite the data and explain any limitations."
          rows={4}
        />
        <div className="form-grid">
          <div>
            <label htmlFor="research-type">Research type</label>
            <select
              id="research-type"
              value={category}
              onChange={(e) => setCategory(e.target.value as Scope["category"])}
            >
              <option value="company">Company analysis</option>
              <option value="comparison">Company comparison</option>
              <option value="historical_prices">
                Historical price analysis
              </option>
              <option value="general">General financial research</option>
            </select>
          </div>
          <div>
            <label htmlFor="symbols">Ticker symbols</label>
            <input
              id="symbols"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              placeholder="MSFT, AAPL, GOOGL"
              autoCapitalize="characters"
              maxLength={110}
            />
            <small>Confirm symbols on the provider’s supported exchange.</small>
          </div>
        </div>
        {category === "historical_prices" && (
          <>
            <div className="form-grid">
              <div>
                <label htmlFor="start-date">From</label>
                <input
                  id="start-date"
                  type="date"
                  required
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </div>
              <div>
                <label htmlFor="end-date">Through</label>
                <input
                  id="end-date"
                  type="date"
                  required
                  max={new Date().toISOString().slice(0, 10)}
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </div>
            </div>
            <p className="notice">
              Price history only. Adjustment coverage is disclosed in the
              result; point-in-time fundamental backtests are not certified.
            </p>
          </>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="form-footer">
          <span className="model-pill">
            <span className="connection-dot" />
            {model.provider} / {model.model}
            <small>Server configured</small>
          </span>
          <button type="submit" disabled={busy || !model.configured}>
            {busy ? "Submitting…" : "Start research"} <span>↗</span>
          </button>
        </div>
      </form>
      {!model.configured && (
        <p className="error">
          The server model is not configured. Ask the operator to configure
          OpenAI or Ollama.
        </p>
      )}
    </section>
  );
}

function Workspace({
  api,
  model,
  disconnect,
}: {
  api: ResearchApi;
  model: Model;
  disconnect: () => void;
}) {
  const [tab, setTab] = useState("workspace");
  const [selected, setSelected] = useState(initialJob);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("Connecting");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("newest");
  const [refresh, setRefresh] = useState(0);
  const pendingSubmission = useRef<{ payload: string; key: string } | null>(
    null,
  );
  const retryKeys = useRef(new Map<string, string>());
  const action = useRef<AbortController | null>(null);
  useEffect(() => () => action.current?.abort(), []);
  const fail = useCallback((err: unknown) => {
    if (err instanceof DOMException && err.name === "AbortError") return;
    setError(
      err instanceof ApiError
        ? err.message
        : "Connection or response validation failed. Reconnect to retrieve the saved server state.",
    );
    if (err instanceof ApiError && err.status === 401)
      setConnection("Credentials expired");
  }, []);
  const openJob = useCallback((id: string) => {
    location.hash = `job=${id}`;
    setSelected(id);
    setTab("workspace");
    setError("");
  }, []);
  useEffect(() => {
    const change = () => {
      setSelected(initialJob());
      setTab("workspace");
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .history(undefined, controller.signal)
      .then((data) => {
        setJobs(data.executions);
        setNext(data.next_cursor);
        setConnection("Connected");
      })
      .catch(fail)
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, refresh, fail]);
  useEffect(() => {
    if (!selected) {
      setJob(null);
      return;
    }
    setJob(null);
    const controller = new AbortController();
    let inFlight = false;
    let ended = false;
    let streamController: AbortController | undefined;
    const snapshot = async () => {
      if (inFlight || controller.signal.aborted || ended) return;
      inFlight = true;
      try {
        const data = await api.job(selected, controller.signal);
        if (controller.signal.aborted) return;
        setJob(data);
        setConnection("Connected");
        setError("");
        setJobs((previous) =>
          [
            data,
            ...previous.filter(
              (item) => item.execution_id !== data.execution_id,
            ),
          ].sort((a, b) => Number(b.created_at) - Number(a.created_at)),
        );
        if (terminal.has(data.state)) {
          ended = true;
          streamController?.abort();
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          fail(err);
          setConnection("Reconnecting");
        }
      } finally {
        inFlight = false;
      }
    };
    void snapshot();
    const interval = setInterval(() => void snapshot(), 2500); // Authoritative fallback, even if SSE is lost.
    const lastId = { value: "" };
    let retryTimer: ReturnType<typeof setTimeout>;
    const stream = async () => {
      if (ended || controller.signal.aborted) return;
      streamController = new AbortController();
      try {
        await api.watch(
          selected,
          () => void snapshot(),
          streamController.signal,
          lastId,
        );
      } catch {
        if (!ended && !controller.signal.aborted)
          setConnection("Polling · stream reconnecting");
      }
      if (!ended && !controller.signal.aborted)
        retryTimer = setTimeout(() => void stream(), 3000);
    };
    void stream();
    return () => {
      controller.abort();
      streamController?.abort();
      clearInterval(interval);
      clearTimeout(retryTimer);
    };
  }, [api, selected, refresh, fail]);

  async function submit(query: string, scope: Scope) {
    if (busy) return;
    setBusy(true);
    setError("");
    action.current?.abort();
    action.current = new AbortController();
    const payload = JSON.stringify({ query, scope });
    if (pendingSubmission.current?.payload !== payload)
      pendingSubmission.current = { payload, key: crypto.randomUUID() };
    try {
      const result = await api.submit(
        query,
        scope,
        pendingSubmission.current.key,
        action.current.signal,
      );
      pendingSubmission.current = null;
      openJob(result.execution_id);
      setRefresh((x) => x + 1);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    if (!job || busy) return;
    setBusy(true);
    action.current?.abort();
    action.current = new AbortController();
    try {
      await api.cancel(job.execution_id, action.current.signal);
      setRefresh((x) => x + 1);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }
  async function retry(target: Job) {
    if (busy) return;
    if (target.retry_execution_id) {
      openJob(target.retry_execution_id);
      return;
    }
    setBusy(true);
    action.current?.abort();
    action.current = new AbortController();
    const key =
      retryKeys.current.get(target.execution_id) || crypto.randomUUID();
    retryKeys.current.set(target.execution_id, key);
    try {
      const result = await api.retry(
        target.execution_id,
        key,
        action.current.signal,
      );
      openJob(result.execution_id);
      setRefresh((x) => x + 1);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }
  async function loadMore() {
    if (!next || loading) return;
    setLoading(true);
    action.current?.abort();
    action.current = new AbortController();
    try {
      const data = await api.history(next, action.current.signal);
      setJobs((previous) => [
        ...previous,
        ...data.executions.filter(
          (row) =>
            !previous.some((item) => item.execution_id === row.execution_id),
        ),
      ]);
      setNext(data.next_cursor);
    } catch (err) {
      fail(err);
    } finally {
      setLoading(false);
    }
  }
  const active = jobs.filter((item) => !terminal.has(item.state));
  const visible = useMemo(
    () =>
      jobs
        .filter(
          (item) =>
            (tab !== "active" || !terminal.has(item.state)) &&
            (tab !== "reports" || item.state === "SUCCEEDED") &&
            (filter === "all" || item.state === filter) &&
            (item.query || item.execution_id)
              .toLowerCase()
              .includes(search.toLowerCase()),
        )
        .sort((a, b) =>
          sort === "oldest"
            ? Number(a.created_at) - Number(b.created_at)
            : Number(b.created_at) - Number(a.created_at),
        ),
    [jobs, tab, filter, search, sort],
  );
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to research
      </a>
      <aside className="sidebar">
        <Brand />
        <nav aria-label="Main navigation">
          {[
            ["workspace", "◈", "Workspace"],
            ["active", "◷", "Active research"],
            ["history", "▤", "Research history"],
            ["reports", "▧", "Saved reports"],
            ["financial", "↗", "Financial data"],
            ["portfolios", "◫", "Portfolios"],
            ["memory", "◎", "Research context"],
            ["monitoring", "◉", "Monitoring"],
            ["collaboration", "♧", "Team workspaces"],
            ["settings", "⚙", "Connection"],
          ].map(([id, icon, label]) => (
            <button
              key={id}
              className={tab === id ? "nav-item selected" : "nav-item"}
              onClick={() => setTab(id!)}
              aria-current={tab === id ? "page" : undefined}
            >
              <span aria-hidden="true">{icon}</span>
              {label}
              {id === "active" && active.length > 0 && (
                <small>{active.length}</small>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="eyebrow">EVIDENCE FIRST</span>
            <p>
              Every figure has a source.
              <br />
              Every limitation stays visible.
            </p>
          </div>
          <button className="nav-item" onClick={disconnect}>
            ↪ &nbsp; Disconnect
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span>
            Research /{" "}
            <strong>{tab === "workspace" ? "Workspace" : pretty(tab)}</strong>
          </span>
          <div>
            <span className="connection-dot" />
            <span role="status">{connection}</span>
            <span className="avatar" aria-label="Authenticated workspace">
              FA
            </span>
          </div>
        </header>
        <main id="main-content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                FINANCIALAGENTIA / RESEARCH INTELLIGENCE
              </span>
              <h1>
                {{
                  financial: "Inspect the financial record.",
                  portfolios: "Your portfolio, in context.",
                  memory: "Build on previous research.",
                  monitoring: "Stay informed, on your schedule.",
                  collaboration: "Research together, share deliberately.",
                }[tab] ||
                  (tab === "workspace"
                    ? "Your next insight starts here."
                    : tab === "settings"
                      ? "Workspace connection"
                      : tab === "active"
                        ? "Research in motion."
                        : tab === "reports"
                          ? "Your evidence, preserved."
                          : "A record of your research.")}
              </h1>
              <p>
                {tab === "workspace"
                  ? "Ask better questions. Follow the analysis. Understand the evidence."
                  : "Saved server state remains authoritative across tabs, refreshes and reconnects."}
              </p>
            </div>
            {tab !== "workspace" && (
              <button
                onClick={() => {
                  setSelected("");
                  location.hash = "";
                  setTab("workspace");
                }}
              >
                + New research
              </button>
            )}
          </div>
          {error && (
            <div className="error error-banner" role="alert">
              {error}
              <button
                className="text-button"
                onClick={() => setRefresh((x) => x + 1)}
              >
                Try refresh
              </button>
              <button className="text-button" onClick={disconnect}>
                Reconnect credentials
              </button>
            </div>
          )}
          {[
            "financial",
            "portfolios",
            "memory",
            "monitoring",
            "collaboration",
          ].includes(tab) && (
            <Platform
              key={tab}
              api={api}
              section={tab}
              openJob={(id) => {
                setSelected(id);
                location.hash = "job=" + id;
                setTab("workspace");
                setRefresh((x) => x + 1);
              }}
            />
          )}
          {tab === "workspace" && (
            <>
              <ResearchForm model={model} busy={busy} onSubmit={submit} />
              {selected && !job && !error && (
                <div className="skeleton" role="status">
                  Loading persisted research…
                </div>
              )}
              {job && (
                <section className="job-panel">
                  <div className="section-heading">
                    <div>
                      <span className="eyebrow">RESEARCH EXECUTION</span>
                      <h2>{job.query || "Saved research"}</h2>
                      <p>
                        {timestamp(job.created_at)} · Attempt{" "}
                        {job.attempt_count || "0"} · {job.model_version}
                      </p>
                    </div>
                    <Badge job={job} />
                  </div>
                  <Progress job={job} />
                  <div className="job-actions">
                    {!terminal.has(job.state) && (
                      <button
                        className="secondary"
                        disabled={busy || job.state === "CANCEL_REQUESTED"}
                        onClick={cancel}
                      >
                        {job.state === "CANCEL_REQUESTED"
                          ? "Cancellation requested…"
                          : "Cancel research"}
                      </button>
                    )}
                    {["FAILED", "TIMED_OUT"].includes(job.state) && (
                      <button disabled={busy} onClick={() => void retry(job)}>
                        {job.retry_execution_id
                          ? "Open retry"
                          : "Retry research"}
                      </button>
                    )}
                    <button
                      className="text-button"
                      onClick={() => setRefresh((x) => x + 1)}
                    >
                      Refresh state
                    </button>
                    <code>{job.execution_id}</code>
                  </div>
                  {job.failure_code && (
                    <p className="notice">
                      {pretty(job.failure_code)}.{" "}
                      {job.state === "FAILED" || job.state === "TIMED_OUT"
                        ? "A retry creates a linked new job and preserves this attempt."
                        : ""}
                    </p>
                  )}
                  {job.result && (
                    <ReportView key={job.execution_id} report={job.result} />
                  )}
                  {job.state === "CANCELLED" && (
                    <p className="notice">
                      Research was cancelled. No late result can complete this
                      job.
                    </p>
                  )}
                </section>
              )}
              {!selected && (
                <section className="getting-started">
                  <span className="eyebrow">
                    A CLEARER PATH TO UNDERSTANDING
                  </span>
                  <div>
                    <article>
                      <span>01</span>
                      <h3>Define the question</h3>
                      <p>
                        Choose instruments and a research scope that matches
                        your decision.
                      </p>
                    </article>
                    <article>
                      <span>02</span>
                      <h3>Follow the workflow</h3>
                      <p>
                        Watch planning, retrieval, analysis and evidence review
                        as they occur.
                      </p>
                    </article>
                    <article>
                      <span>03</span>
                      <h3>Check the foundations</h3>
                      <p>
                        Inspect dates, calculation inputs and source limitations
                        before drawing conclusions.
                      </p>
                    </article>
                  </div>
                </section>
              )}
            </>
          )}
          {["active", "history", "reports"].includes(tab) && (
            <section className="history-panel">
              <div className="history-tools">
                <label className="search-label">
                  Search loaded research
                  <input
                    type="search"
                    placeholder="Question or job ID"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
                <label>
                  Status
                  <select
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  >
                    <option value="all">All statuses</option>
                    {[
                      "QUEUED",
                      "RUNNING",
                      "SUCCEEDED",
                      "FAILED",
                      "CANCELLED",
                      "TIMED_OUT",
                    ].map((status) => (
                      <option key={status}>{status}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Order
                  <select
                    value={sort}
                    onChange={(e) => setSort(e.target.value)}
                  >
                    <option value="newest">Newest first</option>
                    <option value="oldest">Oldest first</option>
                  </select>
                </label>
                <button
                  className="secondary"
                  onClick={() => setRefresh((x) => x + 1)}
                >
                  Refresh
                </button>
              </div>
              {loading && (
                <div role="status" className="skeleton">
                  Loading research history…
                </div>
              )}
              {!loading && !visible.length && (
                <div className="empty-state">
                  <span>◈</span>
                  <h2>No research in this view</h2>
                  <p>
                    Start a research question or adjust the filters. History is
                    retrieved from the backend.
                  </p>
                </div>
              )}
              <div className="history-list">
                {visible.map((item) => (
                  <article key={item.execution_id}>
                    <div className="history-item-icon" aria-hidden="true">
                      ↗
                    </div>
                    <div>
                      <button
                        className="history-title"
                        onClick={() => openJob(item.execution_id)}
                      >
                        {item.query ||
                          `Research ${item.execution_id.slice(0, 8)}`}
                      </button>
                      <p>
                        {timestamp(item.created_at)} · {item.model_version}
                      </p>
                    </div>
                    <Badge job={item} />
                    <button
                      className="text-button"
                      onClick={() => openJob(item.execution_id)}
                    >
                      Open →
                    </button>
                  </article>
                ))}
              </div>
              {next && (
                <button
                  disabled={loading}
                  className="secondary"
                  onClick={() => void loadMore()}
                >
                  Load more research
                </button>
              )}
              <p className="caption">
                Search, status and ordering apply to loaded pages. Retention
                follows the server policy; this is not an indefinite archive.
              </p>
            </section>
          )}
          {tab === "settings" && (
            <section className="settings-card">
              <h2>Server-managed research configuration</h2>
              <dl>
                <dt>Model provider</dt>
                <dd>{model.provider}</dd>
                <dt>Configured model</dt>
                <dd>{model.model}</dd>
                <dt>Credentials configured</dt>
                <dd>
                  {model.configured
                    ? "Yes — live connectivity not asserted"
                    : "No"}
                </dd>
                <dt>Financial data</dt>
                <dd>Financial Datasets, when configured by the operator</dd>
                <dt>Browser authentication</dt>
                <dd>
                  Bearer token in memory. Reconnect after refresh or expiration.
                </dd>
                <dt>Job ownership</dt>
                <dd>
                  Enforced by the server on status, events, results, retries and
                  cancellation.
                </dd>
              </dl>
              <p className="notice">
                Model and financial-provider keys remain on the server.
                Available data and licensed coverage depend on operator
                configuration. No provider fallback occurs silently.
              </p>
              <p>
                Configure <code>LLM_MODEL</code> with an OpenAI model ID or an
                Ollama-prefixed model, then restart API and workers together.
                See the repository’s deployment instructions.
              </p>
              <button className="secondary" onClick={disconnect}>
                Disconnect this workspace
              </button>
            </section>
          )}
          <footer className="footer">
            <span>FinancialAgentia</span>
            <span>Source-backed research · Informational use only</span>
          </footer>
        </main>
      </div>
    </div>
  );
}

function App() {
  const [session, setSession] = useState<{
    api: ResearchApi;
    model: Model;
  } | null>(null);
  return session ? (
    <Workspace
      api={session.api}
      model={session.model}
      disconnect={() => setSession(null)}
    />
  ) : (
    <Connect onConnect={(api, model) => setSession({ api, model })} />
  );
}
createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
);
