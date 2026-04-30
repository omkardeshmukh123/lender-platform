"""
FPC PDF enricher (Rank 3).
Downloads annual report PDFs from lender IR pages.
Parses Fair Practice Code / Interest Rate Policy tables with pdfplumber.
No AI — deterministic table extraction only.
"""
from __future__ import annotations
import io
import logging
import re
import time
from typing import Optional

import pdfplumber
import requests

from enrichers import EnrichmentPayload

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
RATE_DELAY      = 2.0
PDF_LINK_PATTERNS = [
    r'/annual.report',
    r'/investor.relation',
    r'/financials',
    r'annual_report.*\.pdf',
    r'ar\d{4}.*\.pdf',
]


def _safe_float(val) -> Optional[float]:
    if not val:
        return None
    cleaned = re.sub(r"[^0-9.]", "", str(val).strip())
    try:
        f = float(cleaned)
        return f if f > 0 else None
    except ValueError:
        return None


def parse_fpc_table(rows: list) -> list:
    if len(rows) < 2:
        return []
    header = [str(h).lower().strip() if h else "" for h in rows[0]]

    def find_col(*keywords):
        for i, h in enumerate(header):
            if any(kw in h for kw in keywords):
                return i
        return None

    rate_min_col   = find_col("min rate", "rate min", "minimum rate", "from")
    rate_max_col   = find_col("max rate", "rate max", "maximum rate", "upto", "up to")
    amount_min_col = find_col("min amount", "amount min", "minimum amount", "loan min")
    amount_max_col = find_col("max amount", "amount max", "maximum amount", "loan max")

    results = []
    for row in rows[1:]:
        entry = {}
        if rate_min_col is not None and rate_max_col is not None:
            rmin = _safe_float(row[rate_min_col] if rate_min_col < len(row) else None)
            rmax = _safe_float(row[rate_max_col] if rate_max_col < len(row) else None)
            if rmin and rmax:
                entry["interest_rate_min"] = rmin
                entry["interest_rate_max"] = rmax
        if amount_min_col is not None and amount_max_col is not None:
            amin = _safe_float(row[amount_min_col] if amount_min_col < len(row) else None)
            amax = _safe_float(row[amount_max_col] if amount_max_col < len(row) else None)
            if amin and amax:
                entry["loan_amount_min"] = amin
                entry["loan_amount_max"] = amax
        if entry:
            results.append(entry)
    return results


class FPCPDFEnricher:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "MITRAM360-DataPipeline/1.0"})

    def _find_pdf_url(self, website_url: str) -> Optional[str]:
        base = website_url.rstrip("/")
        for path in ["/annual-report", "/investor-relations", "/financials", "/about/annual-report"]:
            try:
                resp = self._session.get(f"{base}{path}", timeout=REQUEST_TIMEOUT, allow_redirects=True)
                for match in re.finditer(r'href=["\']([^"\']*\.pdf[^"\']*)["\']', resp.text, re.I):
                    href = match.group(1)
                    if any(re.search(p, href, re.I) for p in PDF_LINK_PATTERNS):
                        return href if href.startswith("http") else f"{base}{href}"
            except Exception:
                continue
        return None

    def _extract_fpc_tables(self, pdf_bytes: bytes) -> list:
        results = []
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    text = (page.extract_text() or "").lower()
                    if "fair practice" not in text and "interest rate policy" not in text:
                        continue
                    for table in page.extract_tables():
                        if not table or len(table) < 2:
                            continue
                        results.extend(parse_fpc_table(table))
        except Exception as e:
            log.warning("pdfplumber extraction failed: %s", e)
        return results

    def enrich_lender(self, lender_id: str, website_url: Optional[str],
                      policy_map: dict) -> list:
        if not website_url:
            return []
        pdf_url = self._find_pdf_url(website_url)
        if not pdf_url:
            return []
        try:
            time.sleep(RATE_DELAY)
            resp = self._session.get(pdf_url, timeout=30)
            resp.raise_for_status()
            pdf_bytes = resp.content
        except Exception as e:
            log.warning("PDF download failed for %s: %s", pdf_url, e)
            return []
        fpc_entries = self._extract_fpc_tables(pdf_bytes)
        if not fpc_entries:
            return []
        entry = fpc_entries[0]
        payloads = []
        raw = {"lender_id": lender_id, "pdf_url": pdf_url, "fpc": entry}
        for loan_type, policy_id in policy_map.items():
            if "interest_rate_min" in entry and "interest_rate_max" in entry:
                payloads.append(EnrichmentPayload(
                    policy_id=policy_id, field="interest_rate",
                    value_min=entry["interest_rate_min"], value_max=entry["interest_rate_max"],
                    source="fpc_pdf", confidence=0.80, raw_value=raw,
                ))
            if "loan_amount_min" in entry and "loan_amount_max" in entry:
                payloads.append(EnrichmentPayload(
                    policy_id=policy_id, field="loan_amount",
                    value_min=entry["loan_amount_min"], value_max=entry["loan_amount_max"],
                    source="fpc_pdf", confidence=0.80, raw_value=raw,
                ))
        return payloads
