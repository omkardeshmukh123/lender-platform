"""
BankBazaar enricher (Rank 2).
Scrapes BankBazaar/PaisaBazaar loan comparison pages for structured rate data.
Emits EnrichmentPayload objects — routes through BankManager, never writes to policies directly.
"""
from __future__ import annotations
import logging
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from enrichers import EnrichmentPayload

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
RATE_DELAY      = 2.0

LOAN_TYPE_URLS = {
    "MSME Loan":       "https://www.bankbazaar.com/business-loan.html",
    "Personal Loan":   "https://www.bankbazaar.com/personal-loan.html",
    "Home Loan":       "https://www.bankbazaar.com/home-loan.html",
    "LAP":             "https://www.bankbazaar.com/loan-against-property.html",
    "Working Capital": "https://www.bankbazaar.com/working-capital-loan.html",
    "Gold Loan":       "https://www.bankbazaar.com/gold-loan.html",
    "Vehicle Loan":    "https://www.bankbazaar.com/car-loan.html",
    "Education Loan":  "https://www.bankbazaar.com/education-loan.html",
    "Microfinance":    "https://www.bankbazaar.com/microfinance.html",
}


def _parse_range(text: str):
    nums = re.findall(r"[\d]+\.?[\d]*", text.replace(",", ""))
    if len(nums) >= 2:
        return float(nums[0]), float(nums[-1])
    if len(nums) == 1:
        return float(nums[0]), float(nums[0])
    return None, None


def _normalize_amount(value: float, text: str) -> float:
    text_lower = text.lower()
    if "crore" in text_lower or " cr" in text_lower:
        return value * 100
    return value


def parse_bankbazaar_rate_table(html: str, lender_name: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    name_lower = lender_name.lower()

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        rate_col = next((i for i, h in enumerate(headers) if "interest" in h or "rate" in h), None)
        amt_col  = next((i for i, h in enumerate(headers) if "amount" in h or "loan" in h), None)
        ten_col  = next((i for i, h in enumerate(headers) if "tenure" in h or "term" in h), None)

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            row_text = cells[0].get_text(strip=True).lower()
            if name_lower not in row_text and not any(w in row_text for w in name_lower.split() if len(w) > 3):
                continue

            entry = {}
            if rate_col is not None and rate_col < len(cells):
                rmin, rmax = _parse_range(cells[rate_col].get_text())
                if rmin and rmax:
                    entry["interest_rate_min"] = rmin
                    entry["interest_rate_max"] = rmax

            if amt_col is not None and amt_col < len(cells):
                cell_text = cells[amt_col].get_text()
                amin, amax = _parse_range(cell_text)
                if amin and amax:
                    entry["loan_amount_min"] = _normalize_amount(amin, cell_text)
                    entry["loan_amount_max"] = _normalize_amount(amax, cell_text)

            if ten_col is not None and ten_col < len(cells):
                tmin, tmax = _parse_range(cells[ten_col].get_text())
                if tmin and tmax:
                    entry["tenure_min"] = int(tmin)
                    entry["tenure_max"] = int(tmax)

            if entry:
                results.append(entry)

    return results


class BankBazaarEnricher:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "MITRAM360-DataPipeline/1.0"})
        self._page_cache: dict = {}

    def _fetch_page(self, url: str) -> str:
        if url in self._page_cache:
            return self._page_cache[url]
        try:
            time.sleep(RATE_DELAY)
            resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            self._page_cache[url] = resp.text
            return resp.text
        except Exception as e:
            log.warning("BankBazaar fetch failed %s: %s", url, e)
            return ""

    def enrich_lender(self, lender_id: str, lender_name: str, policy_map: dict) -> list:
        payloads = []
        for loan_type, policy_id in policy_map.items():
            url = LOAN_TYPE_URLS.get(loan_type)
            if not url:
                continue
            html = self._fetch_page(url)
            if not html:
                continue
            entries = parse_bankbazaar_rate_table(html, lender_name)
            if not entries:
                continue
            entry = entries[0]
            raw = {"lender_id": lender_id, "url": url, "lender_name": lender_name, "entry": entry}

            for field_key, min_key, max_key in [
                ("interest_rate", "interest_rate_min", "interest_rate_max"),
                ("loan_amount",   "loan_amount_min",   "loan_amount_max"),
            ]:
                if min_key in entry and max_key in entry:
                    payloads.append(EnrichmentPayload(
                        policy_id=policy_id, field=field_key,
                        value_min=entry[min_key], value_max=entry[max_key],
                        source="bankbazaar", confidence=0.70, raw_value=raw,
                    ))

            if "tenure_min" in entry and "tenure_max" in entry:
                payloads.append(EnrichmentPayload(
                    policy_id=policy_id, field="tenure",
                    value_min=float(entry["tenure_min"]), value_max=float(entry["tenure_max"]),
                    source="bankbazaar", confidence=0.70, raw_value=raw,
                ))
        return payloads
