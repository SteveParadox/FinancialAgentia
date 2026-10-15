"""Deterministic analytics. Fractions, not percent points; no missing-value imputation.

Price observations must be positive and ordered by the caller. Daily sample
volatility and Sharpe use 252 trading observations/year; risk-free is an annual
effective rate converted to an equivalent daily rate. Returns exclude dividends
unless upstream closes explicitly include them. CAGR uses elapsed calendar days.
"""
from __future__ import annotations

import math
from statistics import mean, stdev
from typing import Sequence


def numbers(values: Sequence[float], *, positive: bool = False, minimum: int = 1) -> list[float]:
    if len(values) < minimum:
        raise ValueError("Insufficient observations")
    result = []
    for value in values:
        if isinstance(value, bool) or value is None:
            raise ValueError("Missing or invalid observation")
        value = float(value)
        if not math.isfinite(value) or (positive and value <= 0):
            raise ValueError("Observations must be finite" + (" and positive" if positive else ""))
        result.append(value)
    return result


def returns(prices: Sequence[float]) -> list[float]:
    p = numbers(prices, positive=True, minimum=2)
    return [current / previous - 1 for previous, current in zip(p, p[1:])]


def compound_return(period_returns: Sequence[float]) -> float:
    r = numbers(period_returns)
    if any(x < -1 for x in r):
        raise ValueError("Simple returns cannot be below -100%")
    return math.prod(1 + x for x in r) - 1


def cagr(start: float, end: float, elapsed_days: float) -> float:
    start, end, elapsed_days = numbers([start, end, elapsed_days], positive=True)
    return (end / start) ** (365.25 / elapsed_days) - 1


def moving_average(prices: Sequence[float], window: int) -> float:
    if type(window) is not int or window < 1:
        raise ValueError("Window must be a positive integer")
    p = numbers(prices, positive=True, minimum=window)
    return mean(p[-window:])


def volatility(period_returns: Sequence[float], periods_per_year: int = 252) -> float:
    if type(periods_per_year) is not int or periods_per_year < 1:
        raise ValueError("Invalid annualization")
    return stdev(numbers(period_returns, minimum=2)) * math.sqrt(periods_per_year)


def max_drawdown(prices: Sequence[float]) -> float:
    p = numbers(prices, positive=True)
    peak = p[0]
    drawdown = 0.0
    for value in p:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return drawdown


def sharpe_ratio(period_returns: Sequence[float], annual_risk_free: float = 0,
                 periods_per_year: int = 252) -> float | None:
    r = numbers(period_returns, minimum=2)
    rf = numbers([annual_risk_free])[0]
    if rf <= -1 or type(periods_per_year) is not int or periods_per_year < 1:
        raise ValueError("Invalid risk-free rate or annualization")
    deviation = stdev(r)
    if deviation == 0:
        return None  # Undefined, never infinity or a performance guarantee.
    excess = mean(r) - ((1 + rf) ** (1 / periods_per_year) - 1)
    return excess / deviation * math.sqrt(periods_per_year)


def correlation(first: Sequence[float], second: Sequence[float]) -> float | None:
    if len(first) != len(second):
        raise ValueError("Series must be aligned before correlation")
    a, b = numbers(first, minimum=2), numbers(second, minimum=2)
    da = [x - mean(a) for x in a]
    db = [x - mean(b) for x in b]
    denominator = math.sqrt(sum(x*x for x in da) * sum(x*x for x in db))
    return sum(x*y for x, y in zip(da, db)) / denominator if denominator else None


def ratio(numerator: float, denominator: float) -> float | None:
    a, b = numbers([numerator, denominator])
    return a / b if b > 0 else None


def benchmark_relative(asset: Sequence[float], benchmark: Sequence[float]) -> float:
    if len(asset) != len(benchmark):
        raise ValueError("Benchmark observations must align")
    return compound_return(returns(asset)) - compound_return(returns(benchmark))


def analyze_prices(prices: Sequence[float], *, elapsed_days: float) -> dict[str, float]:
    p = numbers(prices, positive=True, minimum=3)
    r = returns(p)
    result = {"total_return": compound_return(r), "cagr": cagr(p[0], p[-1], elapsed_days),
              "annualized_volatility": volatility(r), "max_drawdown": max_drawdown(p),
              "sma": moving_average(p, min(20, len(p)))}
    sharpe = sharpe_ratio(r)
    if sharpe is not None:
        result["sharpe_ratio"] = sharpe
    return result
