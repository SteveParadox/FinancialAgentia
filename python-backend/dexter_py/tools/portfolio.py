"""Read-only long-only portfolio ledger; Decimal arithmetic and explicit limitations."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from io import StringIO
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .financial_datasets import validate_ticker


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    date: date
    type: Literal["BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "DIVIDEND", "SPLIT"]
    symbol: str | None = None
    quantity: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=Decimal("1e12"),
        allow_inf_nan=False,
        max_digits=28,
        decimal_places=10,
    )
    price: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=Decimal("1e12"),
        allow_inf_nan=False,
        max_digits=28,
        decimal_places=10,
    )
    amount: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=Decimal("1e15"),
        allow_inf_nan=False,
        max_digits=28,
        decimal_places=10,
    )
    fee: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=Decimal("1e9"),
        allow_inf_nan=False,
        max_digits=28,
        decimal_places=10,
    )
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("symbol")
    @classmethod
    def symbol_valid(cls, value):
        return validate_ticker(value) if value else None

    @model_validator(mode="after")
    def semantics(self):
        if self.date > date.today():
            raise ValueError("Future transactions unavailable")
        if self.type in {"BUY", "SELL", "SPLIT"} and (
            not self.symbol or self.quantity <= 0
        ):
            raise ValueError("Trade or split requires symbol and positive quantity")
        if self.type in {"BUY", "SELL"} and self.price <= 0:
            raise ValueError("Trade price must be positive")
        if self.type not in {"BUY", "SELL"} and self.fee:
            raise ValueError("Fees supported on trades only")
        if self.type in {"BUY", "SELL"} and self.amount:
            raise ValueError(
                "Trade cash amount is derived from quantity, price and fee"
            )
        if self.type in {"DEPOSIT", "WITHDRAWAL"} and self.symbol:
            raise ValueError("Cash transfer cannot specify an instrument")
        if self.type == "SPLIT" and (self.price or self.amount):
            raise ValueError("Split quantity is multiplier; price/amount must be zero")
        if self.type in {"DEPOSIT", "WITHDRAWAL", "DIVIDEND"} and (
            self.amount <= 0 or self.quantity or self.price
        ):
            raise ValueError("Cash entries require amount only")
        return self


def import_csv(text: str) -> list[LedgerEntry]:
    if len(text) > 500_000:
        raise ValueError("CSV exceeds 500 KB")
    reader = csv.DictReader(StringIO(text))
    required = {
        "transaction_id",
        "date",
        "type",
        "symbol",
        "quantity",
        "price",
        "amount",
        "fee",
        "currency",
    }
    if (
        not reader.fieldnames
        or set(reader.fieldnames) != required
        or len(reader.fieldnames) != len(required)
    ):
        raise ValueError("CSV headers must match the documented ledger template")
    entries = []
    for number, row in enumerate(reader, 2):
        if len(entries) >= 1000:
            raise ValueError("Maximum 1000 rows per import")
        try:
            entries.append(
                LedgerEntry.model_validate(
                    {
                        k: v if v else None if k == "symbol" else "0"
                        for k, v in row.items()
                    }
                )
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid CSV row {number}") from exc
    if not entries:
        raise ValueError("CSV contains no transactions")
    return entries


def reconstruct(entries: list[dict], currency: str) -> dict:
    cash = Decimal(0)
    realized = Decimal(0)
    income = Decimal(0)
    contributions = Decimal(0)
    holdings = {}
    seen = set()
    # Same-day entries retain ledger order: no invented intraday ordering.
    for raw in sorted(entries, key=lambda item: item["date"]):
        entry = LedgerEntry.model_validate(raw)
        if entry.transaction_id in seen:
            raise ValueError("Duplicate transaction ID")
        seen.add(entry.transaction_id)
        if entry.currency != currency:
            raise ValueError(
                "FX conversion is not configured; transaction currency must match portfolio"
            )
        holding = (
            holdings.setdefault(
                entry.symbol, {"quantity": Decimal(0), "cost_basis": Decimal(0)}
            )
            if entry.symbol
            else None
        )
        if entry.type == "DEPOSIT":
            cash += entry.amount
            contributions += entry.amount
        elif entry.type == "WITHDRAWAL":
            cash -= entry.amount
            contributions -= entry.amount
        elif entry.type == "DIVIDEND":
            cash += entry.amount
            income += entry.amount
        elif entry.type == "SPLIT":
            if holding["quantity"] <= 0:
                raise ValueError("Split requires an existing holding")
            holding["quantity"] *= entry.quantity
        elif entry.type == "BUY":
            cost = entry.quantity * entry.price + entry.fee
            cash -= cost
            holding["quantity"] += entry.quantity
            holding["cost_basis"] += cost
        else:
            if entry.quantity > holding["quantity"]:
                raise ValueError("Short positions are not supported")
            basis = holding["cost_basis"] * entry.quantity / holding["quantity"]
            proceeds = entry.quantity * entry.price - entry.fee
            cash += proceeds
            realized += proceeds - basis
            holding["quantity"] -= entry.quantity
            holding["cost_basis"] -= basis
        if cash < 0:
            raise ValueError(
                "Insufficient recorded cash; add deposits before purchases/withdrawals"
            )
    return {
        "cash": cash,
        "realized_profit_loss": realized,
        "dividend_income": income,
        "net_contributions": contributions,
        "holdings": {
            symbol: value for symbol, value in holdings.items() if value["quantity"] > 0
        },
    }


def value_portfolio(ledger: dict, quotes: dict, shocks: dict | None = None) -> dict:
    total = ledger["cash"]
    rows = []
    missing = []
    scenario_delta = Decimal(0)
    for symbol, holding in ledger["holdings"].items():
        quote = quotes.get(symbol)
        if quote is None:
            missing.append(symbol)
            rows.append({"symbol": symbol, **holding, "market_value": None})
            continue
        price = Decimal(str(quote["price"]))
        if not price.is_finite() or price <= 0:
            raise ValueError("Invalid valuation price")
        value = holding["quantity"] * price
        total += value
        shock = Decimal(str((shocks or {}).get(symbol, 0)))
        if not shock.is_finite() or not -1 <= shock <= 10:
            raise ValueError("Scenario return must be between -1 and 10")
        scenario_delta += value * shock
        rows.append(
            {
                "symbol": symbol,
                **holding,
                "market_value": value,
                "unrealized_profit_loss": value - holding["cost_basis"],
                "quote": quote,
            }
        )
    complete = not missing
    for row in rows:
        row["weight"] = (
            row["market_value"] / total
            if complete and total and row["market_value"] is not None
            else None
        )
    return {
        **ledger,
        "holdings": rows,
        "market_value": total if complete else None,
        "known_value": total,
        "missing_prices": missing,
        "profit_loss": total - ledger["net_contributions"] if complete else None,
        "largest_weight": max(
            (row["weight"] for row in rows if row["weight"] is not None),
            default=Decimal(0),
        )
        if complete
        else None,
        "scenario_value": total + scenario_delta if complete else None,
        "methodology_version": "weighted-average-cost-v1",
        "limitations": [
            "Long-only equity/ETF ledger in base currency; recorded splits and dividends only. Quotes are dated, not live. TWR and historical risk require dated valuations and cash-flow alignment."
        ],
    }


def xirr(cashflows: list[tuple[date, float]]) -> float:
    """Actual/365 dated investor cash flows; bounded bisection, unique-root check."""
    ordered = sorted(cashflows)
    if (
        len(ordered) < 2
        or not any(v < 0 for _, v in ordered)
        or not any(v > 0 for _, v in ordered)
    ):
        raise ValueError("XIRR requires positive and negative cash flows")
    # Restrict to conventional flows (contributions followed by final receipt),
    # avoiding a misleading choice among multiple roots.
    if any(v > 0 for _, v in ordered[:-1]) or ordered[-1][1] <= 0:
        raise ValueError("Nonconventional cash flows may have multiple IRRs")
    if ordered[-1][0] <= ordered[0][0] or any(not math.isfinite(v) for _, v in ordered):
        raise ValueError("Invalid XIRR dates/values")

    def npv(rate):
        return sum(
            v / (1 + rate) ** ((d - ordered[0][0]).days / 365) for d, v in ordered
        )

    lo, hi = -0.9999, 100.0
    if npv(lo) * npv(hi) >= 0:
        raise ValueError("No bounded XIRR root")
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def risk_statistics(
    returns: list[float],
    benchmark: list[float] | None = None,
    annual_risk_free: float = 0.0,
) -> dict:
    """Sample daily risk on aligned complete observations; historical 95% VaR/CVaR."""
    import statistics

    if len(returns) < 30 or any(not math.isfinite(r) or r <= -1 for r in returns):
        raise ValueError("At least 30 valid daily returns required")
    if not math.isfinite(annual_risk_free) or annual_risk_free <= -1:
        raise ValueError("Invalid risk-free rate")
    mean = statistics.mean(returns)
    sigma = statistics.stdev(returns)
    rf = (1 + annual_risk_free) ** (1 / 252) - 1
    downside = math.sqrt(sum(min(r - rf, 0) ** 2 for r in returns) / len(returns))
    wealth = peak = 1.0
    drawdown = 0.0
    for r in returns:
        wealth *= 1 + r
        peak = max(peak, wealth)
        drawdown = min(drawdown, wealth / peak - 1)
    ordered = sorted(returns)
    tail = ordered[: max(1, math.ceil(0.05 * len(returns)))]
    result = {
        "observations": len(returns),
        "annualized_volatility": sigma * math.sqrt(252),
        "sharpe_ratio": (mean - rf) / sigma * math.sqrt(252) if sigma else None,
        "sortino_ratio": (mean - rf) / downside * math.sqrt(252) if downside else None,
        "max_drawdown": drawdown,
        "historical_var_95": max(0, -tail[-1]),
        "historical_cvar_95": max(0, -statistics.mean(tail)),
        "total_return": wealth - 1,
        "methodology_version": "daily-risk-v1",
        "assumptions": "252 trading days; sample volatility; effective annual risk-free converted daily; historical 5% lower tail; no forecast",
    }
    if benchmark is not None:
        if len(benchmark) != len(returns) or any(
            not math.isfinite(r) or r <= -1 for r in benchmark
        ):
            raise ValueError("Benchmark returns must be aligned and valid")
        variance = statistics.variance(benchmark)
        covariance = sum(
            (a - mean) * (b - statistics.mean(benchmark))
            for a, b in zip(returns, benchmark)
        ) / (len(returns) - 1)
        result["beta"] = covariance / variance if variance else None
        result["correlation"] = (
            covariance / (sigma * statistics.stdev(benchmark))
            if variance and sigma
            else None
        )
        result["benchmark_relative_compound_return"] = (
            wealth / math.prod(1 + r for r in benchmark) - 1
        )
    return result


def time_weighted_return(subperiods: list[tuple[float, float, float]]) -> float:
    """(opening value, closing value, end-of-period external net contribution).

    Caller MUST supply valuations at every external flow boundary. This function
    cannot reconstruct such valuations from current holdings or sparse quotes.
    """
    if not subperiods:
        raise ValueError("At least one valued subperiod required")
    growth = 1.0
    for opening, closing, end_flow in subperiods:
        if (
            any(not math.isfinite(x) for x in (opening, closing, end_flow))
            or opening <= 0
            or closing - end_flow < 0
        ):
            raise ValueError("Invalid TWR subperiod")
        growth *= (closing - end_flow) / opening
    return growth - 1
