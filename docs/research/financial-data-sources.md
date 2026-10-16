# Financial sources and limitations

Production uses Financial Datasets, configured server-side with `FINANCIAL_DATASETS_API_KEY`. No synthetic fallback or second independent licensed source is configured. HTTP fixtures exist exclusively under tests. Live response access/entitlements were not validated in this environment.

The adapter uses documented canonical endpoints for snapshots, daily/intraday prices, metrics, statements, company facts, news and filings. It normalizes the documented cash-flow field `net_cash_flow_from_operations` to `operating_cash_flow` and preserves original field/filing metadata. Provider URLs, retrieval times, reporting dates, currency, fiscal periods and calculation lineage remain attached. Pagination is disclosed as partial rather than silently treated as complete. Credentials never appear in source URLs.

Provider responses reject wrong tickers, malformed structures, invalid currencies/numbers, future observations/publications and out-of-range requested history. Redirects are disabled. Requests have finite deadlines, three attempts for transient HTTP/transport errors, bounded Retry-After waits, a tool circuit breaker, and a small validated/coalesced cache. Invalid credentials and missing coverage fail explicitly. Cache age does not establish observation freshness.

Freshness: historical closes/derived historical metrics are `historical`; fiscal statements are `reported_period` or `stale` by configurable reporting-age policy; snapshots are `unverified` unless `stale`. No live/delayed entitlement is inferred. Default snapshot age policy is four days to tolerate ordinary weekends; no exchange-holiday calendar or live-market guarantee is claimed. Latest filing availability, independent cross-source verification, corporate-action adjustment policy and point-in-time restatements remain unverified.

## Operator licensing gate

Check the account agreement before enabling access, redistribution or report downloads. Commercial and redistribution rights vary by plan/agreement; a technical API response is not permission to redistribute raw records. The workspace includes exact normalized observations and downloadable analysis, so the operator must verify that intended users, retention and exports are licensed. Do not enable a public/shared deployment or bulk-data service on the assumption a personal subscription permits it.

Official references checked 2026-10-16:

- https://docs.financialdatasets.ai/api/financials/all-financial-statements
- https://docs.financialdatasets.ai/api/prices/historical
- https://docs.financialdatasets.ai/api/prices/snapshot
- https://www.financialdatasets.ai/terms-of-use

No claim is made about current subscription price, a specific account's market delay, or commercial authorization.
