import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock
from enrichers.bse_xbrl import BSEXBRLEnricher
from enrichers import EnrichmentPayload


def test_bse_enricher_skips_lender_without_cin():
    enricher = BSEXBRLEnricher()
    results = enricher.enrich_lender(lender_id="1", cin=None, policy_map={})
    assert results == []


def test_bse_enricher_returns_payload_with_correct_source():
    enricher = BSEXBRLEnricher()
    with patch.object(enricher, '_cin_to_scrip_code', return_value="532343"), \
         patch.object(enricher, '_fetch_fpc_data', return_value={
             'interest_rate_min': 12.5, 'interest_rate_max': 18.0,
             'loan_amount_min': 5.0, 'loan_amount_max': 500.0
         }):
        results = enricher.enrich_lender(
            lender_id="1",
            cin="U65910MH2010PTC123456",
            policy_map={"MSME Loan": "101"}
        )
    assert len(results) == 2
    assert all(r.source == "bse_xbrl" for r in results)
    assert all(r.confidence == 0.95 for r in results)
    assert all(isinstance(r, EnrichmentPayload) for r in results)


def test_bse_enricher_returns_empty_when_no_scrip_code():
    enricher = BSEXBRLEnricher()
    with patch.object(enricher, '_cin_to_scrip_code', return_value=None):
        results = enricher.enrich_lender(
            lender_id="1",
            cin="U65910MH2010PTC123456",
            policy_map={"MSME Loan": "101"}
        )
    assert results == []


def test_bse_enricher_returns_empty_when_no_fpc_data():
    enricher = BSEXBRLEnricher()
    with patch.object(enricher, '_cin_to_scrip_code', return_value="532343"), \
         patch.object(enricher, '_fetch_fpc_data', return_value={}):
        results = enricher.enrich_lender(
            lender_id="1",
            cin="U65910MH2010PTC123456",
            policy_map={"MSME Loan": "101"}
        )
    assert results == []


from enrichers.fpc_pdf import FPCPDFEnricher, parse_fpc_table


def test_parse_fpc_table_extracts_rate_range():
    rows = [
        ["Loan Product", "Min Rate (%)", "Max Rate (%)", "Min Amount (Lakh)", "Max Amount (Lakh)"],
        ["MSME Term Loan", "12.50", "18.00", "5", "500"],
        ["Working Capital", "13.00", "20.00", "2", "200"],
    ]
    results = parse_fpc_table(rows)
    assert len(results) == 2
    assert results[0]["interest_rate_min"] == 12.50
    assert results[0]["interest_rate_max"] == 18.00
    assert results[0]["loan_amount_min"] == 5.0
    assert results[0]["loan_amount_max"] == 500.0


def test_parse_fpc_table_skips_unparseable_rows():
    rows = [
        ["Product", "Rate Min", "Rate Max", "Amount Min", "Amount Max"],
        ["MSME Loan", "N/A", "N/A", "-", "-"],
        ["Home Loan", "8.50", "12.00", "10", "5000"],
    ]
    results = parse_fpc_table(rows)
    assert len(results) == 1
    assert results[0]["interest_rate_min"] == 8.50


def test_fpc_enricher_returns_empty_on_no_website():
    enricher = FPCPDFEnricher()
    results = enricher.enrich_lender(lender_id="1", website_url=None, policy_map={"MSME Loan": "101"})
    assert results == []


from enrichers.bankbazaar import BankBazaarEnricher, parse_bankbazaar_rate_table


def test_parse_bankbazaar_rate_table_extracts_ranges():
    html = """
    <table>
      <tr><th>Bank/NBFC</th><th>Interest Rate</th><th>Loan Amount</th><th>Tenure</th></tr>
      <tr><td>ABC Finance</td><td>12.5% - 18%</td><td>5 Lakh - 500 Lakh</td><td>12 - 60 months</td></tr>
    </table>
    """
    results = parse_bankbazaar_rate_table(html, lender_name="ABC Finance")
    assert len(results) == 1
    assert results[0]["interest_rate_min"] == 12.5
    assert results[0]["interest_rate_max"] == 18.0
    assert results[0]["loan_amount_min"]   == 5.0
    assert results[0]["loan_amount_max"]   == 500.0
    assert results[0]["tenure_min"]        == 12
    assert results[0]["tenure_max"]        == 60


def test_parse_bankbazaar_skips_non_matching_rows():
    html = """
    <table>
      <tr><th>Bank</th><th>Interest Rate</th></tr>
      <tr><td>XYZ Bank</td><td>9% - 12%</td></tr>
      <tr><td>ABC Finance</td><td>12.5% - 18%</td></tr>
    </table>
    """
    results = parse_bankbazaar_rate_table(html, lender_name="ABC Finance")
    assert len(results) == 1
    assert results[0]["interest_rate_min"] == 12.5


def test_bankbazaar_enricher_returns_empty_for_unknown_loan_type():
    enricher = BankBazaarEnricher()
    results = enricher.enrich_lender(
        lender_id="1", lender_name="ABC Finance",
        policy_map={"Unknown Loan Type": "101"}
    )
    assert results == []
