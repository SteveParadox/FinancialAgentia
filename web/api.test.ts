import { describe, expect, test } from "bun:test";
import { safeUrl, JobSchema } from "./api";

describe("browser boundary validation", () => {
  test("untrusted citation schemes and embedded credentials cannot become links", () => {
    for (const url of [
      "javascript:alert(1)",
      "data:text/html,unsafe",
      "http://example.com",
      "https://secret:password@example.com",
      "not a URL",
    ])
      expect(safeUrl(url)).toBeUndefined();
    expect(safeUrl("https://api.financialdatasets.ai/prices/")).toBe(
      "https://api.financialdatasets.ai/prices/",
    );
  });
  test("unknown job states fail closed instead of displaying completion", () => {
    expect(
      JobSchema.safeParse({
        execution_id: crypto.randomUUID(),
        session_id: crypto.randomUUID(),
        state: "COMPLETE_TRUST_ME",
        created_at: "1",
        updated_at: "1",
        model_version: "gpt-4.1",
      }).success,
    ).toBe(false);
  });
});
