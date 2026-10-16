# Using the FinancialAgentia browser workspace

Prerequisite: follow the [quick start](../getting-started/quick-start.md) and [authentication guide](../installation/configuration.md#authentication-and-browser-access). Open `http://127.0.0.1:8000/` for local use. For multi-user deployment, connect with your provisioned JWT. There is no built-in email/password signup.

The left navigation includes **Workspace**, **Active research**, **Research history**, **Saved reports**, **Financial data**, **Portfolios**, **Research context**, **Monitoring**, **Team workspaces**, and **Connection**.

## 1. Run a financial research job

1. Open **Workspace**.
2. In **Research question**, enter a focused question such as: "Analyze Microsoft's reported profitability and financial health; cite evidence and identify unavailable metrics."
3. Choose **Company analysis** and enter `MSFT`. For **Company comparison**, enter at least two distinct symbols, up to five. **Historical prices** requires symbols and valid start/end dates. **General financial research** has fewer scoped constraints.
4. Submit. The API durably queues work; an independently running Python worker performs analysis. The browser shows phase progress, not made-up completion percentages.
5. View **Active research** for progress; open the saved report for numerical observations, source references, date/freshness warnings and coverage limitations. You can cancel an active job or retry an eligible failed one.
6. Find completed items in **Research history** and **Saved reports**. A browser refresh may require reconnecting with a JWT; the job itself is stored server-side until its retention deadline.
7. Export the authorized report in the browser as JSON/Markdown where offered, or print/save as PDF using the browser print dialog.

A report may be partial or classified as insufficient evidence. The LLM's free-form claims are not automatically verified; the app presents normalized evidence with explicit limitations.

## 2. Inspect financial data

1. Open **Financial data**.
2. Enter a ticker and select **Dated quote**, **Reported statements**, **Historical prices**, or **Reported filings / SEC**.
3. For historical prices, set the required date range.
4. Select **Retrieve financial evidence**.
5. Read the **Source-backed observations**, coverage, source URLs, currencies, dates and freshness labels.

The available providers are **Financial Datasets** and optionally SEC EDGAR where operator mappings are configured. SEC filings are not substitute live prices; unsupported currency/date combinations are not silently filled in.

## 3. Create a read-only portfolio

1. Open **Portfolios**; choose a name, **base currency** and optional benchmark and select **Create portfolio**.
2. Add a **DEPOSIT** transaction for cash before purchases. The ledger requires sufficient recorded cash.
3. Add `BUY` / `SELL` / `DIVIDEND` / `WITHDRAWAL` / `SPLIT` entries as appropriate. The ledger is **long-only** and does not support short positions, FX conversions or executing trades.
4. Use the transaction form, or paste CSV into the import area and run the preview before saving.
5. Refresh valuation. Missing quotes or currency mismatches are shown as unavailable rather than invented. Save a valuation snapshot if complete.
6. View holdings, cost basis, realized P/L, income, allocation/concentration and supported analytics.
7. Run **Scenario** with per-ticker fractional shocks, or **Calculate risk proxy** with dates where supported.

### CSV ledger template

The CSV importer requires **exactly** these nine columns (header names and no extras):

```csv
transaction_id,date,type,symbol,quantity,price,amount,fee,currency
deposit-1,2026-01-05,DEPOSIT,,0,0,15000,0,USD
buy-1,2026-01-06,BUY,MSFT,2,400,0,0,USD
```

These are **illustrative recorded transaction inputs**, not actual current prices. Import at most 1,000 rows per request, with unique IDs. The portfolio is limited to 5,000 stored ledger entries and 50 current holdings. Dates must not be in the future; all entries must match the portfolio base currency. The importer prevents conflicting reuse of a transaction ID.

**Risk caveat:** The historical risk panel is explicitly a **constant-current-quantity proxy**, not an account's actual historical returns or time-weighted performance. It supports 1–10 current holdings and requires at least 31 aligned daily observations without large gaps; it is not a forecast.

## 4. Save research context and continue

1. Complete a research job successfully.
2. Open **Research context**, select the eligible report and use **Save research context**.
3. Search your explicit memories, select one, and enter a new question such as "Update the previous analysis with newer reported data".
4. Run the continuation; the prior report is **historical context**, not proof that old figures are current.
5. Delete records you no longer want retained.

This is explicit owner-scoped Redis memory with simple search, not an automatically mined user profile or cross-user vector database. Temporary context defaults to 90-day retention; terminal research job records normally expire after 24 hours unless retention is configured differently.

## 5. Set up monitoring

1. Open **Monitoring** and choose **Price above threshold**, **Price below threshold**, or **Recurring research**.
2. For price rules, enter the supported symbol, numeric threshold and currency; for research schedules, provide a question and relevant scope.
3. Choose an interval and create the rule. API minimum interval is **5 minutes**; recurring research requires at least **1 hour**. The scheduler uses **UTC**.
4. Use the rule controls to disable, re-enable or delete it; inspect **In-app notifications** and acknowledge them.

Monitoring is **periodic polling**, not tick-by-tick streaming. Price alerts trigger on threshold crossing with cooldown; intervals missed while stopped are not guaranteed to be replayed. The system provides **in-app notifications only**; there is no email/push notification delivery built in. Up to 20 alert/schedule rules per principal are supported by the current API.

A worker must be running for monitoring evaluations and recurring research.

## 6. Create a collaborative research workspace

Multi-user collaboration requires **separately provisioned JWT identities**. A shared API key or no-auth local mode collapses all clients into one principal.

1. Open **Team workspaces** and create a workspace.
2. An authenticated prospective member can obtain their account ID from the current session (the API `GET /api/v1/platform/me` exposes the principal).
3. Workspace owners/admins can issue an invitation to that **64-character principal ID**, assigning viewer, editor or permitted admin permissions. Invitations are not email invitations and no invitation mail is sent.
4. The recipient enters the invitation ID and selects **Accept invitation**. Invitations expire after 7 days.
5. Editors can share an **owned, saved research report** into the workspace library as a **frozen snapshot**. Viewers may read permitted reports.
6. Authorized members can add annotations/comments and update review state. Report records carry version information; refresh on version conflicts rather than overwriting others' edits.
7. Owners/admins can update permitted memberships, unshare reports, or revoke access.

Sharing a report does **not** automatically share a personal portfolio or private research context. The original job and shared report snapshot are distinct artifacts; changing the original does not automatically rewrite the shared snapshot.

## 7. Export shared reports

Select a permitted report in **Team workspaces** and use available export actions. The backend supports `GET /api/v1/platform/publications/{id}/export?format=json` with `json`, `markdown` or `csv`. CSV contains structured evidence rows; Markdown includes the report and sources. **PDF is not a dedicated platform export endpoint**; for personal research use the browser print/save-PDF workflow where suitable.

Access controls apply to each report and export. Unshared or revoked reports may no longer be available to another member.

## Important limits

FinancialAgentia does **not** place orders, calculate automatic FX conversion, guarantee current/live market prices, provide production user-registration/identity issuance, or independently prove every free-form investment inference. Confirm provider coverage and reporting date before making decisions.
