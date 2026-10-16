"""Cross-source calculations from validated records, never model-supplied operands."""

from datetime import date
from decimal import Decimal
from hashlib import sha256

from .financial_evidence import FinancialEvidence


def annual_record(row):
    if row.period_start and row.period_end:
        return 335 <= (row.period_end - row.period_start).days <= 395
    return row.fiscal_period in {"annual", "FY"}


def calculate_evidence_metrics(
    evidence: list[FinancialEvidence],
) -> list[FinancialEvidence]:
    """Annual multiples are labeled as last-reported annual, never forward/TTM.

    Negative earnings and zero denominators are unavailable. Statement operands
    require exact dates, currency and duration (or explicit annual period label).
    Snapshots must meet configured age policy; no estimated FX or live feed claim.
    """
    additions = []
    for symbol in sorted({row.symbol for row in evidence}):
        rows = [
            r
            for r in evidence
            if r.symbol == symbol and r.currency and r.evidence_type != "CALCULATED"
        ]
        caps = [
            r
            for r in rows
            if r.metric == "market_cap"
            and r.period_end is None
            and r.value > 0
            and r.data_as_of
            and r.freshness != "stale"
        ]
        cap = max(caps, key=lambda r: r.data_as_of) if caps else None
        for numerator_name, denominator_name, metric in [
            ("gross_profit", "revenue", "gross_margin"),
            ("operating_income", "revenue", "operating_margin"),
            ("net_income", "revenue", "net_profit_margin"),
            ("free_cash_flow", "revenue", "free_cash_flow_margin"),
            ("current_assets", "current_liabilities", "current_ratio"),
            ("total_debt", "shareholders_equity", "debt_to_equity"),
        ]:
            numerators = [
                r
                for r in rows
                if r.metric == numerator_name
                and r.period_end
                and r.freshness != "stale"
            ]
            for numerator in numerators:
                denominator = next(
                    (
                        r
                        for r in rows
                        if r.metric == denominator_name
                        and r.value > 0
                        and r.period_end == numerator.period_end
                        and r.period_start == numerator.period_start
                        and r.fiscal_period == numerator.fiscal_period
                        and r.currency == numerator.currency
                    ),
                    None,
                )
                if denominator:
                    additions.append(
                        derived(
                            metric,
                            numerator.value / denominator.value,
                            [numerator, denominator],
                            "fraction"
                            if metric.endswith("margin")
                            else "dimensionless",
                        )
                    )
        if not cap:
            continue
        for name, metric in [
            ("net_income", "price_to_last_annual_earnings"),
            ("revenue", "price_to_last_annual_sales"),
            ("free_cash_flow", "last_annual_free_cash_flow_yield"),
        ]:
            candidates = [
                r
                for r in rows
                if r.metric == name
                and annual_record(r)
                and r.currency == cap.currency
                and r.value > 0
                and r.freshness != "stale"
            ]
            if not candidates:
                continue
            latest = max(
                candidates,
                key=lambda r: (r.period_end or date.min, r.filing_date or date.min),
            )
            # Conflicting same-context values make this calculation unavailable.
            if (
                len(
                    {
                        r.value
                        for r in candidates
                        if r.period_end == latest.period_end
                        and r.period_start == latest.period_start
                    }
                )
                > 1
            ):
                continue
            value = (
                latest.value / cap.value
                if metric.endswith("yield")
                else cap.value / latest.value
            )
            additions.append(
                derived(
                    metric,
                    value,
                    [cap, latest],
                    "fraction" if metric.endswith("yield") else "dimensionless",
                )
            )
    # Do not add duplicate interpretations of metrics already supplied by a
    # deterministic provider tool for the same context.
    existing = {
        (r.symbol, r.metric, r.period_start, r.period_end, r.currency, r.value)
        for r in evidence
    }
    unique = {}
    for row in additions:
        key = (
            row.symbol,
            row.metric,
            row.period_start,
            row.period_end,
            row.currency,
            row.value,
        )
        if key not in existing:
            unique[key] = row
    return list(unique.values())[:200]


def derived(metric, value: Decimal, inputs: list[FinancialEvidence], unit):
    identity = (
        "calc_"
        + sha256(
            (metric + ":".join(row.evidence_id for row in inputs)).encode()
        ).hexdigest()[:18]
    )
    statement = next((row for row in inputs if row.period_end), inputs[0])
    return FinancialEvidence(
        evidence_id=identity,
        source_id=inputs[0].source_id,
        symbol=inputs[0].symbol,
        metric=metric,
        value=value,
        currency=None,
        unit=unit,
        period_start=statement.period_start,
        period_end=statement.period_end,
        data_as_of=max((r.data_as_of for r in inputs if r.data_as_of), default=None),
        evidence_type="CALCULATED",
        calculation_method=(
            " / ".join(row.metric for row in inputs)
            if not metric.endswith("yield")
            else inputs[1].metric + " / " + inputs[0].metric
        )
        + "; exact currency and statement duration; last annual results, not TTM/forecast; source records retain input currency",
        input_evidence_ids=[r.evidence_id for r in inputs],
        freshness="reported_period",
        fiscal_period=statement.fiscal_period,
        calculation_version="evidence-derived-v1",
        quality_flags=[
            "Valuation feed delay/adjustments are not independently verified"
        ]
        if metric.startswith("price_to") or metric.endswith("yield")
        else [],
    )
