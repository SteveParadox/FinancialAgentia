# Calculation methodology, version 1

`dexter_py/tools/calculations.py` is deterministic. Returns = current/previous − 1; compound return = product(1+r) − 1; CAGR uses actual elapsed calendar days/365.25. SMA uses the stated trailing observation count. RSI is explicitly simple-window RSI, not Wilder smoothing. No missing-value imputation occurs.

Sample return standard deviation × sqrt(252) gives annualized daily volatility. Sharpe subtracts a daily equivalent of the stated annual effective risk-free rate (default zero) and annualizes by sqrt(252); zero variance is undefined, not infinity. Sharpe is dimensionless, not a percentage or currency amount. Gaps above four calendar days prevent daily volatility/Sharpe output; observed-period return and drawdown remain with partial coverage disclosed. This heuristic does not prove a complete exchange calendar.

Drawdown is minimum observed price/running observed peak − 1. Positive, finite prices and ordered unique timestamps are required. Benchmark/correlation utilities require pre-aligned series. Returns do not imply dividends/split adjustment unless the provider explicitly supplies that policy. The adapter currently marks adjustments unspecified.

Statement ratios use Decimal, matching currencies/report periods/period types and positive denominators. Supported when inputs exist: net/gross/operating margin, current ratio, debt/equity, interest coverage, net income/period-end equity. The last is not mislabeled average-equity ROE. Missing denominators, losses where a positive denominator is required, incompatible periods and unavailable data do not produce invented ratios. No DCF, forward estimates, beta, MACD or ATR is presented as implemented in the workspace.

Each calculated record carries calculation version/method and original input evidence IDs. Incomplete/rejected/truncated input lineage prevents certification. Conflicting same-date, same-unit observations are retained and force a partial report. Values remain exact strings in the report; browser chart conversion is for plotting only. Independent JS end-to-end checks recompute historical endpoint returns.
