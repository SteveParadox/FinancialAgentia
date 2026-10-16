"""Deterministic research evaluation, separate from model self-assessment."""

from collections import defaultdict

from pydantic import BaseModel, Field


class VerificationOutcome(BaseModel):
    version: str = "financial-verification-v1"
    source_count: int = Field(ge=0)
    provider_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    supported_claim_count: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    conflicting_observation_groups: int = Field(ge=0)
    independent_matching_groups: int = Field(ge=0)
    equivalent_matching_groups: int = Field(ge=0)
    checks: list[str]
    limitations: list[str]


def verify_research(sources, evidence, claims):
    source_by_id = {row.source_id: row for row in sources}
    groups = defaultdict(list)
    for row in evidence:
        # Unknown duration is NOT automatically comparable with a known duration.
        key = (
            row.symbol,
            row.metric,
            row.period_start,
            row.period_end,
            row.data_as_of,
            row.currency,
            row.unit,
        )
        groups[key].append(row)
    independent = equivalent = conflicts = 0
    for rows in groups.values():
        if len({row.value for row in rows}) > 1:
            conflicts += 1
        providers = {source_by_id[row.source_id].provider for row in rows}
        issuer_ids = {row.issuer_id for row in rows}
        if len(providers) > 1 and len(issuer_ids) == 1 and None not in issuer_ids:
            independent += 1
            equivalent += len({row.value for row in rows}) == 1
    limitations = []
    if not independent:
        limitations.append(
            "No independently sourced, semantically matching observations were available for cross-source verification."
        )
    if any(
        row.period_end
        and not row.period_start
        and row.metric in {"revenue", "net_income", "operating_cash_flow"}
        for row in evidence
    ):
        limitations.append(
            "Some flow observations lack period start dates; annual/quarterly equivalence is not certified."
        )
    return VerificationOutcome(
        source_count=len(sources),
        provider_count=len({s.provider for s in sources}),
        evidence_count=len(evidence),
        supported_claim_count=len(claims),
        stale_count=sum(row.freshness == "stale" for row in evidence),
        conflicting_observation_groups=conflicts,
        independent_matching_groups=independent,
        equivalent_matching_groups=equivalent,
        checks=[
            "schema",
            "source identity",
            "nonfinite rejection",
            "future date rejection",
            "citation resolution",
            "exact numeric claims",
            "duration/currency comparison",
        ],
        limitations=limitations,
    )


def planning_policy(query, available):
    """Capabilities guide the existing planner; never claim an unimplemented tool."""
    lower = query.lower()
    roles = ["evidence verification", "synthesis"]
    if any(
        word in lower
        for word in [
            "valuation",
            "fundamental",
            "profit",
            "revenue",
            "company",
            "earnings",
        ]
    ):
        roles.insert(0, "fundamentals")
    if any(
        word in lower
        for word in ["price", "market", "trend", "historical", "volatility"]
    ):
        roles.insert(0, "market and deterministic risk")
    if any(word in lower for word in ["compar", "versus", " vs "]):
        roles.insert(0, "comparison with aligned periods and currency")
    if "valuation" in lower:
        roles.append(
            "valuation only where licensed inputs and deterministic calculations exist; otherwise disclose unavailable metrics"
        )
    if any(word in lower for word in ["macro", "inflation", "interest rate"]):
        roles.append(
            "macroeconomic coverage is unavailable unless a corresponding configured tool exists"
        )
    return {
        "selected_responsibilities": roles,
        "configured_tools": sorted(available),
        "constraints": [
            "Do not execute unrelated analyses",
            "Never use saved context as current financial evidence",
            "Keep filing duration contexts separate",
            "Do not manufacture unavailable ratios or qualitative claims",
        ],
    }
