import { expect, test, Page } from "@playwright/test";
import { createHmac } from "node:crypto";
import { readFile } from "node:fs/promises";

function token(subject = "alice") {
  const head = Buffer.from(
    JSON.stringify({ alg: "HS256", typ: "JWT" }),
  ).toString("base64url");
  const payload = Buffer.from(
    JSON.stringify({ sub: subject, exp: Math.floor(Date.now() / 1000) + 3600 }),
  ).toString("base64url");
  return `${head}.${payload}.${createHmac("sha256", "browser-fixture-secret").update(`${head}.${payload}`).digest("base64url")}`;
}
async function connect(page: Page, subject = "alice") {
  await page.getByLabel("Access token", { exact: true }).fill(token(subject));
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(
    page.getByRole("heading", { name: "Your next insight starts here." }),
  ).toBeVisible();
}
async function research(
  page: Page,
  question: string,
  symbols: string,
  type = "company",
) {
  await page.getByLabel("Research question", { exact: true }).fill(question);
  await page.getByLabel("Ticker symbols").fill(symbols);
  await page.getByLabel("Research type").selectOption(type);
}
async function saved(page: Page) {
  await expect(
    page.getByRole("article", { name: "Saved research report" }),
  ).toBeVisible();
  const id = page.url().split("#job=")[1]!;
  const result = await page.request.get(
    `/api/v1/agents/executions/${id}/result`,
    { headers: { Authorization: `Bearer ${token()}` } },
  );
  expect(result.status()).toBe(200);
  return result.json();
}

test("company analysis persists through refresh; citations, calculations and JSON export match the saved report", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  await page.goto("/");
  await connect(page);
  await research(
    page,
    "Analyze Microsoft profitability, financial health and market information with cited limitations.",
    "MSFT",
  );
  await page.getByRole("button", { name: "Start research" }).click();
  const report = await saved(page);
  expect(report.status).toBe("PARTIAL");
  expect(
    report.evidence.find((row: any) => row.metric === "net_profit_margin")
      .value,
  ).toBe("0.2");
  expect(
    report.evidence.find((row: any) => row.metric === "current_ratio").value,
  ).toBe("2");
  expect(report.evidence.every((row: any) => row.symbol === "MSFT")).toBe(true);
  await page
    .getByRole("button", { name: "Inspect", exact: true })
    .first()
    .click();
  await expect(page.locator("#evidence-inspector")).toContainText(
    "Evidence ID",
  );
  await expect(
    page.getByRole("link", { name: "View recorded provider endpoint" }).first(),
  ).toHaveAttribute("href", /^https:\/\/api\.financialdatasets\.ai\//);
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "JSON", exact: false }).click();
  const download = await downloadEvent;
  expect(JSON.parse(await readFile((await download.path())!, "utf8"))).toEqual(
    report,
  );
  const oldUrl = page.url();
  await page.reload();
  await connect(page);
  await expect(
    page.getByRole("article", { name: "Saved research report" }),
  ).toBeVisible();
  expect(page.url()).toBe(oldUrl);
  await page.getByRole("button", { name: "Research history" }).click();
  await page
    .getByLabel("Search loaded research")
    .fill("Microsoft profitability");
  await expect(
    page.getByRole("button", {
      name: "Analyze Microsoft profitability, financial health and market information with cited limitations.",
      exact: true,
    }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("comparison covers MSFT, AAPL and GOOGL without conflating instruments or units", async ({
  page,
}) => {
  await page.goto("/");
  await connect(page);
  await research(
    page,
    "Compare Microsoft, Apple and Alphabet reported fundamentals, profitability and financial health.",
    "MSFT,AAPL,GOOGL",
    "comparison",
  );
  await page.getByRole("button", { name: "Start research" }).click();
  const report = await saved(page);
  expect(
    [...new Set(report.evidence.map((row: any) => row.symbol))].sort(),
  ).toEqual(["AAPL", "GOOGL", "MSFT"]);
  for (const symbol of ["MSFT", "AAPL", "GOOGL"]) {
    const margin = report.evidence.find(
      (row: any) => row.symbol === symbol && row.metric === "net_profit_margin",
    );
    expect(Number(margin.value)).toBe(0.2);
    expect(margin.currency).toBeNull();
    expect(margin.unit).toBe("fraction");
    expect(margin.input_evidence_ids.length).toBeGreaterThan(0);
  }
  await expect(
    page.getByRole("table", { name: "Company comparison", exact: true }),
  ).toContainText("GOOGL");
  await expect(
    page.getByRole("table", { name: /Financial evidence with units/ }),
  ).toContainText("MSFT");
});

test("five-year historical prices retain full calculation lineage and independently correct returns", async ({
  page,
}) => {
  await page.goto("/");
  await connect(page);
  await research(
    page,
    "Analyze NVIDIA five-year historical stock performance and risk. Disclose adjustment limitations.",
    "NVDA",
    "historical_prices",
  );
  await page.getByLabel("From", { exact: true }).fill("2021-09-03");
  await page.getByLabel("Through", { exact: true }).fill("2026-09-03");
  await page.getByRole("button", { name: "Start research" }).click();
  const report = await saved(page);
  const closes = report.evidence
    .filter((row: any) => row.metric === "close")
    .sort((a: any, b: any) => a.data_as_of.localeCompare(b.data_as_of));
  expect(closes.length).toBeGreaterThan(1200);
  const result = report.evidence.find(
    (row: any) => row.metric === "total_return",
  );
  expect(Number(result.value)).toBeCloseTo(
    Number(closes.at(-1).value) / Number(closes[0].value) - 1,
    12,
  );
  expect(result.input_evidence_ids.length).toBe(closes.length);
  expect(report.limitations.join(" ")).toContain("point-in-time");
  await expect(
    page.getByRole("img", { name: /Historical close/ }),
  ).toBeVisible();
});

test("cancellation stops a running job and preserves terminal state after reconnect", async ({
  page,
}) => {
  await page.goto("/");
  await connect(page);
  await research(page, "Analyze slowly responding fixture instrument.", "SLOW");
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(
    page.getByText("Retrieving & analyzing", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Cancel research", exact: true })
    .click();
  await expect(
    page.getByText(
      "Research was cancelled. No late result can complete this job.",
    ),
  ).toBeVisible();
  const id = page.url().split("#job=")[1]!;
  const response = await page.request.get(`/api/v1/agents/executions/${id}`, {
    headers: { Authorization: `Bearer ${token()}` },
  });
  expect((await response.json()).state).toBe("CANCELLED");
  await expect(
    page.getByRole("article", { name: "Saved research report" }),
  ).toHaveCount(0);
});

test("provider outage is visible and retry creates a linked job instead of overwriting failure", async ({
  page,
}) => {
  await page.goto("/");
  await connect(page);
  await research(
    page,
    "Analyze unavailable fixture data and do not invent figures.",
    "FAIL",
  );
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(
    page.getByRole("button", { name: "Retry research", exact: true }),
  ).toBeVisible();
  const original = page.url().split("#job=")[1]!;
  await page
    .getByRole("button", { name: "Retry research", exact: true })
    .click();
  await expect.poll(() => page.url().split("#job=")[1]).not.toBe(original);
  const successor = page.url().split("#job=")[1]!;
  const response = await page.request.get(
    `/api/v1/agents/executions/${original}`,
    { headers: { Authorization: `Bearer ${token()}` } },
  );
  const record = await response.json();
  expect(record.state).toBe("FAILED");
  expect(record.retry_execution_id).toBe(successor);
});

test("disconnecting progress transport recovers via persisted snapshots; mobile remains usable", async ({
  page,
  context,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await connect(page);
  await research(
    page,
    "Analyze Microsoft after a browser network interruption.",
    "MSFT",
  );
  await page.route("**/events", (route) => route.abort());
  await page.getByRole("button", { name: "Start research" }).click();
  await expect.poll(() => page.url().includes("#job=")).toBe(true);
  await context.setOffline(true);
  await page.waitForTimeout(300);
  await context.setOffline(false);
  await saved(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "../workspace-mobile.png", fullPage: true });
});

test("multi-user isolation protects report, stream, status, cancellation and retry", async ({
  page,
  browser,
}) => {
  await page.goto("/");
  await connect(page);
  await research(page, "Analyze Microsoft for Alice only.", "MSFT");
  await page.getByRole("button", { name: "Start research" }).click();
  await saved(page);
  const id = page.url().split("#job=")[1]!;
  const bob = await browser.newContext({
    baseURL: page.url().split("/#")[0],
    extraHTTPHeaders: { Authorization: `Bearer ${token("bob")}` },
  });
  for (const path of ["", "/events", "/result"])
    expect(
      (
        await bob.request.get(`/api/v1/agents/executions/${id}${path}`)
      ).status(),
    ).toBe(404);
  for (const path of ["/cancel", "/retry"])
    expect(
      (
        await bob.request.post(`/api/v1/agents/executions/${id}${path}`, {
          headers: { "Idempotency-Key": "bob-cannot-retry" },
        })
      ).status(),
    ).toBe(404);
  const history = await bob.request.get("/api/v1/agents/executions");
  expect((await history.json()).executions).toEqual([]);
  await bob.close();
});

test("invalid credentials are rejected and untrusted request text is escaped", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Access token", { exact: true }).fill("invalid");
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(page.getByRole("alert")).toContainText("rejected");
  await connect(page);
  await expect(page.locator("input[type=password]")).toHaveCount(0);
  await research(
    page,
    '<img src=x onerror="window.__injected=true"> Microsoft research',
    "MSFT",
  );
  await page.getByRole("button", { name: "Start research" }).click();
  await saved(page);
  await expect(
    page.getByRole("heading", {
      name: '<img src=x onerror="window.__injected=true"> Microsoft research',
      exact: true,
    }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => (window as Window & { __injected?: boolean }).__injected,
    ),
  ).toBeUndefined();
  await page.screenshot({ path: "../workspace-desktop.png", fullPage: true });
});
