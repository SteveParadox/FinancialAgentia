"""Financial source and claim validation, intentionally independent of the LLM.

Only deterministic, traceable and type-checked claims may enter an authoritative
research report. The model's original free-form answer is never treated as proof.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import os
from typing import Any, Literal
from .contracts import AgentExecution
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

ALLOWED_PROVIDERS = {"financialdatasets.ai"}
CANONICAL_METRICS = {"net_cash_flow_from_operations": "operating_cash_flow"}
FINANCIAL_VALUES = {
    "price", "close", "open", "high", "low", "volume",
    "revenue", "net_income", "gross_profit", "operating_income",
    "total_assets", "total_liabilities", "shareholders_equity",
    "total_equity", "earnings_per_share", "market_cap",
    "sma", "rsi", "net_profit_margin", "return_on_period_end_equity",
    "total_return", "cagr", "annualized_volatility", "max_drawdown", "sharpe_ratio",
    "operating_cash_flow", "free_cash_flow", "total_debt", "cash_and_equivalents",
    "current_assets", "current_liabilities", "interest_expense", "gross_margin",
    "operating_margin", "current_ratio", "debt_to_equity", "interest_coverage",
}


class EvidenceError(ValueError):
    pass


class FinancialSource(BaseModel):
    source_id: str
    provider: str
    source_url: str | None = None
    retrieved_at: datetime
    published_at: datetime | None = None
    data_as_of: str | None = None
    document_type: str | None = None
    accession_number: str | None = None
    headline: str | None = None
    symbol: str | None = None
    publisher: str | None = None
    cache_status: str = "unspecified"
    adjustment_status: str = "unspecified"
    exchange: str | None = None
    timezone: str | None = None
    verification_status: str = "SINGLE_PROVIDER"


class FinancialEvidence(BaseModel):
    evidence_id: str
    source_id: str
    symbol: str
    metric: str
    value: Decimal
    unit: str | None = None
    currency: str | None = None
    period_end: date | None = None
    data_as_of: str | None = None
    evidence_type: str
    calculation_method: str | None = None
    input_evidence_ids: list[str] = Field(default_factory=list)
    freshness: Literal["historical", "stale", "unverified", "reported_period"] = "unverified"
    quality_flags: list[str] = Field(default_factory=list)
    fiscal_period: str | None = None
    calculation_version: str | None = None
    source_field: str | None = None
    filing_date: date | None = None
    source_document_url: str | None = None

    @field_validator("value")
    @classmethod
    def finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Nonfinite financial value")
        return value


class FinancialClaim(BaseModel):
    claim_id: str
    statement: str
    metric: str
    value: Decimal
    supporting_evidence_ids: list[str] = Field(min_length=1)
    validation_status: Literal["VERIFIED"] = "VERIFIED"
    limitations: list[str] = Field(default_factory=list)


class ProposedClaim(BaseModel):
    statement: str
    metric: str
    value: Decimal
    supporting_evidence_ids: list[str] = Field(min_length=1)


class ProposedClaims(BaseModel):
    claims: list[ProposedClaim] = Field(default_factory=list, max_length=20)


class ResearchReport(BaseModel):
    execution_id: str
    session_id: str
    status: Literal["COMPLETE", "PARTIAL", "INSUFFICIENT_EVIDENCE"]
    answer: str
    claims: list[FinancialClaim] = Field(default_factory=list)
    evidence: list[FinancialEvidence] = Field(default_factory=list)
    sources: list[FinancialSource] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    schema_version: Literal[1] = 1
    agent_executions: list[AgentExecution] = Field(default_factory=list)
    model_metadata: dict = Field(default_factory=dict)
    researched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def references_resolve(self):
        source_ids = {row.source_id for row in self.sources}
        by_id = {row.evidence_id: row for row in self.evidence}
        if len(source_ids) != len(self.sources) or len(by_id) != len(self.evidence):
            raise ValueError("Duplicate source or evidence identity")
        for row in self.evidence:
            if row.source_id not in source_ids or any(ref not in by_id or ref == row.evidence_id for ref in row.input_evidence_ids):
                raise ValueError("Unresolved financial evidence lineage")
            if row.evidence_type == "CALCULATED" and (not row.input_evidence_ids or not row.calculation_method):
                raise ValueError("Calculated evidence lacks methodology or inputs")
        for claim in self.claims:
            refs = [by_id.get(ref) for ref in claim.supporting_evidence_ids]
            if any(ref is None for ref in refs) or not any(ref.metric == claim.metric and ref.value == claim.value for ref in refs):
                raise ValueError("Unresolved or inconsistent financial claim")
        return self


def _number(raw: Any) -> Decimal:
    if isinstance(raw, bool) or raw is None:
        raise EvidenceError("Invalid financial numeric value")
    try:
        result = Decimal(str(raw))
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise EvidenceError("Malformed financial numeric value") from exc
    if not result.is_finite():
        raise EvidenceError("Nonfinite financial numeric value")
    return result


RATIO_METRICS = {"net_profit_margin", "return_on_period_end_equity", "total_return",
                 "cagr", "annualized_volatility", "max_drawdown", "gross_margin", "operating_margin"}
DIMENSIONLESS_METRICS = {"sharpe_ratio", "current_ratio", "debt_to_equity", "interest_coverage"}


def freshness_label(metric: str, observation: str | None, period: date | None,
                    retrieved: datetime, fiscal_period: str | None = None) -> tuple[str, list[str]]:
    """Age is a policy label, never evidence of a live-feed entitlement.

    Historical closes are dated observations. Snapshot age thresholds tolerate
    ordinary weekends; calendars/entitlements are not inferred. Fiscal age does
    not prove that a record is the latest published filing.
    """
    if period:
        days = int(os.getenv("FINANCIAL_STATEMENT_MAX_AGE_DAYS", "550"))
        return ("stale", ["Reporting period exceeds configured age policy"]) if (retrieved.date()-period).days > days else ("reported_period", ["Latest filing availability not independently verified"])
    if metric in {"close", "open", "high", "low", "volume", "sma", "rsi", *RATIO_METRICS, "sharpe_ratio"} and observation:
        return "historical", []
    if not observation:
        return "unverified", ["Observation timestamp unavailable"]
    when = datetime.fromisoformat(observation.replace("Z", "+00:00"))
    when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
    seconds = int(os.getenv("MARKET_SNAPSHOT_MAX_AGE_SECONDS", "345600"))
    return ("stale", ["Snapshot exceeds configured age policy; market calendar not inferred"]) if (retrieved-when).total_seconds() > seconds else ("unverified", ["Feed delay and live entitlement unspecified"])


def _valid_time(value: Any, retrieved: datetime) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10:
            return parsed.date() <= retrieved.date()
        return parsed.tzinfo is not None and parsed <= retrieved
    except ValueError:
        return False


def normalize_tool_evidence(task_results: list[dict]) -> tuple[list[FinancialSource], list[FinancialEvidence]]:
    """Normalize actual tool results, nested statements and derived lineage.

    Unknown timestamps and units stay explicit. Future observations are rejected.
    Derived records require their original numeric input evidence and a method.
    """
    sources: dict[str, FinancialSource] = {}
    evidence: dict[str, FinancialEvidence] = {}

    def consume(result: Any):
        if not isinstance(result, dict):
            return
        if isinstance(result.get("comparison"), list):
            for child in result["comparison"][:5]:
                consume(child)
            return
        if result.get("provider") not in ALLOWED_PROVIDERS:
            return
        url = result.get("source_url")
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or parsed.hostname != "api.financialdatasets.ai" or parsed.username or parsed.password:
            return
        ticker = result.get("ticker")
        if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9.^_-]{1,20}", ticker):
            return
        try:
            retrieved = datetime.fromisoformat(result["retrieved_at"].replace("Z", "+00:00"))
            if retrieved.tzinfo is None or retrieved > datetime.now(timezone.utc):
                return
        except (KeyError, ValueError, TypeError, AttributeError):
            return
        src_id = "src_" + sha256((ticker + url + retrieved.isoformat()).encode()).hexdigest()[:18]
        sources[src_id] = FinancialSource(
            source_id=src_id, provider=result["provider"], source_url=url,
            retrieved_at=retrieved, data_as_of=result.get("data_timestamp"),
            cache_status=result.get("cache_status", "unspecified"),
            adjustment_status=result.get("adjustment_status", "unspecified"),
            exchange=result.get("exchange"), timezone=result.get("timezone"),
        )

        def rows(data):
            if isinstance(data, list):
                return data[:5000]
            if isinstance(data, dict):
                nested = [r for key in ("income_statements", "balance_sheets", "cash_flow_statements")
                          for r in data.get(key, []) if isinstance(r, dict)]
                return nested[:5000] if nested else [data]
            return []

        def add_records(data, derived=False, inputs=None):
            added = []
            for row in rows(data):
                if not isinstance(row, dict) or row.get("ticker", ticker) != ticker:
                    continue
                period_raw = row.get("report_period") or result.get("report_period")
                if period_raw and not _valid_time(period_raw, retrieved):
                    continue
                if any(row.get(key) and not _valid_time(row[key], retrieved)
                       for key in ("filing_date", "filing_datetime", "published_at")):
                    continue
                try:
                    period = date.fromisoformat(period_raw) if period_raw else None
                except (ValueError, TypeError):
                    continue  # One malformed fiscal period cannot crash all report validation.
                observation = row.get("time") or row.get("date") or (period_raw if period_raw else result.get("data_timestamp"))
                if observation and not _valid_time(observation, retrieved):
                    continue
                currency = row.get("currency") or result.get("currency")
                currency = currency if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency) else None
                for source_field, value in row.items():
                    metric = CANONICAL_METRICS.get(source_field, source_field)
                    if metric not in FINANCIAL_VALUES or len(evidence) >= 2000:
                        continue
                    if metric in {"price", "close", "open", "high", "low", "sma", "rsi"} and not observation:
                        continue
                    try:
                        amount = _number(value)
                    except EvidenceError:
                        continue
                    method = (result.get("calculation_methods") or {}).get(metric) if derived else None
                    if derived and (not method or not inputs):
                        continue
                    unit = "fraction" if metric in RATIO_METRICS else "dimensionless" if metric in DIMENSIONLESS_METRICS else "index" if metric == "rsi" else "shares" if metric == "volume" else currency
                    fresh, flags = freshness_label(metric, observation, period, retrieved, row.get("period"))
                    ev_id = "ev_" + sha256(f"{src_id}:{metric}:{amount}:{period}:{observation}:{derived}".encode()).hexdigest()[:18]
                    evidence[ev_id] = FinancialEvidence(
                        evidence_id=ev_id, source_id=src_id, symbol=ticker,
                        metric=metric, value=amount, currency=None if metric in RATIO_METRICS | DIMENSIONLESS_METRICS | {"volume", "rsi"} else currency,
                        unit=unit, period_end=period, data_as_of=str(observation) if observation else None,
                        evidence_type="CALCULATED" if derived else "PROVIDER_REPORTED",
                        calculation_method=method, input_evidence_ids=inputs or [],
                        freshness=fresh, quality_flags=flags,
                        fiscal_period=row.get("fiscal_period") if isinstance(row.get("fiscal_period"), str) else row.get("period") if isinstance(row.get("period"), str) else None,
                        calculation_version="1" if derived else None,
                        source_field=source_field,
                        filing_date=date.fromisoformat(row["filing_date"]) if isinstance(row.get("filing_date"), str) and len(row["filing_date"]) == 10 else None,
                        source_document_url=row["filing_url"] if isinstance(row.get("filing_url"), str) and row["filing_url"].startswith("https://") else None,
                    )
                    added.append(ev_id)
            return added

        if parsed.path.rstrip("/") == "/news":
            for row in rows(result.get("data")):
                if not isinstance(row, dict) or row.get("ticker", ticker) != ticker:
                    continue
                title, article_url, published = row.get("title"), row.get("url"), row.get("date")
                article = urlparse(article_url) if isinstance(article_url, str) else None
                if not isinstance(title, str) or not title.strip() or not article or article.scheme != "https" or not article.hostname or article.username or article.password:
                    continue
                if not _valid_time(published, retrieved):
                    continue
                article_id = "news_" + sha256((ticker + article_url.split("?")[0]).encode()).hexdigest()[:18]
                sources[article_id] = FinancialSource(
                    source_id=article_id, provider="financialdatasets.ai", source_url=article_url,
                    retrieved_at=retrieved, data_as_of=published, document_type="THIRD_PARTY_NEWS",
                    symbol=ticker, publisher=row.get("source") if isinstance(row.get("source"), str) else None,
                    headline="".join(c for c in title[:500] if ord(c) >= 32 and ord(c) != 127),
                    cache_status=result.get("cache_status", "unspecified"),
                )
        inputs = add_records(result.get("input_records")) if result.get("derived") else []
        if result.get("derived"):
            expected = sum(1 for row in rows(result.get("input_records")) if isinstance(row, dict)
                           for key, value in row.items() if CANONICAL_METRICS.get(key, key) in FINANCIAL_VALUES and value is not None)
            # Never certify an aggregate whose full input lineage was truncated
            # or rejected by timestamp/identity/numeric validation.
            if not expected or len(set(inputs)) != expected:
                return
        add_records(result.get("data"), derived=bool(result.get("derived")), inputs=inputs)

    for task in task_results[:50]:
        for call in task.get("tool_results", [])[:5]:
            if call.get("success") is False:
                continue
            consume(call.get("result"))
    records = list(evidence.values())
    source_ids = {e.source_id for e in records}
    return [s for key, s in sources.items() if key in source_ids or s.document_type == "THIRD_PARTY_NEWS"], records


def validate_claims(proposed: ProposedClaims, evidence: list[FinancialEvidence]) -> tuple[list[FinancialClaim], list[str]]:
    """Fail closed on invalid citations, dates, units, and numeric values.

    The prose is NOT auto-certified: accepted statements must include exactly
    the claimed metric's value and no unsupported additional numeric claims.
    """
    by_id = {item.evidence_id: item for item in evidence}
    accepted: list[FinancialClaim] = []
    rejected: list[str] = []
    for i, candidate in enumerate(proposed.claims):
        refs = [by_id.get(ref) for ref in candidate.supporting_evidence_ids]
        if not refs or any(ref is None for ref in refs):
            rejected.append("Unknown financial evidence citation")
            continue
        matching = [ref for ref in refs if ref.metric == candidate.metric and ref.value == candidate.value]
        if not matching:
            rejected.append("Financial metric or numeric value mismatch")
            continue
        if candidate.metric == "price" and (
                not matching[0].data_as_of or not matching[0].currency):
            rejected.append("Price claim lacks source timestamp or currency; display as partial evidence instead")
            continue
        # Avoid asserting incompatible source contexts as a single fact.
        if any(ref.symbol != matching[0].symbol for ref in refs):
            rejected.append("Mixed company evidence")
            continue
        if any(ref.currency and matching[0].currency and ref.currency != matching[0].currency for ref in refs):
            rejected.append("Currency mismatch")
            continue
        if any(ref.period_end and matching[0].period_end and ref.period_end != matching[0].period_end for ref in refs):
            rejected.append("Fiscal reporting period mismatch")
            continue
        # A validated figure for one symbol cannot be attributed to another.
        if not re.search(r"(?<!\w)" + re.escape(matching[0].symbol) + r"(?!\w)",
                         candidate.statement, flags=re.IGNORECASE):
            rejected.append("Claim does not identify the cited company")
            continue
        # Do not accept extra numeric assertions embedded in prose.
        nums = re.findall(r"(?<![\w.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?", candidate.statement)
        if len(nums) != 1:
            rejected.append("Claim prose must contain one independently validated numerical assertion")
            continue
        try:
            statement_number = _number(nums[0].rstrip("%").replace(",", ""))
        except EvidenceError:
            rejected.append("Malformed numeric statement")
            continue
        if statement_number != candidate.value:
            rejected.append("Numeric assertion differs from cited evidence")
            continue
        accepted.append(FinancialClaim(
            claim_id=f"claim_{i+1}",
            # Matching numbers cannot certify surrounding model prose. Render
            # only deterministic facts with their metric, units and timestamp.
            statement=(f"{matching[0].symbol} {candidate.metric}: {candidate.value} "
                       f"({matching[0].unit or matching[0].currency or 'unit unspecified'}; "
                       f"{matching[0].period_end or matching[0].data_as_of or 'date unspecified'})"),
            metric=candidate.metric, value=candidate.value,
            supporting_evidence_ids=candidate.supporting_evidence_ids,
        ))
    return accepted, rejected


def verified_report(execution_id: str, session_id: str,
                    sources: list[FinancialSource], evidence: list[FinancialEvidence],
                    claims: list[FinancialClaim], limitations: list[str]) -> ResearchReport:
    if len(evidence) >= 2000:
        limitations = [*limitations, "Evidence record budget reached; coverage is truncated."]
    groups: dict[tuple, set] = {}
    for row in evidence:
        key = (row.symbol, row.metric, row.period_end, row.data_as_of, row.currency, row.unit)
        groups.setdefault(key, set()).add(row.value)
    if any(len(values) > 1 for values in groups.values()):
        limitations = [*limitations, "Conflicting values exist for the same instrument, metric, date and unit. No source was silently preferred."]
    if any(row.freshness == "stale" for row in evidence):
        limitations = [*limitations, "Some observations exceed the configured age policy; stale figures are not current quotes."]
    if claims:
        answer = "\n".join(
            f"- {claim.statement} [{', '.join(claim.supporting_evidence_ids)}]"
            for claim in claims
        )
        status = "COMPLETE" if not limitations else "PARTIAL"
    elif evidence:
        # Construct a deterministic fall-back from actual recorded financial evidence,
        # not the potentially ungrounded LLM synthesis.
        answer = "\n".join(
            f"- {row.symbol} {row.metric}: {row.value} "
            f"({row.currency or row.unit or 'unit unspecified'}; "
            f"{row.period_end or row.data_as_of or 'date unspecified'}) [{row.evidence_id}]"
            for row in evidence[:20]
        )
        status = "PARTIAL"
        limitations = [*limitations, "No validated LLM claims; displaying source-backed metrics only."]
    elif any(source.headline for source in sources):
        answer = "Source-reported news headlines are available; no numerical analysis was verified."
        status = "PARTIAL"
        limitations = [*limitations, "Headlines are publisher reports, not independently confirmed events or investment conclusions."]
    else:
        answer = "No verified numerical financial evidence was available for this request."
        status = "INSUFFICIENT_EVIDENCE"
        limitations = [*limitations, "No supported provider records were validated."]
    news = [source for source in sources if source.headline]
    if news:
        answer += "\n\nPublisher-reported news:\n" + "\n".join(
            f"- {source.symbol}: {source.headline} ({source.publisher or 'publisher unspecified'}; {source.data_as_of}) [{source.source_id}]"
            for source in news[:10]
        )
    if limitations:
        answer += "\n\nLimitations:\n" + "\n".join(f"- {item}" for item in limitations)
    if sources:
        answer += "\n\nSources:\n" + "\n".join(
            f"- {source.source_id}: {source.source_url} (retrieved {source.retrieved_at.isoformat()}; cache {source.cache_status}; adjustments {source.adjustment_status})"
            for source in sources
        )
    return ResearchReport(
        execution_id=execution_id, session_id=session_id,
        status=status, answer=answer, sources=sources, evidence=evidence,
        claims=claims, limitations=limitations,
    )
