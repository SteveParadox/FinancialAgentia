/** Format exact API decimal strings without conversion through binary floats. */
export function formatMoney(
  value: unknown,
  currency: string,
  locale?: string,
): string {
  if (typeof value !== "string" && typeof value !== "number")
    return "Unavailable";
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(
    String(value),
  );
  if (!match || !/^[A-Z]{3}$/.test(currency)) return "Unavailable";
  const exponent = Number(match[4] || 0);
  if (!Number.isInteger(exponent) || Math.abs(exponent) > 100)
    return "Unavailable";
  const whole = match[2]!;
  const digits = whole + (match[3] || "");
  const cut = whole.length + exponent + 2;
  const kept = cut <= 0 ? "0" : digits.slice(0, cut).padEnd(cut, "0");
  let cents = BigInt(kept);
  if (cut >= 0 && digits[cut] && digits[cut]! >= "5") cents += 1n;
  const negative = match[1] === "-" && cents !== 0n;
  const units = cents / 100n;
  const fraction = String(cents % 100n).padStart(2, "0");
  const formatter = new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const parts = formatter.formatToParts(negative ? -units : units);
  const rendered = parts
    .map((part) => (part.type === "fraction" ? fraction : part.value))
    .join("");
  return negative && units === 0n ? "-" + rendered : rendered;
}
