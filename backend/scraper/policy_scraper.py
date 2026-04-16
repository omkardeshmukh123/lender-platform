"""
policy_scraper.py  v1.0
========================
Extracts loan policy data from NBFC / lender websites and aggregator pages.

Strategy (in order):
  1. BankBazaar product page  — structured HTML, highest quality
  2. PaisaBazaar product page — structured HTML, good quality
  3. Own website loan page    — variable quality, needs regex extraction

Returns a list of PolicyData objects (one per distinct loan product found).

Usage:
    from scraper.policy_scraper import scrape_policies, PolicyData

    policies = scrape_policies(
        lender_name   = "Kogta Financial India Ltd",
        website       = "https://www.kogtafinancial.com",
        loan_types    = ["MSME Loan", "Vehicle Loan"],
        firecrawl_key = None,   # optional
    )
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}
_TIMEOUT = 15
_DELAY   = 1.2   # seconds between requests to same host


# ─── Policy data container ────────────────────────────────────────────────────

@dataclass
class PolicyData:
    product_name:         str
    loan_type:            str
    loan_amount_min:      Optional[float] = None   # Lakhs
    loan_amount_max:      Optional[float] = None
    interest_rate_min:    Optional[float] = None   # % p.a.
    interest_rate_max:    Optional[float] = None
    tenure_min:           Optional[int]   = None   # months
    tenure_max:           Optional[int]   = None
    credit_score_min:     Optional[int]   = None
    min_age:              Optional[int]   = None
    max_age:              Optional[int]   = None
    processing_fee:       Optional[float] = None   # %
    employment_types:     list            = field(default_factory=list)
    eligible_states:      list            = field(default_factory=list)
    collateral_required:  Optional[bool]  = None
    collateral_types:     list            = field(default_factory=list)
    min_monthly_income:   Optional[float] = None   # INR
    min_annual_turnover:  Optional[float] = None   # INR
    min_business_vintage: Optional[int]   = None   # months
    eligibility_notes:    Optional[str]   = None
    data_source:          str             = "website"
    source_url:           str             = ""
    rates_as_of:          Optional[str]   = None   # YYYY-MM-DD
    prepayment_allowed:   Optional[bool]  = True


# ─── Amount converters ────────────────────────────────────────────────────────

def _to_lakhs(value: float, unit: str) -> float:
    """Convert raw value + unit string to Lakhs."""
    unit = unit.lower().strip()
    if "crore" in unit or "cr" in unit:
        return value * 100
    if "thousand" in unit or "000" in unit:
        return value / 10        # ₹50,000 → 0.5 Lakhs
    return value                 # already Lakhs


def _parse_inr_amount(text: str) -> Optional[float]:
    """
    Parse a single Indian rupee amount from text and return Lakhs.
    Handles: ₹5 lakh, Rs. 2 crore, 50,000, 10 lakh, 2.5 crore
    """
    text = unicodedata.normalize("NFKD", text).strip()
    m = re.search(
        r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)\s*"
        r"(crore|cr\.?|lakh|lac|l\b|thousand|k\b)?",
        text, re.I,
    )
    if not m:
        return None
    raw = float(m.group(1).replace(",", ""))
    unit = m.group(2) or ""
    return _to_lakhs(raw, unit)


def _years_to_months(years: float) -> int:
    return round(years * 12)


# ─── Regex extractors ─────────────────────────────────────────────────────────

def _extract_rates(text: str) -> tuple[Optional[float], Optional[float]]:
    """Return (min_rate, max_rate) as % p.a. from free text."""
    # "12% to 36% p.a." / "ROI: 18-24%" / "starting from 14% per annum"
    patterns = [
        r"(\d+(?:\.\d+)?)\s*%\s*(?:to|-|–)\s*(\d+(?:\.\d+)?)\s*%",   # range
        r"(?:starting|from|as low as|minimum)\s+(\d+(?:\.\d+)?)\s*%",  # single lower bound
        r"(\d+(?:\.\d+)?)\s*%\s*(?:p\.?a\.?|per annum)",              # single rate
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            g = m.groups()
            if len(g) == 2 and g[1]:
                lo, hi = float(g[0]), float(g[1])
                if 1 < lo < 80 and 1 < hi < 80:
                    return (min(lo, hi), max(lo, hi))
            elif g[0]:
                val = float(g[0])
                if 1 < val < 80:
                    return (val, None)
    return (None, None)


def _extract_amounts(text: str) -> tuple[Optional[float], Optional[float]]:
    """Return (min_amount_lakhs, max_amount_lakhs) from free text."""
    # "₹50,000 to ₹50 lakhs" / "Rs. 1 lakh to Rs. 5 crore" / "10,000 - 2 crore"
    unit_pat = r"(?:crore|cr\.?|lakh|lac|l\b|thousand|k\b)?"
    money    = r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)\s*" + unit_pat

    m = re.search(
        money + r"\s*(?:to|-|–)\s*" + money,
        text, re.I,
    )
    if m:
        raw1, raw2 = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        # detect unit from surrounding words
        chunk = text[max(0, m.start()-5) : m.end()+20]
        u1 = _detect_unit(chunk, m.group(1))
        u2 = _detect_unit(chunk, m.group(2))
        lo = _to_lakhs(raw1, u1)
        hi = _to_lakhs(raw2, u2)
        if lo > hi:           # swap if needed
            lo, hi = hi, lo
        if 0 < lo < 100_000 and 0 < hi < 100_000:
            return (lo, hi)

    # single amount ("up to ₹10 lakhs")
    m2 = re.search(r"(?:up to|maximum|upto|max\.?)\s*" + money, text, re.I)
    if m2:
        raw = float(m2.group(1).replace(",", ""))
        unit = _detect_unit(text[m2.start():m2.end()+20], m2.group(1))
        hi = _to_lakhs(raw, unit)
        if 0 < hi < 100_000:
            return (None, hi)

    return (None, None)


def _detect_unit(chunk: str, number_str: str) -> str:
    """Detect rupee unit from text surrounding the number."""
    chunk_lower = chunk.lower()
    if "crore" in chunk_lower or " cr" in chunk_lower:
        return "crore"
    if "lakh" in chunk_lower or " lac" in chunk_lower or " l " in chunk_lower:
        return "lakh"
    if "thousand" in chunk_lower or " k " in chunk_lower:
        return "thousand"
    # Infer from magnitude: if raw number > 10_000, probably rupees → convert
    try:
        raw = float(number_str.replace(",", ""))
        if raw >= 10_000:
            return "rupees"
        if raw >= 100:
            return "thousand"
    except ValueError:
        pass
    return "lakh"


def _to_lakhs_rupees(raw: float) -> float:
    return raw / 1_00_000


def _extract_tenure(text: str) -> tuple[Optional[int], Optional[int]]:
    """Return (min_months, max_months). Converts years → months."""
    # "12 to 60 months" / "1 to 5 years" / "up to 3 years"
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:to|-|–)\s*(\d+(?:\.\d+)?)\s*(month|year|yr)",
        text, re.I,
    )
    if m:
        lo, hi, unit = float(m.group(1)), float(m.group(2)), m.group(3).lower()
        if "year" in unit or "yr" in unit:
            lo, hi = _years_to_months(lo), _years_to_months(hi)
        lo, hi = int(lo), int(hi)
        if 1 <= lo < hi <= 600:
            return (lo, hi)

    # single upper bound "up to 60 months"
    m2 = re.search(
        r"(?:up to|maximum|upto|max\.?)\s*(\d+)\s*(month|year|yr)",
        text, re.I,
    )
    if m2:
        val, unit = int(m2.group(1)), m2.group(2).lower()
        if "year" in unit or "yr" in unit:
            val = _years_to_months(val)
        if 1 <= val <= 600:
            return (None, val)

    return (None, None)


def _extract_credit_score(text: str) -> Optional[int]:
    """Extract minimum CIBIL/credit score."""
    m = re.search(
        r"(?:cibil|credit|experian|crif)?\s*score\s*(?:of\s+)?(?:minimum|min\.?|>=|>|above)?\s*(\d{3})\+?",
        text, re.I,
    )
    if m:
        val = int(m.group(1))
        if 300 <= val <= 900:
            return val
    # "700+" pattern
    m2 = re.search(r"\b(6\d\d|7\d\d|8\d\d)\+\s*(?:cibil|credit)?", text, re.I)
    if m2:
        return int(m2.group(1))
    return None


def _extract_age(text: str) -> tuple[Optional[int], Optional[int]]:
    """Return (min_age, max_age) in years."""
    m = re.search(
        r"(\d+)\s*(?:to|-|–)\s*(\d+)\s*years?\s*(?:of age|old)?",
        text, re.I,
    )
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 18 <= lo < hi <= 80:
            return (lo, hi)

    m2 = re.search(r"(?:minimum|min\.?)\s*age\s*(?:of\s+)?(\d+)", text, re.I)
    if m2:
        val = int(m2.group(1))
        if 18 <= val <= 60:
            return (val, None)
    return (None, None)


def _extract_processing_fee(text: str) -> Optional[float]:
    """Extract processing fee as % (0-10 range)."""
    m = re.search(
        r"processing\s+(?:fee|charge|charges?)[^%\d]*(\d+(?:\.\d+)?)\s*%",
        text, re.I,
    )
    if m:
        val = float(m.group(1))
        if 0 <= val <= 10:
            return val
    # "1% to 3%" near processing
    m2 = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*%.*?processing",
        text, re.I,
    )
    if m2:
        return (float(m2.group(1)) + float(m2.group(2))) / 2
    return None


def _extract_employment(text: str) -> list[str]:
    """Extract employment types from text."""
    known = {
        "salaried":          "Salaried",
        "self.employed":     "Self-Employed",
        "self employed":     "Self-Employed",
        "business owner":    "Self-Employed",
        "proprietor":        "Self-Employed",
        "msme":              "Self-Employed",
        "nri":               "NRI",
        "professional":      "Self-Employed",
        "farmer":            "Farmer",
        "agriculture":       "Farmer",
        "jlg":               "JLG Member",
        "joint liability":   "JLG Member",
        "shg":               "SHG Member",
        "self help group":   "SHG Member",
    }
    found = []
    text_lower = text.lower()
    for kw, label in known.items():
        if kw in text_lower and label not in found:
            found.append(label)
    return found


ALL_INDIA_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi",
    "Jammu & Kashmir", "Ladakh", "Puducherry", "Chandigarh",
]
_STATE_LOWER = {s.lower(): s for s in ALL_INDIA_STATES}


def _extract_states(text: str) -> list[str]:
    """Extract Indian state names from text."""
    found = []
    text_lower = text.lower()
    if "pan india" in text_lower or "all india" in text_lower or "all states" in text_lower:
        return ALL_INDIA_STATES[:]
    for key, state in _STATE_LOWER.items():
        if key in text_lower and state not in found:
            found.append(state)
    return found


# ─── HTTP helpers ──────────────────────────────────────────────────────────────

_session_cache: dict[str, requests.Session] = {}

def _get_session(host: str) -> requests.Session:
    if host not in _session_cache:
        s = requests.Session()
        s.headers.update(_HEADERS)
        _session_cache[host] = s
    return _session_cache[host]


def _fetch(url: str, timeout: int = _TIMEOUT) -> Optional[BeautifulSoup]:
    """Fetch URL, return BeautifulSoup or None on error."""
    host = urlparse(url).netloc
    sess = _get_session(host)
    try:
        r = sess.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return BeautifulSoup(r.text, "html.parser")
        log.debug("HTTP %d for %s", r.status_code, url)
        return None
    except Exception as exc:
        log.debug("Fetch error %s: %s", url, exc)
        return None


# ─── Loan page discovery ───────────────────────────────────────────────────────

_LOAN_KEYWORDS = [
    "loan", "finance", "credit", "interest rate", "emi",
    "borrow", "eligibility", "apply",
]
_LOAN_PATH_KEYWORDS = [
    "/loan", "/product", "/finance", "/borrow", "/credit",
    "/msme", "/personal", "/gold", "/home-loan", "/vehicle",
    "/business", "/micro", "/agriculture", "/rates",
]


def _discover_loan_pages(base_url: str, soup: BeautifulSoup) -> list[str]:
    """
    Find internal links likely to contain loan product / rate information.
    Returns up to 5 candidate URLs.
    """
    base = urlparse(base_url)
    candidates = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or "mailto:" in href or "tel:" in href:
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        # Must be same domain
        if parsed.netloc and parsed.netloc != base.netloc:
            continue
        path = parsed.path.lower()
        text = (a.get_text() or "").lower()
        if any(kw in path for kw in _LOAN_PATH_KEYWORDS):
            candidates.add(full)
        elif any(kw in text for kw in ["loan", "rate", "product", "borrow", "interest"]):
            candidates.add(full)
    # Prioritize paths with "rate" or "product"
    def priority(u: str) -> int:
        p = urlparse(u).path.lower()
        if "rate" in p or "interest" in p: return 0
        if "product" in p or "loan" in p: return 1
        if "eligib" in p or "apply" in p: return 2
        return 3
    return sorted(candidates, key=priority)[:5]


# ─── BankBazaar scraper ────────────────────────────────────────────────────────

_BB_LOAN_SLUGS = {
    "MSME Loan":            "msme-loan",
    "Business Loan":        "business-loan",
    "Personal Loan":        "personal-loan",
    "Gold Loan":            "gold-loan",
    "Home Loan":            "home-loan",
    "Vehicle Loan":         "used-car-loan",
    "Education Loan":       "education-loan",
    "Loan Against Property":"loan-against-property",
    "Micro Loan":           "microfinance",
    "Microfinance":         "microfinance",
    "Two Wheeler Loan":     "two-wheeler-loan",
    "Working Capital":      "working-capital-loan",
}

_BB_BASE = "https://www.bankbazaar.com"


def _make_bb_slug(lender_name: str) -> str:
    """Convert lender name to BankBazaar URL slug."""
    slug = lender_name.lower()
    # Remove common suffixes
    for suffix in [
        "private limited", "pvt ltd", "pvt. ltd.", "pvt. ltd", "pvt ltd.",
        "limited", "ltd.", "ltd", "finance", "financial", "services",
        "india", "micro", "microfin", "microfinance", "credit",
        "(india)", "(p)", "(pvt)", "co.", "company",
    ]:
        slug = slug.replace(suffix, " ")
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = slug.strip("-")
    return slug


def _scrape_bankbazaar(lender_name: str, loan_type: str) -> Optional[PolicyData]:
    """Try to get policy data from BankBazaar for given lender + loan type."""
    loan_slug = _BB_LOAN_SLUGS.get(loan_type)
    if not loan_slug:
        return None
    lender_slug = _make_bb_slug(lender_name)
    url = f"{_BB_BASE}/{loan_slug}/{lender_slug}.html"
    soup = _fetch(url)
    if not soup:
        return None

    # Check if it's a real lender page (not 404 or generic)
    title = (soup.title.get_text() if soup.title else "").lower()
    if "not found" in title or "404" in title or lender_slug.split("-")[0] not in title:
        return None

    text = soup.get_text(" ", strip=True)
    log.info("  BankBazaar hit: %s | %s", lender_name, loan_type)

    rate_min, rate_max = _extract_rates(text)
    amt_min, amt_max   = _extract_amounts(text)
    ten_min, ten_max   = _extract_tenure(text)
    credit_min         = _extract_credit_score(text)
    age_min, age_max   = _extract_age(text)
    proc_fee           = _extract_processing_fee(text)
    employment         = _extract_employment(text)
    states             = _extract_states(text)

    if not any([rate_min, amt_min, amt_max, ten_max]):
        return None  # page had no useful data

    return PolicyData(
        product_name      = f"{lender_name} {loan_type}",
        loan_type         = loan_type,
        loan_amount_min   = amt_min,
        loan_amount_max   = amt_max,
        interest_rate_min = rate_min,
        interest_rate_max = rate_max,
        tenure_min        = ten_min,
        tenure_max        = ten_max,
        credit_score_min  = credit_min,
        min_age           = age_min,
        max_age           = age_max,
        processing_fee    = proc_fee,
        employment_types  = employment,
        eligible_states   = states,
        data_source       = "bankbazaar",
        source_url        = url,
    )


# ─── PaisaBazaar scraper ───────────────────────────────────────────────────────

_PB_BASE = "https://www.paisabazaar.com"
_PB_LOAN_SLUGS = {
    "MSME Loan":            "msme-loan",
    "Business Loan":        "business-loan",
    "Personal Loan":        "personal-loan",
    "Gold Loan":            "gold-loan",
    "Home Loan":            "home-loan",
    "Vehicle Loan":         "used-car-loan",
    "Education Loan":       "education-loan",
    "Loan Against Property":"loan-against-property",
    "Two Wheeler Loan":     "two-wheeler-loan",
}


def _scrape_paisabazaar(lender_name: str, loan_type: str) -> Optional[PolicyData]:
    """Try to get policy data from PaisaBazaar for given lender + loan type."""
    loan_slug = _PB_LOAN_SLUGS.get(loan_type)
    if not loan_slug:
        return None
    lender_slug = _make_bb_slug(lender_name)  # same slug logic works
    url = f"{_PB_BASE}/{lender_slug}-{loan_slug}/"
    soup = _fetch(url)
    if not soup:
        return None

    title = (soup.title.get_text() if soup.title else "").lower()
    if "not found" in title or "404" in title:
        return None

    text = soup.get_text(" ", strip=True)
    # PaisaBazaar pages are JS-heavy; check for enough content
    if len(text) < 500:
        return None

    log.info("  PaisaBazaar hit: %s | %s", lender_name, loan_type)

    rate_min, rate_max = _extract_rates(text)
    amt_min, amt_max   = _extract_amounts(text)
    ten_min, ten_max   = _extract_tenure(text)
    credit_min         = _extract_credit_score(text)
    age_min, age_max   = _extract_age(text)
    proc_fee           = _extract_processing_fee(text)
    employment         = _extract_employment(text)
    states             = _extract_states(text)

    if not any([rate_min, amt_min, amt_max, ten_max]):
        return None

    return PolicyData(
        product_name      = f"{lender_name} {loan_type}",
        loan_type         = loan_type,
        loan_amount_min   = amt_min,
        loan_amount_max   = amt_max,
        interest_rate_min = rate_min,
        interest_rate_max = rate_max,
        tenure_min        = ten_min,
        tenure_max        = ten_max,
        credit_score_min  = credit_min,
        min_age           = age_min,
        max_age           = age_max,
        processing_fee    = proc_fee,
        employment_types  = employment,
        eligible_states   = states,
        data_source       = "paisabazaar",
        source_url        = url,
    )


# ─── Own website scraper ───────────────────────────────────────────────────────

def _scrape_own_website(
    lender_name: str,
    website: str,
    loan_types: list[str],
) -> list[PolicyData]:
    """
    Scrape the lender's own website for loan policy data.
    Returns list of PolicyData (may be empty).
    """
    if not website:
        return []
    soup = _fetch(website)
    if not soup:
        return []

    pages_to_check = [website] + _discover_loan_pages(website, soup)
    all_text_blocks = []

    for page_url in pages_to_check[:4]:
        if page_url != website:
            time.sleep(_DELAY)
        page_soup = _fetch(page_url) if page_url != website else soup
        if page_soup:
            all_text_blocks.append((page_url, page_soup.get_text(" ", strip=True)))

    results = []
    for loan_type in loan_types:
        # Score each page by relevance to this loan type
        loan_keywords = loan_type.lower().split()
        best_text, best_url, best_score = "", website, 0
        for pg_url, txt in all_text_blocks:
            score = sum(1 for kw in loan_keywords if kw in txt.lower())
            score += txt.lower().count("interest rate") * 2
            score += txt.lower().count("% p.a") * 2
            score += txt.lower().count("loan amount") * 2
            if score > best_score:
                best_score, best_text, best_url = score, txt, pg_url

        if best_score < 2:
            continue   # not enough loan content on site

        rate_min, rate_max = _extract_rates(best_text)
        amt_min, amt_max   = _extract_amounts(best_text)
        ten_min, ten_max   = _extract_tenure(best_text)
        credit_min         = _extract_credit_score(best_text)
        age_min, age_max   = _extract_age(best_text)
        proc_fee           = _extract_processing_fee(best_text)
        employment         = _extract_employment(best_text)
        states             = _extract_states(best_text)

        if not any([rate_min, amt_min, amt_max, ten_max, credit_min]):
            continue   # no useful financial data

        results.append(PolicyData(
            product_name      = f"{lender_name} {loan_type}",
            loan_type         = loan_type,
            loan_amount_min   = amt_min,
            loan_amount_max   = amt_max,
            interest_rate_min = rate_min,
            interest_rate_max = rate_max,
            tenure_min        = ten_min,
            tenure_max        = ten_max,
            credit_score_min  = credit_min,
            min_age           = age_min,
            max_age           = age_max,
            processing_fee    = proc_fee,
            employment_types  = employment,
            eligible_states   = states,
            data_source       = "website",
            source_url        = best_url,
        ))

    return results


# ─── Firecrawl enrichment ──────────────────────────────────────────────────────

def _firecrawl_enrich(policy: PolicyData, firecrawl_key: str) -> PolicyData:
    """
    Use Firecrawl structured extraction to fill any missing fields in a PolicyData.
    Only called when a policy already has some data but key fields are still None.
    """
    import requests as _req
    if not policy.source_url:
        return policy

    prompt = (
        "Extract loan product details from this Indian NBFC/lender webpage. "
        "Return exact values only — do NOT estimate. "
        "interest_rate_min and interest_rate_max: annual interest rate in % (e.g. 14.5). "
        "loan_amount_min and loan_amount_max: loan amount in Indian Rupees Lakhs (e.g. 5 means Rs 5 lakh). "
        "tenure_min and tenure_max: loan tenure in months (e.g. 12). "
        "credit_score_min: minimum CIBIL score required (e.g. 700). "
        "processing_fee_pct: processing fee as percentage (e.g. 2.0). "
        "employment_types: comma-separated list from: Salaried, Self-Employed, Farmer, JLG Member, SHG Member, NRI. "
        "min_age, max_age: minimum and maximum borrower age in years."
    )

    payload = {
        "url":     policy.source_url,
        "formats": ["extract"],
        "extract": {
            "schema": {
                "type": "object",
                "properties": {
                    "interest_rate_min":  {"type": "number"},
                    "interest_rate_max":  {"type": "number"},
                    "loan_amount_min":    {"type": "number"},
                    "loan_amount_max":    {"type": "number"},
                    "tenure_min":         {"type": "number"},
                    "tenure_max":         {"type": "number"},
                    "credit_score_min":   {"type": "number"},
                    "processing_fee_pct": {"type": "number"},
                    "employment_types":   {"type": "string"},
                    "min_age":            {"type": "number"},
                    "max_age":            {"type": "number"},
                },
            },
            "prompt": prompt,
        },
    }

    try:
        resp = _req.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {firecrawl_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        ext  = (data.get("data") or data).get("extract") or {}

        def safe_float(key: str, lo: float, hi: float) -> Optional[float]:
            v = ext.get(key)
            if v is None: return None
            try:
                f = float(v)
                return f if lo <= f <= hi else None
            except (TypeError, ValueError):
                return None

        def safe_int(key: str, lo: int, hi: int) -> Optional[int]:
            v = ext.get(key)
            if v is None: return None
            try:
                i = int(float(v))
                return i if lo <= i <= hi else None
            except (TypeError, ValueError):
                return None

        # Fill only missing fields
        if policy.interest_rate_min is None:
            policy.interest_rate_min = safe_float("interest_rate_min", 1, 80)
        if policy.interest_rate_max is None:
            policy.interest_rate_max = safe_float("interest_rate_max", 1, 80)
        if policy.loan_amount_min is None:
            policy.loan_amount_min = safe_float("loan_amount_min", 0.01, 100_000)
        if policy.loan_amount_max is None:
            policy.loan_amount_max = safe_float("loan_amount_max", 0.01, 100_000)
        if policy.tenure_min is None:
            policy.tenure_min = safe_int("tenure_min", 1, 600)
        if policy.tenure_max is None:
            policy.tenure_max = safe_int("tenure_max", 1, 600)
        if policy.credit_score_min is None:
            policy.credit_score_min = safe_int("credit_score_min", 300, 900)
        if policy.processing_fee is None:
            policy.processing_fee = safe_float("processing_fee_pct", 0, 10)
        if policy.min_age is None:
            policy.min_age = safe_int("min_age", 18, 60)
        if policy.max_age is None:
            policy.max_age = safe_int("max_age", 40, 80)
        if not policy.employment_types:
            raw_emp = ext.get("employment_types", "")
            if raw_emp:
                policy.employment_types = [e.strip() for e in raw_emp.split(",") if e.strip()]

        log.info("  Firecrawl enriched: %s", policy.product_name)

    except Exception as exc:
        log.debug("Firecrawl error for %s: %s", policy.source_url, exc)

    return policy


# ─── Main entry point ─────────────────────────────────────────────────────────

def scrape_policies(
    lender_name:   str,
    website:       str = "",
    loan_types:    list[str] | None = None,
    firecrawl_key: str | None = None,
    skip_aggregators: bool = False,
) -> list[PolicyData]:
    """
    Full policy scrape for one lender.

    Strategy:
    1. For each loan_type: try BankBazaar → PaisaBazaar
    2. For any loan_types that still have no data: try own website
    3. If firecrawl_key is set: fill missing fields in any partial policies
    4. Deduplicate by loan_type (keep best completeness per type)

    Returns list of PolicyData, sorted best-first by field count.
    """
    if loan_types is None:
        loan_types = ["MSME Loan", "Business Loan", "Personal Loan", "Micro Loan"]

    collected: dict[str, PolicyData] = {}   # loan_type → best policy

    def _count_fields(p: PolicyData) -> int:
        return sum(1 for v in [
            p.loan_amount_min, p.loan_amount_max, p.interest_rate_min,
            p.interest_rate_max, p.tenure_min, p.tenure_max,
            p.credit_score_min, p.employment_types, p.eligible_states,
        ] if v)

    if not skip_aggregators:
        for lt in loan_types:
            time.sleep(_DELAY)
            pol = _scrape_bankbazaar(lender_name, lt)
            if pol and _count_fields(pol) > _count_fields(collected.get(lt, PolicyData("", lt))):
                collected[lt] = pol

        for lt in loan_types:
            if lt in collected and _count_fields(collected[lt]) >= 4:
                continue   # already have good data
            time.sleep(_DELAY)
            pol = _scrape_paisabazaar(lender_name, lt)
            if pol and _count_fields(pol) > _count_fields(collected.get(lt, PolicyData("", lt))):
                collected[lt] = pol

    # Own website for any still-missing loan types (or if aggregators skipped)
    missing = [lt for lt in loan_types if lt not in collected]
    if missing or skip_aggregators:
        own = _scrape_own_website(lender_name, website, loan_types if skip_aggregators else missing)
        for pol in own:
            lt = pol.loan_type
            if _count_fields(pol) > _count_fields(collected.get(lt, PolicyData("", lt))):
                collected[lt] = pol

    # Firecrawl fill for any partial policies
    if firecrawl_key:
        for lt, pol in collected.items():
            if _count_fields(pol) < 4:
                collected[lt] = _firecrawl_enrich(pol, firecrawl_key)

    results = [p for p in collected.values() if _count_fields(p) >= 2]
    results.sort(key=_count_fields, reverse=True)
    return results
