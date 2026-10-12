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
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

ALLOWED_PROVIDERS = {"financialdatasets.ai"}
FINANCIAL_VALUES = {
    "price", "close", "open", "high", "low", "volume",
    "revenue", "net_income", "gross_profit", "operating_income",
    "total_assets", "total_liabilities", "shareholders_equity",
    "total_equity", "earnings_per_share", "market_cap",
    "sma", "rsi", "net_profit_margin", "return_on_period_end_equity",
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
    validation_status: str = "VERIFIED"
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
    status: str
    answer: str
    claims: list[FinancialClaim] = Field(default_factory=list)
    evidence: list[FinancialEvidence] = Field(default_factory=list)
    sources: list[FinancialSource] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    researched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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


def normalize_tool_evidence(task_results: list[dict]) -> tuple[list[FinancialSource], list[FinancialEvidence]]:
    """Accept known provider-backed records, never fictional tool outputs.

    Missing source metadata never becomes a verified citation. Input structures
    and unsupported metrics are rejected instead of speculatively mapped.
    """
    sources: list[FinancialSource] = []
    evidence: list[FinancialEvidence] = []
    for task in task_results[:50]:
        for call in task.get("tool_results", [])[:5]:
            result = call.get("result")
            if not isinstance(result, dict) or result.get("provider") not in ALLOWED_PROVIDERS:
                continue
            url = result.get("source_url")
            parsed = urlparse(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != "https" or parsed.hostname != "api.financialdatasets.ai":
                continue
            if call.get("tool") == "calculate_financial_ratios":
                # Derived metrics need explicit source-input lineage.
                continue
            ticker = result.get("ticker")
            if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9.^_-]{1,20}", ticker):
                continue
            try:
                retrieved = datetime.fromisoformat(result["retrieved_at"])
                if retrieved.tzinfo is None:
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            src_id = "src_" + sha256((ticker + url + retrieved.isoformat()).encode()).hexdigest()[:18]
            sources.append(FinancialSource(
                source_id=src_id, provider=result["provider"], source_url=url,
                retrieved_at=retrieved, data_as_of=result.get("data_timestamp"),
            ))
            data = result.get("data")
            rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
            for idx, row in enumerate(rows[:100]):
                if not isinstance(row, dict):
                    continue
                if row.get("ticker", ticker) != ticker:
                    continue
                try:
                    period = date.fromisoformat(row["report_period"]) if row.get("report_period") else None
                except ValueError:
                    continue
                currency = row.get("currency") if isinstance(row.get("currency"), str) else None
                for metric, value in row.items():
                    if metric == "price" and not result.get("data_timestamp"):
                        # Never certify an un-timestamped quote as a market price.
                        continue
                    if metric not in FINANCIAL_VALUES:
                        continue
                    try:
                        amount = _number(value)
                    except EvidenceError:
                        continue
                    entry_id = "ev_" + sha256(
                        f"{src_id}:{idx}:{metric}:{amount}".encode()
                    ).hexdigest()[:18]
                    evidence.append(FinancialEvidence(
                        evidence_id=entry_id, source_id=src_id, symbol=ticker,
                        metric=metric, value=amount, currency=currency,
                        unit=currency if metric != "volume" else "shares",
                        period_end=period, data_as_of=result.get("data_timestamp"),
                        evidence_type="PROVIDER_REPORTED",
                    ))
                    if len(evidence) >= 200:
                        break
                if len(evidence) >= 200:
                    break
            if len(evidence) >= 200:
                break
        if len(evidence) >= 200:
            break
    valid_source_ids = {e.source_id for e in evidence}
    return [s for s in sources if s.source_id in valid_source_ids], evidence


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
            claim_id=f"claim_{i+1}", statement=candidate.statement,
            metric=candidate.metric, value=candidate.value,
            supporting_evidence_ids=candidate.supporting_evidence_ids,
        ))
    return accepted, rejected


def verified_report(execution_id: str, session_id: str,
                    sources: list[FinancialSource], evidence: list[FinancialEvidence],
                    claims: list[FinancialClaim], limitations: list[str]) -> ResearchReport:
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
    else:
        answer = "No verified numerical financial evidence was available for this request."
        status = "INSUFFICIENT_EVIDENCE"
        limitations = [*limitations, "No supported provider records were validated."]
    return ResearchReport(
        execution_id=execution_id, session_id=session_id,
        status=status, answer=answer, sources=sources, evidence=evidence,
        claims=claims, limitations=limitations,
    )
