"""
BSE XBRL enricher (Rank 4 — highest trust).
Fetches annual XBRL filings from BSE for listed NBFCs.
Extracts Fair Practice Code interest rate and loan amount ranges.
"""
from __future__ import annotations
import logging
import time
from typing import Optional

import requests

from enrichers import EnrichmentPayload

log = logging.getLogger(__name__)

BSE_CIN_LOOKUP  = "https://api.bseindia.com/BseIndiaAPI/api/listofscripData/w?scripCode=&Group=&industry=&segment=EQ&status=A"
BSE_XBRL_BASE   = "https://www.bseindia.com/xml-data/corpfiling/"
REQUEST_TIMEOUT = 15
RATE_DELAY      = 1.0


class BSEXBRLEnricher:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "MITRAM360-DataPipeline/1.0"})

    def _cin_to_scrip_code(self, cin: str) -> Optional[str]:
        try:
            resp = self._session.get(BSE_CIN_LOOKUP, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            for item in resp.json().get("Table", []):
                if item.get("CIN_NO", "").strip().upper() == cin.upper():
                    return str(item.get("SCRIP_CD", "")).strip()
        except Exception as e:
            log.warning("CIN lookup failed for %s: %s", cin, e)
        return None

    def _fetch_fpc_data(self, scrip_code: str) -> dict:
        try:
            url = f"{BSE_XBRL_BASE}{scrip_code}/FPC/"
            resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            data = resp.json()
            return {
                "interest_rate_min": float(data.get("InterestRateMin", 0) or 0),
                "interest_rate_max": float(data.get("InterestRateMax", 0) or 0),
                "loan_amount_min":   float(data.get("LoanAmountMinLakhs", 0) or 0),
                "loan_amount_max":   float(data.get("LoanAmountMaxLakhs", 0) or 0),
            }
        except Exception as e:
            log.warning("XBRL fetch failed for scrip %s: %s", scrip_code, e)
            return {}

    def enrich_lender(self, lender_id: str, cin: Optional[str],
                      policy_map: dict[str, str]) -> list[EnrichmentPayload]:
        if not cin:
            return []
        scrip_code = self._cin_to_scrip_code(cin)
        if not scrip_code:
            return []
        time.sleep(RATE_DELAY)
        fpc = self._fetch_fpc_data(scrip_code)
        if not fpc:
            return []
        payloads = []
        for loan_type, policy_id in policy_map.items():
            raw = {"lender_id": lender_id, "cin": cin, "scrip_code": scrip_code, "fpc": fpc}
            if fpc.get("interest_rate_min") and fpc.get("interest_rate_max"):
                payloads.append(EnrichmentPayload(
                    policy_id=policy_id,
                    field="interest_rate",
                    value_min=fpc["interest_rate_min"],
                    value_max=fpc["interest_rate_max"],
                    source="bse_xbrl",
                    confidence=0.95,
                    raw_value=raw,
                ))
            if fpc.get("loan_amount_min") and fpc.get("loan_amount_max"):
                payloads.append(EnrichmentPayload(
                    policy_id=policy_id,
                    field="loan_amount",
                    value_min=fpc["loan_amount_min"],
                    value_max=fpc["loan_amount_max"],
                    source="bse_xbrl",
                    confidence=0.95,
                    raw_value=raw,
                ))
        return payloads
