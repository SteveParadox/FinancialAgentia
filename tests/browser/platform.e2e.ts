import { test, expect, Page } from "@playwright/test";
import { createHmac } from "node:crypto";
function token(subject: string) {
  const head = Buffer.from(
    JSON.stringify({ alg: "HS256", typ: "JWT" }),
  ).toString("base64url");
  const body = Buffer.from(
    JSON.stringify({ sub: subject, exp: Math.floor(Date.now() / 1000) + 3600 }),
  ).toString("base64url");
  return `${head}.${body}.${createHmac("sha256", "browser-fixture-secret").update(`${head}.${body}`).digest("base64url")}`;
}
async function login(page: Page, user: string) {
  await page.goto("/");
  await page.getByLabel("Access token", { exact: true }).fill(token(user));
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(
    page.getByRole("heading", { name: "Your next insight starts here." }),
  ).toBeVisible();
}
async function navigate(page: Page, label: string) {
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: label, exact: true })
    .click();
}
async function report(page: Page, user: string) {
  const response = await page.request.post("/api/v1/agents/executions", {
    headers: {
      Authorization: `Bearer ${token(user)}`,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: {
      query: "Analyze MSFT profitability and valuation limitations",
      scope: { category: "company", symbols: ["MSFT"] },
    },
  });
  expect(response.status()).toBe(202);
  const id = (await response.json()).execution_id as string;
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(`/api/v1/agents/executions/${id}`, {
              headers: { Authorization: `Bearer ${token(user)}` },
            })
          ).json()
        ).state,
    )
    .toBe("SUCCEEDED");
  return id;
}

test("connected portfolio ledger, valuation, scenario, context follow-up and periodic monitoring", async ({
  page,
}) => {
  const user = "platform-browser";
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page, user);
  const id = await report(page, user);
  await navigate(page, "Research context");
  await page.getByLabel("Completed research job ID").fill(id);
  await page.getByLabel("Context label").fill("Microsoft prior analysis");
  await page
    .getByRole("button", { name: "Save research context", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Microsoft prior analysis" }),
  ).toBeVisible();
  await page
    .getByLabel("Follow-up question")
    .fill(
      "Update the previous Microsoft research with refreshed reported evidence",
    );
  await page
    .getByRole("button", { name: "Continue with refreshed evidence" })
    .click();
  await expect(
    page.getByRole("article", { name: "Saved research report" }),
  ).toBeVisible();
  expect(page.url()).not.toContain(id);
  await navigate(page, "Portfolios");
  await page.getByLabel("Portfolio name").fill("Long term equities");
  await page
    .getByRole("button", { name: "Create portfolio", exact: true })
    .click();
  await page.getByLabel("Cash amount").fill("1000");
  await page.getByRole("button", { name: "Save transaction" }).click();
  await expect(page.getByText("Ledger saved.", { exact: false })).toBeVisible();
  await page.getByLabel("Transaction type").selectOption("BUY");
  await page.getByLabel("Holding symbol").fill("MSFT");
  await page.getByLabel("Quantity", { exact: true }).fill("2");
  await page.getByLabel("Acquisition / sale price").fill("250");
  await page.getByRole("button", { name: "Save transaction" }).click();
  await expect(
    page.getByRole("heading", { name: /Ledger version 3/ }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Retrieve latest available valuation" })
    .click();
  await expect(
    page.locator(".metric-card").filter({ hasText: "Market value" }),
  ).toContainText("$1,100.00");
  await page.getByLabel("Scenario holding").fill("MSFT");
  await page.getByLabel("Hypothetical return fraction").fill("-0.2");
  await page
    .getByRole("button", { name: "Evaluate hypothetical scenario" })
    .click();
  await expect(
    page.getByText("Hypothetical value:", { exact: false }),
  ).toContainText("$980.00");
  await page.getByLabel("New portfolio name").fill("Core equities");
  await page
    .getByRole("button", { name: "Rename portfolio", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: /Core equities · Ledger version 4/ }),
  ).toBeVisible();
  await page.getByLabel("Risk start", { exact: true }).fill("2026-07-01");
  await page.getByLabel("Risk end", { exact: true }).fill("2026-09-03");
  await page
    .getByRole("button", { name: "Calculate risk proxy", exact: true })
    .click();
  await expect(
    page.getByRole("cell", { name: "annualized_volatility", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Save valuation snapshot", exact: true })
    .click();
  await expect(
    page.getByText("Valuation snapshot persisted with source quote dates."),
  ).toBeVisible();
  await navigate(page, "Monitoring");
  await page.getByLabel("Rule name").fill("Microsoft weekly update");
  await page.getByLabel("Monitoring type").selectOption("research");
  await page
    .getByLabel("Recurring research question")
    .fill("Analyze MSFT reported profitability");
  await page
    .getByLabel("Evaluation interval / cooldown")
    .selectOption("604800");
  await page.getByRole("button", { name: "Create monitoring rule" }).click();
  await expect(
    page.getByRole("heading", { name: "Microsoft weekly update" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.getByLabel("Access token", { exact: true }).fill(token(user));
  await page.getByRole("button", { name: "Open workspace" }).click();
  await navigate(page, "Portfolios");
  await page.getByRole("button", { name: "Core equities · USD" }).click();
  await expect(
    page.getByRole("cell", { name: "BUY", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("team report sharing is isolated, comments persist and revocation blocks export", async ({
  page,
  browser,
}) => {
  const owner = "workspace-owner",
    editor = "workspace-editor";
  await login(page, owner);
  const id = await report(page, owner);
  const editorAccount = await page.request.get("/api/v1/platform/me", {
    headers: { Authorization: `Bearer ${token(editor)}` },
  });
  const accountId = (await editorAccount.json()).principal as string;
  await navigate(page, "Team workspaces");
  await page.getByLabel("Workspace name").fill("Equity research desk");
  await page
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await page.getByLabel("Recipient account ID").fill(accountId);
  await page.getByLabel("Granted role").selectOption("editor");
  await page
    .getByRole("button", { name: "Create invitation", exact: true })
    .click();
  const notice = await page.locator(".platform-notice").innerText();
  const invitation = /Invitation ID: ([a-f0-9-]{36})/.exec(notice)![1]!;
  const context = await browser.newContext();
  const other = await context.newPage();
  await login(other, editor);
  await navigate(other, "Team workspaces");
  await other.getByLabel("Invitation ID", { exact: true }).fill(invitation);
  await other
    .getByRole("button", { name: "Accept invitation", exact: true })
    .click();
  await expect(
    other.getByText("Your role: editor", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  // Refresh explicit workspace version before concurrent membership changes.
  await page
    .getByRole("button", { name: "Equity research desk", exact: true })
    .click();
  await page.getByLabel("Owned report job ID").fill(id);
  await page.getByLabel("Shared report title").fill("Microsoft team report");
  await page
    .getByRole("button", { name: "Share a saved report snapshot" })
    .click();
  await other
    .getByRole("button", { name: "Equity research desk", exact: true })
    .click();
  await other.getByRole("button", { name: /^Open report / }).click();
  await expect(
    other.getByRole("heading", { name: "Microsoft team report", exact: true }),
  ).toBeVisible();
  await other
    .getByLabel("Research comment")
    .fill("Review currency and reporting period alignment.");
  await other
    .getByRole("button", { name: "Add annotation", exact: true })
    .click();
  await expect(
    other.getByRole("cell", {
      name: "Review currency and reporting period alignment.",
    }),
  ).toBeVisible();
  const fileEvent = other.waitForEvent("download");
  await other.getByRole("button", { name: "Export JSON", exact: true }).click();
  expect((await fileEvent).suggestedFilename()).toMatch(/^research-.*\.json$/);
  await page.getByRole("button", { name: /^Open report / }).click();
  await expect(
    page.getByRole("cell", {
      name: "Review currency and reporting period alignment.",
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Revoke report sharing", exact: true })
    .click();
  await expect(
    page.getByText("No reports shared in this workspace."),
  ).toBeVisible();
  const shared = (
    await (
      await other.request.get("/api/v1/platform/workspaces", {
        headers: { Authorization: `Bearer ${token(editor)}` },
      })
    ).json()
  ).items[0];
  expect(shared.data.publications).toEqual([]);
  await context.close();
});

test("financial data workspace renders normalized source records and annual statements", async ({
  page,
}) => {
  await login(page, "financial-data-browser");
  await navigate(page, "Financial data");
  await page.getByLabel("Symbol", { exact: true }).fill("MSFT");
  await page
    .getByRole("button", { name: "Retrieve financial evidence", exact: true })
    .click();
  await expect(
    page.getByRole("cell", { name: "price", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "300", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Source record", exact: true }),
  ).toHaveAttribute("href", /^https:\/\/api\.financialdatasets\.ai\//);
  await page.getByLabel("Dataset", { exact: true }).selectOption("statements");
  await page
    .getByRole("button", { name: "Retrieve financial evidence", exact: true })
    .click();
  await expect(
    page.getByRole("cell", { name: "revenue", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "2025-12-31", exact: true }).first(),
  ).toBeVisible();
});
