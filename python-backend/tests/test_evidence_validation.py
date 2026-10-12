"""Deterministic verification of numerical claims and source identity."""
from datetime import datetime, timezone
from decimal import Decimal

from dexter_py.agent.financial_evidence import (
    FinancialEvidence, FinancialSource, ProposedClaims, ProposedClaim,
    normalize_tool_evidence, validate_claims, verified_report,
)


def real_fixture():
    tool = {
        "tool_results": [
            {"tool": "get_price_snapshot", "result": {
                "ticker": "AAPL", "provider": "financialdatasets.ai",
                "source_url": "https://api.financialdatasets.ai/prices/snapshot/?ticker=AAPL",
                "retrieved_at": "2026-10-10T12:00:00+00:00",
                "data_timestamp": "2026-10-10T11:59:00+00:00",
                "data": {"price": 195.25, "ticker": "AAPL", "currency": "USD"},
            }}
        ]
    }
    return [tool]


def test_source_and_price_are_normalized():
    sources, records = normalize_tool_evidence(real_fixture())
    assert len(sources) == len(records) == 1
    assert records[0].value == Decimal("195.25")
    assert records[0].source_id == sources[0].source_id


def test_fabricated_provider_host_cannot_become_evidence():
    bad = real_fixture()
    bad[0]["tool_results"][0]["result"]["source_url"] = "https://api.financialdatasets.ai.attacker.test/"
    assert normalize_tool_evidence(bad) == ([], [])


def test_unsubstantiated_claim_rejected():
    _, records = normalize_tool_evidence(real_fixture())
    proposed = ProposedClaims(claims=[
        ProposedClaim(statement="AAPL costs 900", metric="price", value=900,
                      supporting_evidence_ids=[records[0].evidence_id])
    ])
    accepted, rejected = validate_claims(proposed, records)
    assert not accepted and rejected


def test_invented_citation_rejected():
    _, records = normalize_tool_evidence(real_fixture())
    proposed = ProposedClaims(claims=[
        ProposedClaim(statement="AAPL costs 195.25", metric="price", value=195.25,
                      supporting_evidence_ids=["ev_nonexistent"])
    ])
    assert not validate_claims(proposed, records)[0]


def test_validated_claim_and_report():
    sources, evidence = normalize_tool_evidence(real_fixture())
    proposed = ProposedClaims(claims=[
        ProposedClaim(statement="AAPL price is 195.25", metric="price",
                      value="195.25", supporting_evidence_ids=[evidence[0].evidence_id])
    ])
    claims, rejected = validate_claims(proposed, evidence)
    assert len(claims) == 1 and not rejected
    report = verified_report("run", "session", sources, evidence, claims, [])
    assert report.status == "COMPLETE"
    assert evidence[0].evidence_id in report.answer


def test_missing_source_is_insufficient_evidence():
    report = verified_report("run", "session", [], [], [], [])
    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert not report.claims


def test_inconsistent_periods_fail_claim_validation():
    ref1 = FinancialEvidence(
        evidence_id="ev_a", source_id="src_1", symbol="AAPL",
        metric="revenue", value="100", currency="USD",
        period_end="2025-12-31", evidence_type="PROVIDER_REPORTED")
    ref2 = FinancialEvidence(
        evidence_id="ev_b", source_id="src_1", symbol="AAPL",
        metric="net_income", value="20", currency="USD",
        period_end="2024-12-31", evidence_type="PROVIDER_REPORTED")
    proposed = ProposedClaims(claims=[
        ProposedClaim(statement="Revenue was 100", metric="revenue", value=100,
                      supporting_evidence_ids=["ev_a", "ev_b"])
    ])
    assert not validate_claims(proposed, [ref1, ref2])[0]

def test_wrong_company_attribution_is_rejected():
    _, records = normalize_tool_evidence(real_fixture())
    proposed = ProposedClaims(claims=[
        ProposedClaim(statement="MSFT price is 195.25", metric="price", value="195.25",
                      supporting_evidence_ids=[records[0].evidence_id])
    ])
    assert not validate_claims(proposed, records)[0]

def test_price_without_currency_must_remain_partial():
    _, evidence = normalize_tool_evidence(real_fixture())
    unpriced = evidence[0].model_copy(update={"currency": None})
    proposed = ProposedClaims(claims=[
        ProposedClaim(statement="AAPL price is 195.25", metric="price", value="195.25",
                      supporting_evidence_ids=[unpriced.evidence_id])
    ])
    assert not validate_claims(proposed, [unpriced])[0]
