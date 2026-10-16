from datetime import datetime, timezone, date
import pytest
from pydantic import ValidationError
from dexter_py.agent.financial_evidence import freshness_label, ResearchReport, normalize_tool_evidence
from test_evidence_validation import real_fixture


def test_freshness_never_guesses_live_entitlements_and_tolerates_weekend(monkeypatch):
    now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
    monkeypatch.setenv('MARKET_SNAPSHOT_MAX_AGE_SECONDS', '345600')
    assert freshness_label('price', '2026-10-09T20:00:00Z', None, now)[0] == 'unverified'
    assert freshness_label('price', '2026-09-01T20:00:00Z', None, now)[0] == 'stale'
    assert freshness_label('close', '2021-01-01', None, now)[0] == 'historical'
    assert freshness_label('revenue', None, date(2026, 6, 30), now)[0] == 'reported_period'
    monkeypatch.setenv('FINANCIAL_STATEMENT_MAX_AGE_DAYS', '30')
    assert freshness_label('revenue', None, date(2026, 6, 30), now)[0] == 'stale'


def test_sharpe_is_dimensionless_and_period_timestamp_is_per_record():
    data = real_fixture(); result = data[0]['tool_results'][0]['result']
    result.update(data={'sharpe_ratio': 1.5}, derived=True,
                  input_records=[{'close': 100, 'time': '2026-10-09', 'currency': 'USD'}],
                  calculation_methods={'sharpe_ratio': 'mean excess / sample deviation; annualized sqrt(252)'})
    _, evidence = normalize_tool_evidence(data)
    sharpe = next(row for row in evidence if row.metric == 'sharpe_ratio')
    assert sharpe.unit == 'dimensionless' and sharpe.currency is None
    result.update(derived=False, data=[{'revenue': 100, 'currency': 'USD', 'report_period': '2025-12-31'},
                                     {'revenue': 80, 'currency': 'USD', 'report_period': '2024-12-31'}])
    _, evidence = normalize_tool_evidence(data)
    assert {row.data_as_of for row in evidence} == {'2025-12-31', '2024-12-31'}


def test_report_schema_rejects_dangling_evidence():
    sources, evidence = normalize_tool_evidence(real_fixture())
    with pytest.raises(ValidationError):
        ResearchReport(execution_id='x', session_id='s', status='PARTIAL', answer='observations',
                       evidence=evidence, sources=[])
    invalid = evidence[0].model_copy(update={'input_evidence_ids': ['missing'], 'evidence_type': 'CALCULATED'})
    with pytest.raises(ValidationError):
        ResearchReport(execution_id='x', session_id='s', status='PARTIAL', answer='observations',
                       evidence=[invalid], sources=sources)


def test_incomplete_calculation_inputs_are_never_certified():
    data = real_fixture(); result = data[0]['tool_results'][0]['result']
    result.update(data={'total_return': .1}, derived=True,
                  input_records=[{'close': 100, 'time': '2026-10-09', 'currency': 'USD'},
                                 {'close': 'invalid', 'time': '2026-10-10', 'currency': 'USD'}],
                  calculation_methods={'total_return': 'last / first - 1'})
    _, evidence = normalize_tool_evidence(data)
    assert not any(row.metric == 'total_return' for row in evidence)


def test_conflicting_observations_force_partial_report():
    from dexter_py.agent.financial_evidence import verified_report
    sources, evidence = normalize_tool_evidence(real_fixture())
    different = evidence[0].model_copy(update={'evidence_id': 'conflict', 'value': evidence[0].value + 1})
    report = verified_report('job', 'session', sources, [*evidence, different], [], [])
    assert report.status == 'PARTIAL'
    assert any('Conflicting' in warning for warning in report.limitations)


def test_documented_operating_cash_flow_field_and_filing_dates_are_preserved():
    data = real_fixture(); result = data[0]['tool_results'][0]['result']
    result['data'] = [{'net_cash_flow_from_operations': 250, 'currency': 'USD', 'report_period': '2025-12-31',
                       'filing_date': '2026-02-01', 'filing_datetime': '2026-02-01T14:30:00Z',
                       'fiscal_period': 'FY', 'filing_url': 'https://www.sec.gov/Archives/edgar/data/fixture'}]
    _, evidence = normalize_tool_evidence(data)
    assert evidence[0].metric == 'operating_cash_flow'
    assert evidence[0].source_field == 'net_cash_flow_from_operations'
    assert evidence[0].filing_date.isoformat() == '2026-02-01' and evidence[0].fiscal_period == 'FY'
    result['data'][0]['filing_date'] = '2099-01-01'
    assert normalize_tool_evidence(data) == ([], [])
