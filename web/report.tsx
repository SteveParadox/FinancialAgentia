import React, { useState } from "react";
import type { ResearchReport } from "../src/services/research-contract";
import { pretty, safeUrl, timestamp } from "./api";

type Evidence = ResearchReport["evidence"][number];

export function exportReport(report: ResearchReport, format: "json" | "md") {
  const content =
    format === "json"
      ? JSON.stringify(report, null, 2)
      : [
          "# FinancialAgentia research",
          `Research ID: ${report.execution_id}`,
          `As researched: ${report.researched_at}`,
          `Verification: ${report.status}`,
          report.answer,
          "\n## Evidence",
          ...report.evidence.map(
            (row) =>
              `- ${row.evidence_id}: ${row.symbol} ${row.metric} = ${row.value} ${row.unit || row.currency || "unit unspecified"}; as of ${row.period_end || row.data_as_of || "unavailable"}; freshness ${row.freshness || "unverified"}; source ${row.source_id}; inputs ${row.input_evidence_ids.join(", ") || "none"}; method ${row.calculation_method || "provider reported"}`,
          ),
          "\n## Sources",
          ...report.sources.map(
            (source) =>
              `- ${source.source_id}: ${source.source_url || "URL unavailable"}; retrieved ${source.retrieved_at}; observed ${source.data_as_of || "unavailable"}`,
          ),
          "\nInformational research. No investment outcome is guaranteed.",
        ].join("\n\n");
  const url = URL.createObjectURL(
    new Blob([content], {
      type:
        format === "json" ? "application/json" : "text/markdown;charset=utf-8",
    }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = `financialagentia-${report.execution_id}.${format}`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function PriceChart({ rows }: { rows: Evidence[] }) {
  const points = rows
    .filter((row) => row.metric === "close" && row.data_as_of)
    .sort((a, b) => a.data_as_of!.localeCompare(b.data_as_of!));
  if (
    points.length < 2 ||
    new Set(points.map((row) => row.symbol)).size !== 1 ||
    new Set(points.map((row) => row.currency)).size !== 1
  )
    return null;
  const values = points.map((row) => Number(row.value));
  const min = Math.min(...values),
    max = Math.max(...values),
    spread = max - min || 1;
  const dates = points.map((row) => new Date(row.data_as_of!).getTime());
  const first = dates[0]!,
    span = dates[dates.length - 1]! - first;
  if (!span || !dates.every(Number.isFinite)) return null;
  const line = values
    .map(
      (value, index) =>
        `${42 + ((dates[index]! - first) / span) * 616},${160 - ((value - min) / spread) * 130}`,
    )
    .join(" ");
  return (
    <div className="chart-panel">
      <div className="section-heading">
        <h3>{points[0]!.symbol} historical close</h3>
        <span>
          {points[0]!.currency || "Currency unavailable"} · provider
          observations
        </span>
      </div>
      <svg
        viewBox="0 0 700 205"
        role="img"
        aria-label={`Historical close from ${points[0]!.data_as_of} to ${points[points.length - 1]!.data_as_of}. Minimum ${min}, maximum ${max}.`}
      >
        {[30, 95, 160].map((y) => (
          <line key={y} x1="42" x2="658" y1={y} y2={y} className="grid-line" />
        ))}
        <polyline
          points={line}
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
        />
        <text x="42" y="195">
          {points[0]!.data_as_of!.slice(0, 10)}
        </text>
        <text x="658" y="195" textAnchor="end">
          {points[points.length - 1]!.data_as_of!.slice(0, 10)}
        </text>
        <text x="42" y="20">
          {max.toLocaleString()}
        </text>
        <text x="42" y="180">
          {min.toLocaleString()}
        </text>
      </svg>
      <p className="caption">
        Adjustment policy: review linked sources. Chart values are recorded
        closes, not live prices. All plotted values are available in the
        evidence table.
      </p>
    </div>
  );
}

export function ReportView({ report }: { report: ResearchReport }) {
  const [focus, setFocus] = useState<Evidence | null>(null);
  const [evidencePage, setEvidencePage] = useState(0);
  const evidenceRows = report.evidence.slice(
    evidencePage * 50,
    (evidencePage + 1) * 50,
  );
  const sources = new Map(
    report.sources.map((source) => [source.source_id, source]),
  );
  const symbols = [...new Set(report.evidence.map((row) => row.symbol))];
  const calculated = report.evidence.filter(
    (row) => row.evidence_type === "CALCULATED",
  );
  return (
    <article className="report" aria-label="Saved research report">
      <div className="section-heading report-heading">
        <div>
          <span className="eyebrow">PERSISTED RESEARCH REPORT</span>
          <h2>Findings & evidence</h2>
          <p>
            {timestamp(report.researched_at)} · Schema v{report.schema_version}
          </p>
        </div>
        <span
          className={`badge ${report.status === "COMPLETE" ? "good" : "warning"}`}
        >
          {report.status === "COMPLETE"
            ? "Validated findings"
            : pretty(report.status)}
        </span>
      </div>
      <div className="report-actions">
        <button
          className="secondary"
          onClick={() => exportReport(report, "md")}
        >
          ↓ Markdown
        </button>
        <button
          className="secondary"
          onClick={() => exportReport(report, "json")}
        >
          ↓ JSON
        </button>
        <button className="secondary" onClick={() => window.print()}>
          Print / save PDF
        </button>
      </div>
      <div className="metric-strip">
        <div>
          <strong>{symbols.join(", ") || "—"}</strong>
          <span>Instruments covered</span>
        </div>
        <div>
          <strong>{report.evidence.length}</strong>
          <span>Evidence records</span>
        </div>
        <div>
          <strong>{calculated.length}</strong>
          <span>Calculated metrics</span>
        </div>
        <div>
          <strong>{report.sources.length}</strong>
          <span>Retrieved sources</span>
        </div>
      </div>
      {report.limitations.length > 0 && (
        <aside className="notice" aria-label="Research limitations">
          <strong>Coverage & limitations</strong>
          <ul>
            {report.limitations.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </aside>
      )}
      {report.claims.length > 0 && (
        <section>
          <h3>Verified numerical findings</h3>
          <ul className="findings">
            {report.claims.map((claim) => (
              <li key={claim.claim_id}>
                <p>{claim.statement}</p>
                <div>
                  {claim.supporting_evidence_ids.map((id) => (
                    <button
                      className="citation"
                      key={id}
                      onClick={() => {
                        setFocus(
                          report.evidence.find(
                            (row) => row.evidence_id === id,
                          ) || null,
                        );
                        document
                          .getElementById("evidence-inspector")
                          ?.scrollIntoView({ block: "center" });
                      }}
                    >
                      {id}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      {symbols.length > 1 && (
        <section>
          <h3>Company comparison</h3>
          <p className="caption">
            Each cell shows its own reporting date and unit. Different periods
            or currencies are not ranked as directly comparable.
          </p>
          <div className="table-scroll">
            <table aria-label="Company comparison">
              <thead>
                <tr>
                  <th>Metric</th>
                  {symbols.map((symbol) => (
                    <th key={symbol}>{symbol}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  "revenue",
                  "net_income",
                  "net_profit_margin",
                  "free_cash_flow",
                  "total_debt",
                  "current_ratio",
                ].map((metric) => (
                  <tr key={metric}>
                    <td>{pretty(metric)}</td>
                    {symbols.map((symbol) => {
                      const rows = report.evidence.filter(
                        (row) => row.symbol === symbol && row.metric === metric,
                      );
                      const observations = [
                        ...new Map(
                          rows.map((row) => [
                            `${row.value}:${row.period_end || row.data_as_of}:${row.currency}:${row.unit}`,
                            row,
                          ]),
                        ).values(),
                      ];
                      const row = observations[0];
                      return (
                        <td key={symbol}>
                          {observations.length === 1 && row ? (
                            <>
                              <span className="number">
                                {row.value}{" "}
                                {row.unit || row.currency || "unit unspecified"}
                              </span>
                              <span className="table-sub">
                                {row.period_end ||
                                  row.data_as_of?.slice(0, 10) ||
                                  "Date unavailable"}
                              </span>
                              <button
                                className="text-button"
                                onClick={() => setFocus(row)}
                              >
                                Inspect inputs
                              </button>
                            </>
                          ) : observations.length > 1 ? (
                            "Multiple observations — inspect evidence"
                          ) : (
                            "Unavailable"
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {symbols.map((symbol) => (
        <PriceChart
          key={symbol}
          rows={report.evidence.filter((row) => row.symbol === symbol)}
        />
      ))}
      {report.evidence.length > 0 && (
        <section>
          <div className="section-heading">
            <h3>Financial observations</h3>
            <span>
              Exact provider or calculated values · select a row to inspect
            </span>
          </div>
          <div className="table-scroll">
            <table>
              <caption className="sr-only">
                Financial evidence with units, dates, freshness and sources
              </caption>
              <thead>
                <tr>
                  <th>Instrument / metric</th>
                  <th>Value</th>
                  <th>Unit</th>
                  <th>Period / as of</th>
                  <th>Freshness</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {evidenceRows.map((row) => (
                  <tr key={row.evidence_id}>
                    <td>
                      <strong>{row.symbol}</strong>
                      <span className="table-sub">{pretty(row.metric)}</span>
                    </td>
                    <td className="number">{row.value}</td>
                    <td>{row.unit || row.currency || "Unspecified"}</td>
                    <td>
                      {row.period_end ||
                        row.data_as_of?.slice(0, 10) ||
                        "Unavailable"}
                    </td>
                    <td>
                      <span
                        className={`badge ${row.freshness === "stale" ? "warning" : ""}`}
                      >
                        {pretty(row.freshness || "unverified")}
                      </span>
                    </td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => setFocus(row)}
                      >
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.evidence.length > 50 && (
            <div className="pagination">
              <button
                className="secondary"
                disabled={evidencePage === 0}
                onClick={() => setEvidencePage((page) => page - 1)}
              >
                Previous observations
              </button>
              <span>
                Page {evidencePage + 1} of{" "}
                {Math.ceil(report.evidence.length / 50)}
              </span>
              <button
                className="secondary"
                disabled={(evidencePage + 1) * 50 >= report.evidence.length}
                onClick={() => setEvidencePage((page) => page + 1)}
              >
                Next observations
              </button>
            </div>
          )}
        </section>
      )}
      <section id="evidence-inspector" className="inspector" aria-live="polite">
        <h3>Evidence inspector</h3>
        {focus ? (
          <>
            <p>
              <strong>
                {focus.symbol} · {pretty(focus.metric)}
              </strong>{" "}
              = {focus.value} {focus.unit || focus.currency}
            </p>
            <dl>
              <dt>Evidence ID</dt>
              <dd>{focus.evidence_id}</dd>
              <dt>Original field</dt>
              <dd>{focus.source_field || "Unavailable"}</dd>
              <dt>Filing date</dt>
              <dd>{focus.filing_date || "Unavailable"}</dd>
              <dt>Original filing</dt>
              <dd>
                {safeUrl(focus.source_document_url) ? (
                  <a
                    href={safeUrl(focus.source_document_url)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Provider-reported filing document
                  </a>
                ) : (
                  "Unavailable"
                )}
              </dd>
              <dt>Source</dt>
              <dd>{focus.source_id}</dd>
              <dt>As of</dt>
              <dd>
                {focus.period_start ? focus.period_start + " to " : ""}
                {focus.period_end || focus.data_as_of || "Unavailable"}
              </dd>
              <dt>Method</dt>
              <dd>
                {focus.calculation_method || "Provider-reported observation"}
              </dd>
              <dt>Calculation version</dt>
              <dd>{focus.calculation_version || "Not applicable"}</dd>
              <dt>Input evidence</dt>
              <dd>
                {focus.input_evidence_ids.join(", ") || "No derived inputs"}
              </dd>
              <dt>Quality flags</dt>
              <dd>
                {focus.quality_flags?.join("; ") ||
                  "No additional flags recorded"}
              </dd>
              <dt>Retrieved</dt>
              <dd>{timestamp(sources.get(focus.source_id)?.retrieved_at)}</dd>
            </dl>
          </>
        ) : (
          <p>Select an observation or citation to inspect its provenance.</p>
        )}
      </section>
      {report.sources.length > 0 && (
        <section>
          <h3>Evidence & sources</h3>
          <p className="caption">
            Source provenance is recorded. One data provider does not constitute
            independent cross-source verification.
          </p>
          <div className="sources">
            {report.sources.map((source) => (
              <div className="source-card" key={source.source_id}>
                <div className="source-icon">↗</div>
                <div>
                  <strong>{source.headline || source.provider}</strong>
                  <p>
                    {safeUrl(source.source_url) ? (
                      <a
                        href={safeUrl(source.source_url)}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {source.headline
                          ? source.publisher || "Read publisher report"
                          : "View recorded provider endpoint"}
                      </a>
                    ) : (
                      "Source URL unavailable"
                    )}
                  </p>
                  <p className="caption">
                    As of {source.data_as_of || "unavailable"} · Retrieved{" "}
                    {timestamp(source.retrieved_at)}
                    <br />
                    Cache: {source.cache_status || "unspecified"} · Adjustments:{" "}
                    {source.adjustment_status || "unspecified"}
                  </p>
                  <code>{source.source_id}</code>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      <details className="saved-answer">
        <summary>Saved report text</summary>
        <pre>{report.answer}</pre>
      </details>
      <section>
        <h3>Execution metadata</h3>
        <dl>
          <dt>Job</dt>
          <dd>
            <code>{report.execution_id}</code>
          </dd>
          <dt>Validation</dt>
          <dd>{pretty(report.status)}</dd>
          <dt>Research timestamp</dt>
          <dd>{timestamp(report.researched_at)}</dd>
          <dt>Configured model</dt>
          <dd>
            {String(
              (report.model_metadata as Record<string, unknown> | undefined)
                ?.configured_model || "Not recorded in this report",
            )}
          </dd>
        </dl>
        {!!(report.model_metadata as Record<string, unknown> | undefined)
          ?.verification && (
          <details>
            <summary>Deterministic verification record</summary>
            <pre>
              {JSON.stringify(
                (report.model_metadata as Record<string, unknown>).verification,
                null,
                2,
              )}
            </pre>
          </details>
        )}
        {report.agent_executions.length > 0 && (
          <div className="agent-list">
            {report.agent_executions.map((agent) => (
              <div key={agent.execution_id}>
                <strong>{pretty(agent.agent_id)}</strong>
                <span className="badge">{agent.status}</span>
                <p>{agent.tools.join(", ")}</p>
                <span className="caption">
                  {agent.duration_seconds?.toFixed(2) || "—"} s{" "}
                  {agent.error_code && `· ${agent.error_code}`}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
      <p className="disclaimer">
        Informational financial research. Figures may be delayed or incomplete.
        Estimated risk measures and historical returns do not guarantee future
        outcomes.
      </p>
    </article>
  );
}
