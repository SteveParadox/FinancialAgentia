import { expect, test } from "bun:test";
import { formatMoney } from "../web/money";
test("currency rendering preserves large decimal cents and explicit rounding", () => {
  expect(formatMoney("1000000000000000.01", "USD", "en-US")).toBe(
    "$1,000,000,000,000,000.01",
  );
  expect(formatMoney("1100", "USD", "en-US")).toBe("$1,100.00");
  expect(formatMoney("-0.015", "USD", "en-US")).toBe("-$0.02");
  expect(formatMoney("1.999", "USD", "en-US")).toBe("$2.00");
  expect(formatMoney("1e-3", "USD", "en-US")).toBe("$0.00");
  expect(formatMoney("5e-3", "USD", "en-US")).toBe("$0.01");
  expect(formatMoney("1e4", "USD", "en-US")).toBe("$10,000.00");
  expect(formatMoney("NaN", "USD", "en-US")).toBe("Unavailable");
  expect(formatMoney(null, "USD", "en-US")).toBe("Unavailable");
});
