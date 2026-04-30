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
