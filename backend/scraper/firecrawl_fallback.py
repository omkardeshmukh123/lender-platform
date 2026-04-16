"""
firecrawl_fallback.py
=====================
Firecrawl fallback for fields the scraper consistently misses.

ONLY called when these fields are null/LOW confidence from the scraper:
  - employee_count
  - branch_count
  - established_year

NOT used for: phone, email, hq_state, operating_states, loan_tags
(scraper wins on those — see A/B test results April 2026)

Usage:
    from scraper.firecrawl_fallback import get_fallback_fields, needs_fallback

    if needs_fallback(scraped):
        fc_fields = get_fallback_fields(website, api_key)
        scraped.update({k: v for k, v in fc_fields.items() if v is not None})
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

# Fields we let Firecrawl fill in. Scraper is better at everything else.
FALLBACK_FIELDS = frozenset({"employee_count", "branch_count", "established_year"})

# Validation bounds
_BOUNDS: dict[str, tuple[int, int]] = {
    "employee_count":  (1, 500_000),
    "branch_count":    (1, 50_000),
    "established_year": (1900, 2025),
}

_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"


def needs_fallback(scraped: dict) -> bool:
    """Return True if any FALLBACK_FIELDS are missing from the scraper output."""
    for field in FALLBACK_FIELDS:
        val = scraped.get(field)
        if val is None or val == 0:
            return True
    return False


def get_fallback_fields(website: str, api_key: str | None = None) -> dict[str, Any]:
    """
    Call Firecrawl to extract only the fields the scraper missed.
    Returns a dict with validated values (None for anything that fails validation).
    """
    api_key = api_key or os.getenv("FIRECRAWL_API_KEY", "")
    if not api_key:
        log.debug("Firecrawl fallback skipped — FIRECRAWL_API_KEY not set")
        return {}

    payload = {
        "url":     website,
        "formats": ["extract"],
        "extract": {
            "schema": {
                "type": "object",
                "properties": {
                    "employee_count":  {"type": "string"},
                    "branch_count":    {"type": "string"},
                    "established_year": {"type": "string"},
                },
            },
            "prompt": (
                "Extract these fields from this Indian NBFC/lender website. "
                "employee_count: total number of employees (integer). "
                "branch_count: total number of branches or offices (integer). "
                "established_year: year the company was founded or incorporated (4-digit year). "
                "Return empty string if not explicitly stated. Do NOT estimate or infer."
            ),
        },
    }

    try:
        resp = requests.post(
            _FIRECRAWL_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data      = resp.json()
        extracted = (data.get("data") or data).get("extract") or {}
        result    = {}
        for field in FALLBACK_FIELDS:
            raw = extracted.get(field)
            result[field] = _validate_int(field, raw)
        non_null = {k: v for k, v in result.items() if v is not None}
        if non_null:
            log.info("Firecrawl fallback filled: %s", list(non_null.keys()))
        return result

    except requests.HTTPError as exc:
        log.warning("Firecrawl HTTP error for %s: %s", website, exc)
        return {}
    except Exception as exc:
        log.warning("Firecrawl fallback error for %s: %s", website, exc)
        return {}


def _validate_int(field: str, raw: Any) -> int | None:
    """Parse raw string to int and check against known bounds. Returns None if invalid."""
    if raw is None or str(raw).strip() in ("", "null", "0"):
        return None
    try:
        val = int(float(str(raw).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None
    lo, hi = _BOUNDS.get(field, (0, 10_000_000))
    if not (lo <= val <= hi):
        log.debug("Firecrawl %s=%d out of bounds [%d, %d] — rejected", field, val, lo, hi)
        return None
    return val
