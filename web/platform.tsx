import React, { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { ResearchApi, BASE, timestamp } from "./api";
import { ReportView } from "./report";
import { formatMoney } from "./money";
import { ResearchReportSchema } from "../src/services/research-contract";

const ROOT = "/api/v1/platform";
const Document = z.object({
  schema_version: z.literal(1),
  id: z.string().uuid(),
  owner: z.string(),
  version: z.number().int().positive(),
  created_at: z.number(),
  updated_at: z.number(),
  expires_at: z.number().nullable(),
  data: z.record(z.string(), z.unknown()),
});
type Doc = z.infer<typeof Document>;
type Data = Record<string, unknown>;
const Documents = z.object({ items: z.array(Document) });
const Evidence = z
  .object({
    symbol: z.string(),
    metric: z.string(),
    value: z.string(),
    source_id: z.string(),
    currency: z.string().nullable(),
    unit: z.string().nullable(),
    data_as_of: z.string().nullable(),
    freshness: z.string(),
    period_start: z.string().nullable().optional(),
    period_end: z.string().nullable().optional(),
  })
  .passthrough();
const DataResult = z.object({
  evidence: z.array(Evidence),
  sources: z.array(
    z
      .object({
        source_id: z.string(),
        provider: z.string(),
        source_url: z.string().nullable(),
        retrieved_at: z.string(),
      })
      .passthrough(),
  ),
  limitations: z.array(z.string()),
  coverage: z.string(),
});
function text(value: unknown): string {
  return value === null || value === undefined
    ? "Unavailable"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}
function rows(value: unknown): Data[] {
  return z.array(z.record(z.string(), z.unknown())).safeParse(value).data || [];
}
function strings(value: unknown): string[] {
  return z.array(z.string()).safeParse(value).data || [];
}
function jsonBody(value: unknown) {
  return {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  };
}
function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const id = React.useId();
  return (
    <div className="platform-field">
      <label htmlFor={id}>{label}</label>
      {React.isValidElement<{ id?: string }>(children)
        ? React.cloneElement(children, { id })
        : children}
    </div>
  );
}
function Money({ value, currency }: { value: unknown; currency: string }) {
  return <span>{formatMoney(value, currency)}</span>;
}
function Table({
  records,
  columns,
}: {
  records: Data[];
  columns: [string, string][];
}) {
  return records.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map(([key, title]) => (
              <th key={key}>{title}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {records.slice(0, 200).map((record, i) => (
            <tr key={i}>
              {columns.map(([key]) => (
                <td key={key}>{text(record[key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {records.length > 200 && (
        <p>Showing the first 200 records. Narrow the requested interval.</p>
      )}
    </div>
  ) : (
    <p className="empty-inline">No records yet.</p>
  );
}

export function Platform({
  api,
  section,
  openJob,
}: {
  api: ResearchApi;
  section: string;
  openJob: (id: string) => void;
}) {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [documents, setDocuments] = useState<Doc[]>([]);
  const [notifications, setNotifications] = useState<Doc[]>([]);
  const [selected, setSelected] = useState<Doc | null>(null);
  const [analytics, setAnalytics] = useState<Data | null>(null);
  const [risk, setRisk] = useState<Data | null>(null);
  const [preview, setPreview] = useState<Data | null>(null);
  const [publication, setPublication] = useState<Doc | null>(null);
  const [providers, setProviders] = useState<Data[]>([]);
  const [financial, setFinancial] = useState<z.infer<typeof DataResult> | null>(
    null,
  );
  const [principal, setPrincipal] = useState("");
  const [invitation, setInvitation] = useState("");
  const [symbol, setSymbol] = useState("MSFT");
  const [dataset, setDataset] = useState("quote");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [name, setName] = useState("");
  const [rename, setRename] = useState("");
  const [publicationTitle, setPublicationTitle] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [benchmark, setBenchmark] = useState("");
  const [csv, setCsv] = useState("");
  const [entryType, setEntryType] = useState("DEPOSIT");
  const [entryDate, setEntryDate] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [quantity, setQuantity] = useState("1");
  const [price, setPrice] = useState("");
  const [amount, setAmount] = useState("");
  const [fee, setFee] = useState("0");
  const [entryId, setEntryId] = useState<string>(crypto.randomUUID());
  const [question, setQuestion] = useState("");
  const [jobId, setJobId] = useState("");
  const [ruleKind, setRuleKind] = useState("price_above");
  const [threshold, setThreshold] = useState("");
  const [interval, setInterval] = useState("3600");
  const [recipient, setRecipient] = useState("");
  const [role, setRole] = useState("viewer");
  const [comment, setComment] = useState("");
  const [annotationSection, setAnnotationSection] = useState("report");
  const [shock, setShock] = useState("-0.2");
  const lifecycle = useRef(new AbortController());
  useEffect(() => () => lifecycle.current.abort(), []);
  const read = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const response = await api.request(ROOT + path, {
        ...init,
        signal: lifecycle.current.signal,
      });
      return response.status === 204 ? null : response.json();
    },
    [api],
  );
  async function act(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      if (!lifecycle.current.signal.aborted)
        setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      if (!lifecycle.current.signal.aborted) setBusy(false);
    }
  }
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const who = z
          .object({ principal: z.string() })
          .parse(await read("/me"));
        if (!active) return;
        setPrincipal(who.principal);
        if (section === "financial") {
          const result = z
            .object({ providers: z.array(z.record(z.string(), z.unknown())) })
            .parse(await read("/providers"));
          if (active) setProviders(result.providers);
        } else if (section === "monitoring") {
          const result = Documents.extend({
            notifications: z.array(Document),
          }).parse(await read("/monitoring"));
          if (active) {
            setDocuments(result.items);
            setNotifications(result.notifications);
          }
        } else {
          const endpoint =
            section === "portfolios"
              ? "/portfolios"
              : section === "memory"
                ? "/memories"
                : "/workspaces";
          const result = Documents.parse(await read(endpoint));
          if (active) setDocuments(result.items);
        }
      } catch (e) {
        if (active && !lifecycle.current.signal.aborted)
          setError(
            e instanceof Error ? e.message : "Unable to load saved records",
          );
      }
    })();
    return () => {
      active = false;
    };
  }, [read, section, refresh]);
  async function loadPortfolio(id: string) {
    setPreview(null);
    setAnalytics(null);
    setRisk(null);
    const record = Document.parse(await read(`/portfolios/${id}`));
    setSelected(record);
    setRename(text(record.data.name));
  }
  async function loadWorkspace(id: string) {
    setPublication(null);
    setSelected(Document.parse(await read(`/workspaces/${id}`)));
  }
  async function loadPublication(id: string) {
    setPublication(Document.parse(await read(`/publications/${id}`)));
  }
  async function submitEntry(csvMode: boolean, previewMode: boolean) {
    if (!selected) return;
    const trade = ["BUY", "SELL", "SPLIT"].includes(entryType);
    const entries = [
      {
        transaction_id: entryId,
        type: entryType,
        date: entryDate,
        currency: selected.data.currency,
        symbol:
          trade || entryType === "DIVIDEND"
            ? symbol.trim().toUpperCase()
            : null,
        quantity: trade ? quantity : "0",
        price: entryType === "SPLIT" ? "0" : trade ? price : "0",
        amount: trade ? "0" : amount,
        fee: ["BUY", "SELL"].includes(entryType) ? fee : "0",
      },
    ];
    const result = await read(`/portfolios/${selected.id}/transactions`, {
      method: "POST",
      ...jsonBody({
        version: selected.version,
        preview: previewMode,
        ...(csvMode ? { csv } : { entries }),
      }),
    });
    if (previewMode)
      setPreview(z.record(z.string(), z.unknown()).parse(result));
    else {
      setSelected(Document.parse(result));
      setPreview(null);
      setAnalytics(null);
      setRisk(null);
      setCsv("");
      setEntryId(crypto.randomUUID());
      setNotice("Ledger saved. Identical transaction IDs are deduplicated.");
    }
  }
  async function download(format: string) {
    if (!publication) return;
    const response = await api.request(
      `${ROOT}/publications/${publication.id}/export?format=${format}`,
      { signal: lifecycle.current.signal },
    );
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `research-${publication.id}.${format === "markdown" ? "md" : format}`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const members = selected
    ? z.record(z.string(), z.string()).safeParse(selected.data.members).data ||
      {}
    : {};
  const canEdit =
    selected && ["owner", "admin", "editor"].includes(members[principal] || "");
  const canManage =
    selected && ["owner", "admin"].includes(members[principal] || "");
  const savedReport = publication
    ? ResearchReportSchema.safeParse(publication.data.report).data
    : null;
  return (
    <div className="platform-area" aria-busy={busy}>
      {error && (
        <div className="error" role="alert">
          {error}
          <button
            className="text-button"
            onClick={() => {
              setError("");
              setRefresh((x) => x + 1);
            }}
          >
            Reload records
          </button>
        </div>
      )}
      {notice && (
        <p className="platform-notice" role="status">
          {notice}
        </p>
      )}
      <div className="section-heading">
        <div>
          <span className="eyebrow">CONNECTED FINANCIAL INTELLIGENCE</span>
          <h2>
            {
              {
                financial: "Financial data",
                portfolios: "Your portfolios",
                memory: "Research context",
                monitoring: "Monitoring & updates",
                collaboration: "Team workspaces",
              }[section]
            }
          </h2>
        </div>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => setRefresh((x) => x + 1)}
        >
          Refresh
        </button>
      </div>

      {section === "financial" && (
        <>
          <div className="platform-grid">
            {providers.map((provider) => (
              <article className="platform-card" key={text(provider.id)}>
                <h3>{text(provider.id)}</h3>
                <span className="badge">
                  {provider.configured
                    ? "Configured · live access unverified"
                    : "Not configured"}
                </span>
                <p>{strings(provider.capabilities).join(" · ")}</p>
                {rows(provider.instruments).map((item) => (
                  <p key={text(item.issuer_id)}>
                    {text(item.symbol)} · {text(item.issuer_id)} ·{" "}
                    {text(item.exchange)}
                  </p>
                ))}
              </article>
            ))}
          </div>
          <form
            className="platform-card platform-form"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                const params = new URLSearchParams({
                  symbol: symbol.trim(),
                  dataset,
                });
                if (start)
                  params.set(dataset === "sec" ? "as_of" : "start", start);
                if (end && dataset === "history") params.set("end", end);
                setFinancial(
                  DataResult.parse(await read("/financial-data?" + params)),
                );
              });
            }}
          >
            <Field label="Symbol">
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                required
                maxLength={20}
              />
            </Field>
            <Field label="Dataset">
              <select
                value={dataset}
                onChange={(e) => setDataset(e.target.value)}
              >
                <option value="quote">Dated quote · Financial Datasets</option>
                <option value="statements">
                  Annual statements · Financial Datasets
                </option>
                <option value="history">
                  Historical prices · Financial Datasets
                </option>
                <option value="sec">Reported filings · SEC</option>
              </select>
            </Field>
            {["history", "sec"].includes(dataset) && (
              <Field
                label={
                  dataset === "sec" ? "Filing cutoff (optional)" : "Start date"
                }
              >
                <input
                  type="date"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                  required={dataset === "history"}
                />
              </Field>
            )}
            {dataset === "history" && (
              <Field label="End date">
                <input
                  type="date"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                  required
                />
              </Field>
            )}
            <button disabled={busy}>Retrieve financial evidence</button>
          </form>
          {financial && (
            <section className="platform-card">
              <h3>Source-backed observations</h3>
              <p>
                Coverage: {financial.coverage}. Delayed, historical and
                unverified observations are labeled individually.
              </p>
              <Table
                records={financial.evidence}
                columns={[
                  ["symbol", "Instrument"],
                  ["metric", "Metric"],
                  ["value", "Value"],
                  ["currency", "Currency"],
                  ["unit", "Units"],
                  ["period_start", "Period start"],
                  ["data_as_of", "As of"],
                  ["freshness", "Freshness"],
                ]}
              />
              {financial.sources.map((source) => (
                <p key={source.source_id}>
                  {source.provider} · Retrieved {source.retrieved_at} ·{" "}
                  <a
                    href={source.source_url || undefined}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Source record
                  </a>
                </p>
              ))}
              {financial.limitations.map((item) => (
                <p key={item}>{item}</p>
              ))}
            </section>
          )}
        </>
      )}

      {section === "portfolios" && (
        <>
          <form
            className="platform-card platform-form"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                const record = Document.parse(
                  await read("/portfolios", {
                    method: "POST",
                    ...jsonBody({
                      name,
                      currency,
                      benchmark: benchmark.trim() || null,
                    }),
                  }),
                );
                setSelected(record);
                setName("");
                setRefresh((x) => x + 1);
              });
            }}
          >
            <Field label="Portfolio name">
              <input
                required
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <Field label="Base currency">
              <input
                required
                pattern="[A-Z]{3}"
                maxLength={3}
                value={currency}
                onChange={(e) => setCurrency(e.target.value.toUpperCase())}
              />
            </Field>
            <Field label="Benchmark (optional)">
              <input
                value={benchmark}
                maxLength={20}
                onChange={(e) => setBenchmark(e.target.value)}
                placeholder="SPY"
              />
            </Field>
            <button disabled={busy}>Create portfolio</button>
          </form>
          <div className="platform-tabs">
            {documents.map((record) => (
              <button
                className={
                  selected?.id === record.id ? "selected" : "secondary"
                }
                disabled={busy}
                key={record.id}
                onClick={() => void act(() => loadPortfolio(record.id))}
              >
                {text(record.data.name)} · {text(record.data.currency)}
              </button>
            ))}
          </div>
          {!documents.length && (
            <p>No portfolios yet. Add recorded deposits before purchases.</p>
          )}
          {selected && (
            <>
              <section className="platform-card">
                <h3>
                  {text(selected.data.name)} · Ledger version {selected.version}
                </h3>
                <form
                  className="platform-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      setSelected(
                        Document.parse(
                          await read(`/portfolios/${selected.id}`, {
                            method: "PATCH",
                            ...jsonBody({
                              version: selected.version,
                              name: rename,
                            }),
                          }),
                        ),
                      );
                      setRefresh((x) => x + 1);
                    });
                  }}
                >
                  <Field label="New portfolio name">
                    <input
                      required
                      maxLength={120}
                      value={rename}
                      onChange={(e) => setRename(e.target.value)}
                    />
                  </Field>
                  <button className="secondary" disabled={busy}>
                    Rename portfolio
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => {
                      if (
                        window.confirm(
                          "Delete this portfolio, ledger and recorded snapshots?",
                        )
                      )
                        void act(async () => {
                          await read(
                            `/portfolios/${selected.id}?version=${selected.version}`,
                            { method: "DELETE" },
                          );
                          setSelected(null);
                          setRefresh((x) => x + 1);
                        });
                    }}
                  >
                    Delete portfolio
                  </button>
                </form>
                <p>
                  Read-only investment intelligence. Long-only equities/ETFs,
                  explicit splits and dividends, one base currency. Transactions
                  remain authoritative.
                </p>
                <form
                  className="platform-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(() => submitEntry(false, false));
                  }}
                >
                  <Field label="Transaction type">
                    <select
                      value={entryType}
                      onChange={(e) => setEntryType(e.target.value)}
                    >
                      {[
                        "DEPOSIT",
                        "BUY",
                        "SELL",
                        "DIVIDEND",
                        "WITHDRAWAL",
                        "SPLIT",
                      ].map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Transaction date">
                    <input
                      type="date"
                      required
                      value={entryDate}
                      onChange={(e) => setEntryDate(e.target.value)}
                    />
                  </Field>
                  <Field label="Transaction ID">
                    <input
                      required
                      value={entryId}
                      onChange={(e) => setEntryId(e.target.value)}
                    />
                  </Field>
                  {["BUY", "SELL", "SPLIT", "DIVIDEND"].includes(entryType) && (
                    <Field label="Holding symbol">
                      <input
                        required
                        value={symbol}
                        maxLength={20}
                        onChange={(e) => setSymbol(e.target.value)}
                      />
                    </Field>
                  )}
                  {["BUY", "SELL", "SPLIT"].includes(entryType) ? (
                    <>
                      <Field
                        label={
                          entryType === "SPLIT"
                            ? "Split multiplier"
                            : "Quantity"
                        }
                      >
                        <input
                          type="number"
                          min="0.00000001"
                          step="any"
                          required
                          value={quantity}
                          onChange={(e) => setQuantity(e.target.value)}
                        />
                      </Field>
                      {entryType !== "SPLIT" && (
                        <>
                          <Field label="Acquisition / sale price">
                            <input
                              type="number"
                              min="0.00000001"
                              step="any"
                              required
                              value={price}
                              onChange={(e) => setPrice(e.target.value)}
                            />
                          </Field>
                          <Field label="Trade fee">
                            <input
                              type="number"
                              min="0"
                              step="any"
                              value={fee}
                              onChange={(e) => setFee(e.target.value)}
                              required
                            />
                          </Field>
                        </>
                      )}
                    </>
                  ) : (
                    <Field label="Cash amount">
                      <input
                        type="number"
                        min="0.00000001"
                        step="any"
                        required
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                      />
                    </Field>
                  )}
                  <button disabled={busy}>Save transaction</button>
                </form>
                <details>
                  <summary>Import a validated CSV ledger</summary>
                  <p>
                    Required headers:
                    transaction_id,date,type,symbol,quantity,price,amount,fee,currency.
                    Blank numeric fields mean zero. Maximum 1,000 rows per
                    import.
                  </p>
                  <Field label="CSV ledger">
                    <textarea
                      rows={6}
                      value={csv}
                      maxLength={500000}
                      onChange={(e) => {
                        setCsv(e.target.value);
                        setPreview(null);
                      }}
                      placeholder="transaction_id,date,type,symbol,quantity,price,amount,fee,currency"
                    />
                  </Field>
                  <button
                    disabled={busy || !csv.trim()}
                    className="secondary"
                    onClick={() => void act(() => submitEntry(true, true))}
                  >
                    Validate & preview CSV
                  </button>
                  {preview && (
                    <>
                      <Table
                        records={rows(preview.rows)}
                        columns={[
                          ["date", "Date"],
                          ["type", "Type"],
                          ["symbol", "Symbol"],
                          ["quantity", "Quantity"],
                          ["price", "Price"],
                          ["amount", "Amount"],
                        ]}
                      />
                      <button
                        disabled={busy}
                        onClick={() => void act(() => submitEntry(true, false))}
                      >
                        Commit validated import
                      </button>
                    </>
                  )}
                </details>
                <Table
                  records={rows(selected.data.entries)}
                  columns={[
                    ["date", "Date"],
                    ["type", "Type"],
                    ["symbol", "Instrument"],
                    ["quantity", "Quantity"],
                    ["price", "Price"],
                    ["amount", "Cash"],
                    ["currency", "Currency"],
                  ]}
                />
              </section>
              <section className="platform-card">
                <h3>Valuation & concentration</h3>
                <button
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      setAnalytics(
                        z
                          .record(z.string(), z.unknown())
                          .parse(
                            await read(`/portfolios/${selected.id}/analytics`),
                          ),
                      );
                    })
                  }
                >
                  Retrieve latest available valuation
                </button>
                {analytics && (
                  <>
                    <div className="platform-grid">
                      {[
                        ["Market value", "market_value"],
                        ["Profit / loss", "profit_loss"],
                        ["Cash", "cash"],
                        ["Realized profit / loss", "realized_profit_loss"],
                        ["Dividend income", "dividend_income"],
                      ].map(([label, key]) => (
                        <article className="metric-card" key={key}>
                          <span>{label}</span>
                          <strong>
                            <Money
                              value={analytics[key!]}
                              currency={text(selected.data.currency)}
                            />
                          </strong>
                        </article>
                      ))}
                    </div>
                    <Table
                      records={rows(analytics.holdings).map((row) => ({
                        ...row,
                        as_of: z
                          .record(z.string(), z.unknown())
                          .safeParse(row.quote).data?.as_of,
                      }))}
                      columns={[
                        ["symbol", "Holding"],
                        ["quantity", "Quantity"],
                        ["cost_basis", "Cost basis"],
                        ["market_value", "Market value"],
                        ["unrealized_profit_loss", "Unrealized P/L"],
                        ["weight", "Weight fraction"],
                        ["as_of", "Quote as of"],
                      ]}
                    />
                    {strings(analytics.missing_prices).length > 0 && (
                      <p className="error">
                        Missing, stale or currency-incompatible quotes:{" "}
                        {strings(analytics.missing_prices).join(", ")}. Total
                        valuation is unavailable.
                      </p>
                    )}
                    <p>
                      Annualized money-weighted return (XIRR):{" "}
                      {analytics.money_weighted_return === null
                        ? "Unavailable"
                        : `${(Number(analytics.money_weighted_return) * 100).toFixed(2)}%`}
                    </p>
                    <p>{text(analytics.return_methodology)}</p>
                    {strings(analytics.warnings).map((value) => (
                      <p key={value}>{value}</p>
                    ))}
                    {strings(analytics.limitations).map((value) => (
                      <p key={value}>{value}</p>
                    ))}
                  </>
                )}
                <form
                  className="platform-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      setAnalytics(
                        z.record(z.string(), z.unknown()).parse(
                          await read(`/portfolios/${selected.id}/scenarios`, {
                            method: "POST",
                            ...jsonBody({
                              shocks: {
                                [symbol.trim().toUpperCase()]: shock,
                              },
                            }),
                          }),
                        ),
                      );
                    });
                  }}
                >
                  <Field label="Scenario holding">
                    <input
                      value={symbol}
                      onChange={(e) => setSymbol(e.target.value)}
                      required
                    />
                  </Field>
                  <Field label="Hypothetical return fraction">
                    <input
                      type="number"
                      min="-1"
                      max="10"
                      step="0.01"
                      required
                      value={shock}
                      onChange={(e) => setShock(e.target.value)}
                    />
                  </Field>
                  <button disabled={busy} className="secondary">
                    Evaluate hypothetical scenario
                  </button>
                </form>
                {analytics?.scenario_value !== undefined && (
                  <p>
                    Hypothetical value:{" "}
                    <Money
                      value={analytics.scenario_value}
                      currency={text(selected.data.currency)}
                    />
                    . A scenario is an assumption, not a forecast.
                  </p>
                )}
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const result = z
                        .object({ execution_id: z.string().uuid() })
                        .parse(
                          await read(`/portfolios/${selected.id}/research`, {
                            method: "POST",
                            headers: { "Idempotency-Key": crypto.randomUUID() },
                          }),
                        );
                      openJob(result.execution_id);
                    })
                  }
                >
                  Research portfolio holdings
                </button>
              </section>
              <section className="platform-card">
                <h3>Recorded portfolio valuations</h3>
                <p>
                  Snapshots preserve their ledger version and input quote dates.
                  No missing history is invented.
                </p>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const result = z.object({ portfolio: Document }).parse(
                        await read(`/portfolios/${selected.id}/valuations`, {
                          method: "POST",
                        }),
                      );
                      setSelected(result.portfolio);
                      setNotice(
                        "Valuation snapshot persisted with source quote dates.",
                      );
                    })
                  }
                >
                  Save valuation snapshot
                </button>
                <Table
                  records={rows(selected.data.valuations)}
                  columns={[
                    ["captured_at", "Captured (UTC epoch)"],
                    ["market_value", "Market value"],
                    ["currency", "Currency"],
                    ["ledger_version", "Ledger version"],
                  ]}
                />
                <h3>Historical risk proxy</h3>
                <p>
                  Holds today’s quantities and cash constant. This is not
                  transaction-based portfolio performance. At least 31 aligned
                  daily closes are required.
                </p>
                <form
                  className="platform-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      setRisk(
                        z
                          .record(z.string(), z.unknown())
                          .parse(
                            await read(
                              `/portfolios/${selected.id}/risk?${new URLSearchParams({ start, end })}`,
                            ),
                          ),
                      );
                    });
                  }}
                >
                  <Field label="Risk start">
                    <input
                      type="date"
                      required
                      value={start}
                      onChange={(e) => setStart(e.target.value)}
                    />
                  </Field>
                  <Field label="Risk end">
                    <input
                      type="date"
                      required
                      value={end}
                      onChange={(e) => setEnd(e.target.value)}
                    />
                  </Field>
                  <button disabled={busy}>Calculate risk proxy</button>
                </form>
                {risk && (
                  <>
                    <p>{text(risk.label)}</p>
                    <Table
                      records={Object.entries(
                        z
                          .record(z.string(), z.unknown())
                          .parse(risk.statistics),
                      ).map(([metric, value]) => ({ metric, value }))}
                      columns={[
                        ["metric", "Metric"],
                        ["value", "Value"],
                      ]}
                    />
                    {strings(risk.limitations).map((value) => (
                      <p key={value}>{value}</p>
                    ))}
                  </>
                )}
              </section>
            </>
          )}
        </>
      )}

      {section === "memory" && (
        <>
          <p>
            Context is private and opt-in for each follow-up. Prior figures
            remain dated context; the worker retrieves current evidence again.
            Records expire automatically under the configured retention policy.
            Deletion removes the saved context, while original reports remain in
            research history.
          </p>
          <form
            className="platform-card platform-form"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await read("/memories", {
                  method: "POST",
                  ...jsonBody({ execution_id: jobId, label: name }),
                });
                setRefresh((x) => x + 1);
                setNotice("Research context saved.");
              });
            }}
          >
            <Field label="Completed research job ID">
              <input
                required
                value={jobId}
                onChange={(e) => setJobId(e.target.value)}
              />
            </Field>
            <Field label="Context label">
              <input
                required
                maxLength={120}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <button disabled={busy}>Save research context</button>
          </form>
          <Field label="Follow-up question">
            <textarea
              value={question}
              maxLength={10000}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Update the previous analysis with the latest available reported results."
            />
          </Field>
          <div className="platform-grid">
            {documents.map((record) => (
              <article className="platform-card" key={record.id}>
                <h3>{text(record.data.label)}</h3>
                <p>{text(record.data.query)}</p>
                <p>
                  Researched {text(record.data.researched_at)} · Context expires{" "}
                  {timestamp(String(record.expires_at))}
                </p>
                <details>
                  <summary>Inspect retained context</summary>
                  <p className="preserve-lines">{text(record.data.summary)}</p>
                </details>
                <button
                  disabled={busy || !question.trim()}
                  onClick={() =>
                    void act(async () => {
                      const result = z
                        .object({ execution_id: z.string().uuid() })
                        .parse(
                          await (
                            await api.request(BASE, {
                              method: "POST",
                              ...jsonBody({
                                query: question,
                                context_id: record.id,
                              }),
                              headers: {
                                "Content-Type": "application/json",
                                "Idempotency-Key": crypto.randomUUID(),
                              },
                            })
                          ).json(),
                        );
                      openJob(result.execution_id);
                    })
                  }
                >
                  Continue with refreshed evidence
                </button>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      await read(
                        `/memories/${record.id}?version=${record.version}`,
                        { method: "DELETE" },
                      );
                      setRefresh((x) => x + 1);
                    })
                  }
                >
                  Delete saved context
                </button>
              </article>
            ))}
          </div>
          {!documents.length && <p>No optional research context retained.</p>}
        </>
      )}

      {section === "monitoring" && (
        <>
          <p>
            Periodic UTC evaluation, not continuous real-time monitoring. Price
            alerts trigger on the first satisfied observation and subsequent
            threshold crossings, subject to cooldown. Missed intervals are
            skipped. Notifications stay in this application.
          </p>
          <form
            className="platform-card platform-form"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await read("/monitoring", {
                  method: "POST",
                  ...jsonBody({
                    name,
                    kind: ruleKind,
                    interval_seconds: Number(interval),
                    cooldown_seconds: Number(interval),
                    currency,
                    ...(ruleKind === "research"
                      ? { query: question }
                      : { symbol, threshold }),
                  }),
                });
                setRefresh((x) => x + 1);
                setName("");
              });
            }}
          >
            <Field label="Rule name">
              <input
                required
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <Field label="Monitoring type">
              <select
                value={ruleKind}
                onChange={(e) => {
                  setRuleKind(e.target.value);
                  if (e.target.value === "research" && Number(interval) < 3600)
                    setInterval("3600");
                }}
              >
                <option value="price_above">Price above threshold</option>
                <option value="price_below">Price below threshold</option>
                <option value="research">Recurring research</option>
              </select>
            </Field>
            {ruleKind === "research" ? (
              <Field label="Recurring research question">
                <textarea
                  required
                  value={question}
                  maxLength={10000}
                  onChange={(e) => setQuestion(e.target.value)}
                />
              </Field>
            ) : (
              <>
                <Field label="Alert symbol">
                  <input
                    required
                    value={symbol}
                    maxLength={20}
                    onChange={(e) => setSymbol(e.target.value)}
                  />
                </Field>
                <Field label="Threshold">
                  <input
                    type="number"
                    min="0.00000001"
                    step="any"
                    required
                    value={threshold}
                    onChange={(e) => setThreshold(e.target.value)}
                  />
                </Field>
                <Field label="Alert currency">
                  <input
                    required
                    pattern="[A-Z]{3}"
                    value={currency}
                    maxLength={3}
                    onChange={(e) => setCurrency(e.target.value.toUpperCase())}
                  />
                </Field>
              </>
            )}
            <Field label="Evaluation interval / cooldown">
              <select
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
              >
                {ruleKind !== "research" && (
                  <option value="300">Every 5 minutes</option>
                )}
                <option value="3600">Hourly</option>
                <option value="86400">Daily</option>
                <option value="604800">Weekly</option>
              </select>
            </Field>
            <button disabled={busy}>Create monitoring rule</button>
          </form>
          <div className="platform-grid">
            {documents.map((record) => (
              <article className="platform-card" key={record.id}>
                <h3>{text(record.data.name)}</h3>
                <span className="badge">
                  {record.data.enabled ? "Enabled" : "Paused"}
                </span>
                <p>
                  {text(record.data.kind)} · Every{" "}
                  {Number(record.data.interval_seconds) / 60} minutes
                </p>
                <p>
                  Last evaluation:{" "}
                  {record.data.last_evaluation
                    ? timestamp(String(record.data.last_evaluation))
                    : "Not evaluated"}
                </p>
                <p>
                  Observed value: {text(record.data.last_observed)} · Data as of{" "}
                  {text(record.data.observed_at)}
                </p>
                {!!record.data.error && (
                  <p className="error">
                    Evaluation issue: {text(record.data.error)}. No successful
                    observation recorded.
                  </p>
                )}
                <button
                  disabled={busy}
                  className="secondary"
                  onClick={() =>
                    void act(async () => {
                      await read(
                        `/monitoring/${record.data.kind === "research" ? "schedule" : "alert"}/${record.id}`,
                        {
                          method: "POST",
                          ...jsonBody({
                            version: record.version,
                            enabled: !record.data.enabled,
                          }),
                        },
                      );
                      setRefresh((x) => x + 1);
                    })
                  }
                >
                  {record.data.enabled ? "Pause" : "Resume"}
                </button>
                <button
                  disabled={busy}
                  className="text-button"
                  onClick={() =>
                    void act(async () => {
                      await read(
                        `/monitoring/${record.data.kind === "research" ? "schedule" : "alert"}/${record.id}?version=${record.version}`,
                        { method: "DELETE" },
                      );
                      setRefresh((x) => x + 1);
                    })
                  }
                >
                  Delete rule
                </button>
              </article>
            ))}
          </div>
          <section className="platform-card">
            <h3>In-app notifications</h3>
            {notifications.map((record) => (
              <article className="platform-notification" key={record.id}>
                <strong>{text(record.data.rule_name)}</strong>
                <p>
                  {text(record.data.message)} ·{" "}
                  {timestamp(String(record.created_at))}
                </p>
                {!!record.data.value && (
                  <p>
                    {text(record.data.symbol)} {text(record.data.value)}{" "}
                    {text(record.data.currency)} · As of{" "}
                    {text(record.data.observed_at)} ·{" "}
                    {text(record.data.freshness)}
                  </p>
                )}
                {typeof record.data.execution_id === "string" && (
                  <button
                    className="secondary"
                    onClick={() => openJob(text(record.data.execution_id))}
                  >
                    Open scheduled research
                  </button>
                )}
                <button
                  className="secondary"
                  disabled={busy || !!record.data.acknowledged}
                  onClick={() =>
                    void act(async () => {
                      await read(
                        `/notifications/${record.id}/acknowledge?version=${record.version}`,
                        { method: "POST" },
                      );
                      setRefresh((x) => x + 1);
                    })
                  }
                >
                  {record.data.acknowledged ? "Acknowledged" : "Acknowledge"}
                </button>
              </article>
            ))}
            {!notifications.length && <p>No notifications yet.</p>}
          </section>
        </>
      )}

      {section === "collaboration" && (
        <>
          <p>
            Your invitation account ID:{" "}
            <code className="break-all">{principal}</code>. Workspace access
            applies only to explicitly shared reports. Personal research, saved
            context and portfolios remain private.
          </p>
          <div className="platform-grid">
            <form
              className="platform-card platform-form"
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  setSelected(
                    Document.parse(
                      await read("/workspaces", {
                        method: "POST",
                        ...jsonBody({ name }),
                      }),
                    ),
                  );
                  setRefresh((x) => x + 1);
                  setName("");
                });
              }}
            >
              <Field label="Workspace name">
                <input
                  required
                  maxLength={120}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <button disabled={busy}>Create workspace</button>
            </form>
            <form
              className="platform-card platform-form"
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  setSelected(
                    Document.parse(
                      await read(`/invitations/${invitation}/accept`, {
                        method: "POST",
                      }),
                    ),
                  );
                  setRefresh((x) => x + 1);
                  setInvitation("");
                });
              }}
            >
              <Field label="Invitation ID">
                <input
                  required
                  value={invitation}
                  onChange={(e) => setInvitation(e.target.value)}
                />
              </Field>
              <button disabled={busy}>Accept invitation</button>
              <button
                type="button"
                className="secondary"
                disabled={busy || !invitation.trim()}
                onClick={() =>
                  void act(async () => {
                    const record = Document.parse(
                      await read(`/invitations/${invitation}`),
                    );
                    await read(
                      `/invitations/${invitation}/revoke?version=${record.version}`,
                      { method: "POST" },
                    );
                    setNotice("Issued invitation revoked.");
                  })
                }
              >
                Revoke issued invitation
              </button>
            </form>
          </div>
          <div className="platform-tabs">
            {documents.map((record) => (
              <button
                disabled={busy}
                className="secondary"
                key={record.id}
                onClick={() => void act(() => loadWorkspace(record.id))}
              >
                {text(record.data.name)}
              </button>
            ))}
          </div>
          {selected && (
            <>
              <section className="platform-card">
                <h3>{text(selected.data.name)}</h3>
                <div className="platform-tabs">
                  {members[principal] !== "owner" && (
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await read(`/workspaces/${selected.id}/leave`, {
                            method: "POST",
                            ...jsonBody({ version: selected.version }),
                          });
                          setSelected(null);
                          setPublication(null);
                          setRefresh((x) => x + 1);
                        })
                      }
                    >
                      Leave workspace
                    </button>
                  )}
                  {members[principal] === "owner" && (
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => {
                        if (
                          window.confirm(
                            "Delete this workspace and its shared report snapshots? Personal research remains in its original history.",
                          )
                        )
                          void act(async () => {
                            await read(
                              `/workspaces/${selected.id}?version=${selected.version}`,
                              { method: "DELETE" },
                            );
                            setSelected(null);
                            setPublication(null);
                            setRefresh((x) => x + 1);
                          });
                      }}
                    >
                      Delete workspace
                    </button>
                  )}
                </div>
                <p>
                  Workspace version {selected.version} · Your role:{" "}
                  {members[principal]}
                </p>
                <Table
                  records={Object.entries(members).map(([account, role]) => ({
                    account,
                    role,
                  }))}
                  columns={[
                    ["account", "Account ID"],
                    ["role", "Role"],
                  ]}
                />
                {canManage && (
                  <>
                    <form
                      className="platform-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void act(async () => {
                          const invite = Document.parse(
                            await read(
                              `/workspaces/${selected.id}/invitations`,
                              {
                                method: "POST",
                                ...jsonBody({ recipient, role }),
                              },
                            ),
                          );
                          setNotice(
                            `Invitation ID: ${invite.id}. Expires in seven days. Share it with the selected account through your chosen channel.`,
                          );
                        });
                      }}
                    >
                      <Field label="Recipient account ID">
                        <input
                          required
                          pattern="[a-f0-9]{64}"
                          value={recipient}
                          onChange={(e) => setRecipient(e.target.value)}
                        />
                      </Field>
                      <Field label="Granted role">
                        <select
                          value={role}
                          onChange={(e) => setRole(e.target.value)}
                        >
                          <option value="viewer">Viewer</option>
                          <option value="editor">Editor</option>
                          {members[principal] === "owner" && (
                            <option value="admin">Admin</option>
                          )}
                        </select>
                      </Field>
                      <button disabled={busy}>Create invitation</button>
                    </form>
                    <div className="platform-tabs">
                      {Object.entries(members)
                        .filter(([, role]) => role !== "owner")
                        .map(([member]) => (
                          <button
                            disabled={busy}
                            className="secondary"
                            key={member}
                            onClick={() =>
                              void act(async () => {
                                setSelected(
                                  Document.parse(
                                    await read(
                                      `/workspaces/${selected.id}/members`,
                                      {
                                        method: "POST",
                                        ...jsonBody({
                                          version: selected.version,
                                          member,
                                          role: "remove",
                                        }),
                                      },
                                    ),
                                  ),
                                );
                                setPublication(null);
                                setRefresh((x) => x + 1);
                              })
                            }
                          >
                            Remove account {member.slice(0, 10)}…
                          </button>
                        ))}
                    </div>
                  </>
                )}
              </section>
              {canEdit && (
                <form
                  className="platform-card platform-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      setPublication(
                        Document.parse(
                          await read(
                            `/workspaces/${selected.id}/publications`,
                            {
                              method: "POST",
                              ...jsonBody({
                                execution_id: jobId,
                                title: publicationTitle,
                              }),
                            },
                          ),
                        ),
                      );
                      await loadWorkspace(selected.id);
                      setRefresh((x) => x + 1);
                    });
                  }}
                >
                  <Field label="Owned report job ID">
                    <input
                      required
                      value={jobId}
                      onChange={(e) => setJobId(e.target.value)}
                    />
                  </Field>
                  <Field label="Shared report title">
                    <input
                      required
                      maxLength={120}
                      value={publicationTitle}
                      onChange={(e) => setPublicationTitle(e.target.value)}
                    />
                  </Field>
                  <button disabled={busy}>Share a saved report snapshot</button>
                </form>
              )}
              <section className="platform-card">
                <h3>Shared research library</h3>
                <div className="platform-tabs">
                  {strings(selected.data.publications).map((id) => (
                    <button
                      className="secondary"
                      disabled={busy}
                      key={id}
                      onClick={() => void act(() => loadPublication(id))}
                    >
                      Open report {id.slice(0, 8)}…
                    </button>
                  ))}
                </div>
                {!strings(selected.data.publications).length && (
                  <p>No reports shared in this workspace.</p>
                )}
              </section>
              {publication && (
                <section className="platform-card">
                  <h3>{text(publication.data.title)}</h3>
                  <p>
                    Version {publication.version} · Review:{" "}
                    {text(publication.data.review_status)}
                  </p>
                  <div className="platform-tabs">
                    {["json", "markdown", "csv"].map((format) => (
                      <button
                        className="secondary"
                        disabled={busy}
                        key={format}
                        onClick={() => void act(() => download(format))}
                      >
                        Export {format.toUpperCase()}
                      </button>
                    ))}
                    <button
                      className="secondary"
                      onClick={() => window.print()}
                    >
                      Print / save PDF
                    </button>
                    {canManage && (
                      <button
                        disabled={busy}
                        className="secondary"
                        onClick={() =>
                          void act(async () => {
                            await read(
                              `/workspaces/${selected.id}/publications/${publication.id}?version=${selected.version}`,
                              { method: "DELETE" },
                            );
                            await loadWorkspace(selected.id);
                          })
                        }
                      >
                        Revoke report sharing
                      </button>
                    )}
                  </div>
                  {savedReport && <ReportView report={savedReport} />}
                  <h4>Annotations & review</h4>
                  <Table
                    records={rows(publication.data.comments)}
                    columns={[
                      ["section", "Section"],
                      ["text", "Comment"],
                      ["author", "Author"],
                      ["at", "Timestamp"],
                    ]}
                  />
                  {canEdit && (
                    <>
                      <form
                        className="platform-form"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void act(async () => {
                            setPublication(
                              Document.parse(
                                await read(
                                  `/publications/${publication.id}/comments`,
                                  {
                                    method: "POST",
                                    ...jsonBody({
                                      version: publication.version,
                                      text: comment,
                                      section: annotationSection,
                                    }),
                                  },
                                ),
                              ),
                            );
                            setComment("");
                          });
                        }}
                      >
                        <Field label="Report section">
                          <input
                            required
                            maxLength={80}
                            value={annotationSection}
                            onChange={(e) =>
                              setAnnotationSection(e.target.value)
                            }
                          />
                        </Field>
                        <Field label="Research comment">
                          <textarea
                            required
                            maxLength={3000}
                            value={comment}
                            onChange={(e) => setComment(e.target.value)}
                          />
                        </Field>
                        <button disabled={busy}>Add annotation</button>
                      </form>
                      <div className="platform-tabs">
                        {["draft", "in_review", "reviewed"].map((status) => (
                          <button
                            className="secondary"
                            disabled={busy}
                            key={status}
                            onClick={() =>
                              void act(async () => {
                                setPublication(
                                  Document.parse(
                                    await read(
                                      `/publications/${publication.id}/review`,
                                      {
                                        method: "POST",
                                        ...jsonBody({
                                          version: publication.version,
                                          status,
                                        }),
                                      },
                                    ),
                                  ),
                                );
                              })
                            }
                          >
                            Mark {status.replaceAll("_", " ")}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                  <details>
                    <summary>Version history & attribution</summary>
                    <Table
                      records={rows(publication.data.history)}
                      columns={[
                        ["version", "Version"],
                        ["type", "Change"],
                        ["author", "Author"],
                        ["at", "Timestamp"],
                      ]}
                    />
                  </details>
                </section>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
