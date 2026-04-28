"""
policy_scraper.py  v2.0
========================
Extracts loan policy data from NBFC / lender websites and aggregator pages.

Strategy (lender's own site is authoritative — freshest data):
  1. Own website          — non-JS, fast
  2. Firecrawl            — JS-rendered own site (no LLM, regex only)
  3. BankBazaar           — aggregator fallback (may lag market by weeks)
  4. PaisaBazaar          — aggregator fallback
  5. Firecrawl fill       — top up any partial policy still below threshold

All float financial values are rounded to 2 decimal places.
Interest rates > 42% are hard-rejected; > 36% are stored with rate_flagged=True.
No LLM extraction anywhere — only regex against rendered text.
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
_TIMEOUT            = 15
_DELAY              = 1.2    # seconds between requests to same host
_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"

# RBI / NBFC rate guardrails
_RATE_HARD_MAX  = 42.0   # reject completely above this
_RATE_FLAG_ABOVE = 36.0  # store with rate_flagged=True above this


# ─── Policy data container ────────────────────────────────────────────────────

@dataclass
class PolicyData:
    product_name:         str
    loan_type:            str
    loan_amount_min:      Optional[float] = None   # Lakhs, rounded to 2dp
    loan_amount_max:      Optional[float] = None
    interest_rate_min:    Optional[float] = None   # % p.a., rounded to 2dp
    interest_rate_max:    Optional[float] = None
    tenure_min:           Optional[int]   = None   # months
    tenure_max:           Optional[int]   = None
    credit_score_min:     Optional[int]   = None
    min_age:              Optional[int]   = None
    max_age:              Optional[int]   = None
    processing_fee:       Optional[float] = None   # %, rounded to 2dp
    employment_types:     list            = field(default_factory=list)
    eligible_states:      list            = field(default_factory=list)
    collateral_required:  Optional[bool]  = None
    collateral_types:     list            = field(default_factory=list)
    min_monthly_income:   Optional[float] = None   # INR, rounded to 2dp
    min_annual_turnover:  Optional[float] = None   # INR, rounded to 2dp
    min_business_vintage: Optional[int]   = None   # months
    eligibility_notes:    Optional[str]   = None
    rate_type:            Optional[str]   = None   # fixed|floating|mclr_linked|repo_linked|hybrid
    rate_flagged:         bool            = False   # True if rate > 36% (review required)
    data_source:          str             = "website"
    source_url:           str             = ""
    rates_as_of:          Optional[str]   = None   # YYYY-MM-DD as stated by lender (not scrape date)
    prepayment_allowed:   Optional[bool]  = True


# ─── Float rounding ───────────────────────────────────────────────────────────

def _r2(v: Optional[float]) -> Optional[float]:
    return round(v, 2) if v is not None else None


# ─── Amount converters ────────────────────────────────────────────────────────

def _to_lakhs(value: float, unit: str) -> float:
    unit = unit.lower().strip()
    if "crore" in unit or "cr" in unit:
        return value * 100
    if "thousand" in unit or "000" in unit:
        return value / 10
    return value


def _years_to_months(years: float) -> int:
    return round(years * 12)


# ─── Rate validation ──────────────────────────────────────────────────────────

def _validate_rates(
    lo: Optional[float], hi: Optional[float]
) -> tuple[Optional[float], Optional[float], bool]:
    """
    Apply RBI guardrails to extracted rates.
    Returns (rate_min, rate_max, rate_flagged).
    Hard-rejects > 42%; flags > 36%.
    """
    flagged = False
    if lo is not None:
        if lo > _RATE_HARD_MAX:
            lo = None
        elif lo > _RATE_FLAG_ABOVE:
            flagged = True
    if hi is not None:
        if hi > _RATE_HARD_MAX:
            hi = None
        elif hi > _RATE_FLAG_ABOVE:
            flagged = True
    return _r2(lo), _r2(hi), flagged


# ─── Regex extractors ─────────────────────────────────────────────────────────

def _extract_rates(text: str) -> tuple[Optional[float], Optional[float]]:
    """Return (min_rate, max_rate) % p.a. Bounds: 5–42%."""
    patterns = [
        r"(\d+(?:\.\d+)?)\s*%\s*(?:to|-|–)\s*(\d+(?:\.\d+)?)\s*%",
        r"(?:starting|from|as low as|minimum)\s+(\d+(?:\.\d+)?)\s*%",
        r"(\d+(?:\.\d+)?)\s*%\s*(?:p\.?a\.?|per annum)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            g = m.groups()
            if len(g) == 2 and g[1]:
                lo, hi = float(g[0]), float(g[1])
                if 5 <= lo <= _RATE_HARD_MAX and 5 <= hi <= _RATE_HARD_MAX:
                    return (min(lo, hi), max(lo, hi))
            elif g[0]:
                val = float(g[0])
                if 5 <= val <= _RATE_HARD_MAX:
                    return (val, None)
    return (None, None)


def _extract_amounts(text: str) -> tuple[Optional[float], Optional[float]]:
    unit_pat = r"(?:crore|cr\.?|lakh|lac|l\b|thousand|k\b)?"
    money    = r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)\s*" + unit_pat

    m = re.search(money + r"\s*(?:to|-|–)\s*" + money, text, re.I)
    if m:
        raw1 = float(m.group(1).replace(",", ""))
        raw2 = float(m.group(2).replace(",", ""))
        chunk = text[max(0, m.start()-5) : m.end()+20]
        lo = _to_lakhs(raw1, _detect_unit(chunk, m.group(1)))
        hi = _to_lakhs(raw2, _detect_unit(chunk, m.group(2)))
        if lo > hi:
            lo, hi = hi, lo
        if 0 < lo < 100_000 and 0 < hi < 100_000:
            return (_r2(lo), _r2(hi))

    m2 = re.search(r"(?:up to|maximum|upto|max\.?)\s*" + money, text, re.I)
    if m2:
        raw  = float(m2.group(1).replace(",", ""))
        unit = _detect_unit(text[m2.start():m2.end()+20], m2.group(1))
        hi   = _to_lakhs(raw, unit)
        if 0 < hi < 100_000:
            return (None, _r2(hi))

    return (None, None)


def _detect_unit(chunk: str, number_str: str) -> str:
    cl = chunk.lower()
    if "crore" in cl or " cr" in cl:
        return "crore"
    if "lakh" in cl or " lac" in cl or " l " in cl:
        return "lakh"
    if "thousand" in cl or " k " in cl:
        return "thousand"
    try:
        raw = float(number_str.replace(",", ""))
        if raw >= 10_000:
            return "rupees"
        if raw >= 100:
            return "thousand"
    except ValueError:
        pass
    return "lakh"


def _extract_tenure(text: str) -> tuple[Optional[int], Optional[int]]:
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

    m2 = re.search(r"(?:up to|maximum|upto|max\.?)\s*(\d+)\s*(month|year|yr)", text, re.I)
    if m2:
        val, unit = int(m2.group(1)), m2.group(2).lower()
        if "year" in unit or "yr" in unit:
            val = _years_to_months(val)
        if 1 <= val <= 600:
            return (None, val)

    return (None, None)


def _extract_credit_score(text: str) -> Optional[int]:
    m = re.search(
        r"(?:cibil|credit|experian|crif)?\s*score\s*(?:of\s+)?(?:minimum|min\.?|>=|>|above)?\s*(\d{3})\+?",
        text, re.I,
    )
    if m:
        val = int(m.group(1))
        if 300 <= val <= 900:
            return val
    m2 = re.search(r"\b(6\d\d|7\d\d|8\d\d)\+\s*(?:cibil|credit)?", text, re.I)
    if m2:
        return int(m2.group(1))
    return None


def _extract_age(text: str) -> tuple[Optional[int], Optional[int]]:
    m = re.search(r"(\d+)\s*(?:to|-|–)\s*(\d+)\s*years?\s*(?:of age|old)?", text, re.I)
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
    m = re.search(
        r"processing\s+(?:fee|charge|charges?)[^%\d]*(\d+(?:\.\d+)?)\s*%",
        text, re.I,
    )
    if m:
        val = float(m.group(1))
        if 0 <= val <= 10:
            return _r2(val)
    m2 = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*%.*?processing",
        text, re.I,
    )
    if m2:
        return _r2((float(m2.group(1)) + float(m2.group(2))) / 2)
    return None


def _extract_employment(text: str) -> list[str]:
    known = {
        "salaried":        "Salaried",
        "self.employed":   "Self-Employed",
        "self employed":   "Self-Employed",
        "business owner":  "Self-Employed",
        "proprietor":      "Self-Employed",
        "msme":            "Self-Employed",
        "nri":             "NRI",
        "professional":    "Self-Employed",
        "farmer":          "Farmer",
        "agriculture":     "Farmer",
        "jlg":             "JLG Member",
        "joint liability": "JLG Member",
        "shg":             "SHG Member",
        "self help group": "SHG Member",
    }
    found, text_lower = [], text.lower()
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
    found, text_lower = [], text.lower()
    if "pan india" in text_lower or "all india" in text_lower or "all states" in text_lower:
        return ALL_INDIA_STATES[:]
    for key, state in _STATE_LOWER.items():
        if key in text_lower and state not in found:
            found.append(state)
    return found


def _extract_rate_type(text: str) -> Optional[str]:
    """Detect rate linkage from page text. Returns None if not determinable."""
    tl = text.lower()
    if any(k in tl for k in ["repo rate", "repo-linked", "eblr", "external benchmark", "rllr"]):
        return "repo_linked"
    if any(k in tl for k in ["mclr", "marginal cost of fund"]):
        return "mclr_linked"
    if any(k in tl for k in ["floating rate", "variable rate", "floating interest", "variable interest"]):
        return "floating"
    if any(k in tl for k in ["fixed rate", "fixed interest", "fixed @ "]):
        return "fixed"
    return None


def _extract_effective_date(text: str) -> Optional[str]:
    """Extract lender-stated effective date (w.e.f. / effective from) as YYYY-MM-DD."""
    m = re.search(
        r"(?:w\.?e\.?f\.?|effective\s+(?:from|date)|revised\s+(?:on|from))[:\s]*"
        r"(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})",
        text, re.I,
    )
    if not m:
        return None
    try:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if 1 <= mo <= 12 and 1 <= d <= 31 and 2000 <= y <= 2099:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    except ValueError:
        pass
    return None


# ─── HTTP helpers ──────────────────────────────────────────────────────────────

_session_cache: dict[str, requests.Session] = {}


def _get_session(host: str) -> requests.Session:
    if host not in _session_cache:
        s = requests.Session()
        s.headers.update(_HEADERS)
        _session_cache[host] = s
    return _session_cache[host]


def _fetch(url: str, timeout: int = _TIMEOUT) -> Optional[BeautifulSoup]:
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

_LOAN_PATH_KEYWORDS = [
    "/loan", "/product", "/finance", "/borrow", "/credit",
    "/msme", "/personal", "/gold", "/home-loan", "/vehicle",
    "/business", "/micro", "/agriculture", "/rates",
]

_LOAN_PATH_GUESSES: dict[str, list[str]] = {
    "Personal Loan":         ["/personal-loan", "/personal_loan", "/loans/personal", "/products/personal-loan"],
    "Business Loan":         ["/business-loan", "/business_loan", "/loans/business", "/sme-loan", "/products/business"],
    "MSME Loan":             ["/msme-loan", "/msme", "/loans/msme", "/sme", "/products/msme"],
    "Home Loan":             ["/home-loan", "/home_loan", "/housing-loan", "/loans/home"],
    "Gold Loan":             ["/gold-loan", "/gold_loan", "/loans/gold"],
    "Vehicle Loan":          ["/vehicle-loan", "/auto-loan", "/car-loan", "/loans/vehicle"],
    "Micro Loan":            ["/micro-loan", "/microfinance", "/micro", "/jlg", "/group-loan"],
    "Microfinance":          ["/microfinance", "/micro-loan", "/micro", "/jlg"],
    "Education Loan":        ["/education-loan", "/education_loan", "/loans/education"],
    "Two Wheeler Loan":      ["/two-wheeler-loan", "/two-wheeler", "/bike-loan"],
    "Loan Against Property": ["/loan-against-property", "/lap", "/mortgage"],
    "Working Capital":       ["/working-capital", "/working-capital-loan", "/overdraft"],
    "Agriculture Loan":      ["/agriculture-loan", "/agri-loan", "/kisan-loan", "/farm-loan"],
}


def _discover_loan_pages(base_url: str, soup: BeautifulSoup) -> list[str]:
    base       = urlparse(base_url)
    candidates = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or "mailto:" in href or "tel:" in href:
            continue
        full   = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.netloc and parsed.netloc != base.netloc:
            continue
        path = parsed.path.lower()
        text = (a.get_text() or "").lower()
        if any(kw in path for kw in _LOAN_PATH_KEYWORDS):
            candidates.add(full)
        elif any(kw in text for kw in ["loan", "rate", "product", "borrow", "interest"]):
            candidates.add(full)

    def priority(u: str) -> int:
        p = urlparse(u).path.lower()
        if "rate" in p or "interest" in p: return 0
        if "product" in p or "loan" in p:  return 1
        if "eligib" in p or "apply" in p:  return 2
        return 3

    return sorted(candidates, key=priority)[:5]


# ─── Shared field counter ─────────────────────────────────────────────────────

def _count_policy_fields(p: PolicyData) -> int:
    # Interest rate counts 3× — it's the most critical field for loan matching
    rate_score = sum(3 for v in [p.interest_rate_min, p.interest_rate_max] if v)
    other_score = sum(1 for v in [
        p.loan_amount_min, p.loan_amount_max, p.tenure_min, p.tenure_max,
        p.credit_score_min, p.employment_types, p.eligible_states,
    ] if v)
    return rate_score + other_score


# ─── Own website scraper ───────────────────────────────────────────────────────

def _scrape_own_website(
    lender_name: str,
    website: str,
    loan_types: list[str],
) -> list[PolicyData]:
    if not website:
        return []
    soup = _fetch(website)
    if not soup:
        return []

    pages_to_check  = [website] + _discover_loan_pages(website, soup)
    all_text_blocks = []
    for page_url in pages_to_check[:4]:
        if page_url != website:
            time.sleep(_DELAY)
        page_soup = _fetch(page_url) if page_url != website else soup
        if page_soup:
            all_text_blocks.append((page_url, page_soup.get_text(" ", strip=True)))

    results = []
    for loan_type in loan_types:
        loan_keywords = loan_type.lower().split()
        best_text, best_url, best_score = "", website, 0
        for pg_url, txt in all_text_blocks:
            score  = sum(1 for kw in loan_keywords if kw in txt.lower())
            score += txt.lower().count("interest rate") * 2
            score += txt.lower().count("% p.a") * 2
            score += txt.lower().count("loan amount") * 2
            if score > best_score:
                best_score, best_text, best_url = score, txt, pg_url

        if best_score < 2:
            continue

        ir_min, ir_max   = _extract_rates(best_text)
        ir_min, ir_max, flagged = _validate_rates(ir_min, ir_max)
        amt_min, amt_max = _extract_amounts(best_text)
        ten_min, ten_max = _extract_tenure(best_text)
        credit_min       = _extract_credit_score(best_text)
        age_min, age_max = _extract_age(best_text)
        proc_fee         = _extract_processing_fee(best_text)
        employment       = _extract_employment(best_text)
        states           = _extract_states(best_text)
        rate_type        = _extract_rate_type(best_text)
        rates_as_of      = _extract_effective_date(best_text)

        if not any([ir_min, amt_min, amt_max, ten_max, credit_min]):
            continue

        results.append(PolicyData(
            product_name      = f"{lender_name} {loan_type}",
            loan_type         = loan_type,
            loan_amount_min   = amt_min,
            loan_amount_max   = amt_max,
            interest_rate_min = ir_min,
            interest_rate_max = ir_max,
            tenure_min        = ten_min,
            tenure_max        = ten_max,
            credit_score_min  = credit_min,
            min_age           = age_min,
            max_age           = age_max,
            processing_fee    = proc_fee,
            employment_types  = employment,
            eligible_states   = states,
            rate_type         = rate_type,
            rate_flagged      = flagged,
            rates_as_of       = rates_as_of,
            data_source       = "website",
            source_url        = best_url,
        ))

    return results


# ─── Firecrawl helpers ────────────────────────────────────────────────────────

def _firecrawl_fetch_markdown(url: str, firecrawl_key: str) -> str:
    """Render a URL with Firecrawl and return its markdown. Returns '' on any error."""
    try:
        resp = requests.post(
            _FIRECRAWL_ENDPOINT,
            headers={"Authorization": f"Bearer {firecrawl_key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"]},
            timeout=45,
        )
        if resp.status_code in (404, 422):
            return ""
        resp.raise_for_status()
        data = resp.json()
        return (data.get("data") or data).get("markdown") or ""
    except requests.Timeout:
        log.warning("Firecrawl timeout: %s", url)
        return ""
    except Exception as exc:
        log.debug("Firecrawl fetch error %s: %s", url, exc)
        return ""


def _firecrawl_scrape_primary(
    lender_name: str,
    website: str,
    loan_type: str,
    firecrawl_key: str,
) -> Optional[PolicyData]:
    """
    Render JS-heavy pages with Firecrawl (markdown only), then apply regex extractors.
    No LLM — only returns data explicitly on the page.
    """
    if not website or not firecrawl_key:
        return None

    base         = website.rstrip("/")
    path_guesses = _LOAN_PATH_GUESSES.get(loan_type, ["/products", "/loans"])
    urls         = [base] + [base + p for p in path_guesses[:3]]
    loan_keywords = loan_type.lower().split()

    best_policy: Optional[PolicyData] = None
    best_score = 0

    for url in urls:
        text = _firecrawl_fetch_markdown(url, firecrawl_key)
        if len(text) < 200:
            time.sleep(0.5)
            continue

        relevance  = sum(1 for kw in loan_keywords if kw in text.lower())
        relevance += text.lower().count("interest rate") * 2
        relevance += text.lower().count("% p.a") * 2
        relevance += text.lower().count("loan amount") * 2
        if relevance < 2:
            time.sleep(0.5)
            continue

        ir_min, ir_max   = _extract_rates(text)
        ir_min, ir_max, flagged = _validate_rates(ir_min, ir_max)
        amt_min, amt_max = _extract_amounts(text)
        ten_min, ten_max = _extract_tenure(text)
        credit_min       = _extract_credit_score(text)
        age_min, age_max = _extract_age(text)
        proc_fee         = _extract_processing_fee(text)
        employment       = _extract_employment(text)
        states           = _extract_states(text)
        rate_type        = _extract_rate_type(text)
        rates_as_of      = _extract_effective_date(text)

        if not any([ir_min, amt_min, amt_max, ten_max]):
            time.sleep(0.5)
            continue

        policy = PolicyData(
            product_name      = f"{lender_name} {loan_type}",
            loan_type         = loan_type,
            interest_rate_min = ir_min,
            interest_rate_max = ir_max,
            loan_amount_min   = amt_min,
            loan_amount_max   = amt_max,
            tenure_min        = ten_min,
            tenure_max        = ten_max,
            credit_score_min  = credit_min,
            min_age           = age_min,
            max_age           = age_max,
            processing_fee    = proc_fee,
            employment_types  = employment,
            eligible_states   = states,
            rate_type         = rate_type,
            rate_flagged      = flagged,
            rates_as_of       = rates_as_of,
            data_source       = "firecrawl",
            source_url        = url,
        )
        score = _count_policy_fields(policy)
        if score > best_score:
            best_score  = score
            best_policy = policy
        if best_score >= 9:   # 3×rate + 3 other fields
            break

        time.sleep(0.5)

    if best_policy:
        log.info("  Firecrawl primary: %s | %s (score=%d)", lender_name, loan_type, best_score)
    return best_policy


def _firecrawl_enrich(policy: PolicyData, firecrawl_key: str) -> PolicyData:
    """Re-render the policy's source URL and fill missing fields via regex. No LLM."""
    if not policy.source_url:
        return policy

    text = _firecrawl_fetch_markdown(policy.source_url, firecrawl_key)
    if len(text) < 200:
        return policy

    if policy.interest_rate_min is None or policy.interest_rate_max is None:
        ir_min, ir_max = _extract_rates(text)
        ir_min, ir_max, flagged = _validate_rates(ir_min, ir_max)
        if policy.interest_rate_min is None:
            policy.interest_rate_min = ir_min
        if policy.interest_rate_max is None:
            policy.interest_rate_max = ir_max
        policy.rate_flagged = policy.rate_flagged or flagged

    if policy.loan_amount_min is None or policy.loan_amount_max is None:
        amt_min, amt_max = _extract_amounts(text)
        if policy.loan_amount_min is None:
            policy.loan_amount_min = amt_min
        if policy.loan_amount_max is None:
            policy.loan_amount_max = amt_max

    if policy.tenure_min is None or policy.tenure_max is None:
        ten_min, ten_max = _extract_tenure(text)
        if policy.tenure_min is None:
            policy.tenure_min = ten_min
        if policy.tenure_max is None:
            policy.tenure_max = ten_max

    if policy.credit_score_min is None:
        policy.credit_score_min = _extract_credit_score(text)
    if policy.processing_fee is None:
        policy.processing_fee = _extract_processing_fee(text)
    if policy.min_age is None or policy.max_age is None:
        age_min, age_max = _extract_age(text)
        if policy.min_age is None:
            policy.min_age = age_min
        if policy.max_age is None:
            policy.max_age = age_max
    if not policy.employment_types:
        policy.employment_types = _extract_employment(text)
    if not policy.eligible_states:
        policy.eligible_states = _extract_states(text)
    if policy.rate_type is None:
        policy.rate_type = _extract_rate_type(text)
    if policy.rates_as_of is None:
        policy.rates_as_of = _extract_effective_date(text)

    log.info("  Firecrawl enriched: %s (score=%d)", policy.product_name, _count_policy_fields(policy))
    return policy


# ─── BankBazaar scraper ────────────────────────────────────────────────────────

_BB_LOAN_SLUGS = {
    "MSME Loan":             "msme-loan",
    "Business Loan":         "business-loan",
    "Personal Loan":         "personal-loan",
    "Gold Loan":             "gold-loan",
    "Home Loan":             "home-loan",
    "Vehicle Loan":          "used-car-loan",
    "Education Loan":        "education-loan",
    "Loan Against Property": "loan-against-property",
    "Micro Loan":            "microfinance",
    "Microfinance":          "microfinance",
    "Two Wheeler Loan":      "two-wheeler-loan",
    "Working Capital":       "working-capital-loan",
}
_BB_BASE = "https://www.bankbazaar.com"


def _make_bb_slug(lender_name: str) -> str:
    slug = lender_name.lower()
    for suffix in [
        "private limited", "pvt ltd", "pvt. ltd.", "pvt. ltd", "pvt ltd.",
        "limited", "ltd.", "ltd", "finance", "financial", "services",
        "india", "micro", "microfin", "microfinance", "credit",
        "(india)", "(p)", "(pvt)", "co.", "company",
    ]:
        slug = slug.replace(suffix, " ")
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip()).strip("-")
    return slug


def _scrape_bankbazaar(lender_name: str, loan_type: str) -> Optional[PolicyData]:
    loan_slug = _BB_LOAN_SLUGS.get(loan_type)
    if not loan_slug:
        return None
    lender_slug = _make_bb_slug(lender_name)
    url  = f"{_BB_BASE}/{loan_slug}/{lender_slug}.html"
    soup = _fetch(url)
    if not soup:
        return None

    title = (soup.title.get_text() if soup.title else "").lower()
    if "not found" in title or "404" in title or lender_slug.split("-")[0] not in title:
        return None

    text = soup.get_text(" ", strip=True)
    log.info("  BankBazaar hit: %s | %s", lender_name, loan_type)

    ir_min, ir_max   = _extract_rates(text)
    ir_min, ir_max, flagged = _validate_rates(ir_min, ir_max)
    amt_min, amt_max = _extract_amounts(text)
    ten_min, ten_max = _extract_tenure(text)
    credit_min       = _extract_credit_score(text)
    age_min, age_max = _extract_age(text)
    proc_fee         = _extract_processing_fee(text)
    employment       = _extract_employment(text)
    states           = _extract_states(text)
    rate_type        = _extract_rate_type(text)
    rates_as_of      = _extract_effective_date(text)

    if not any([ir_min, amt_min, amt_max, ten_max]):
        return None

    return PolicyData(
        product_name      = f"{lender_name} {loan_type}",
        loan_type         = loan_type,
        loan_amount_min   = amt_min,
        loan_amount_max   = amt_max,
        interest_rate_min = ir_min,
        interest_rate_max = ir_max,
        tenure_min        = ten_min,
        tenure_max        = ten_max,
        credit_score_min  = credit_min,
        min_age           = age_min,
        max_age           = age_max,
        processing_fee    = proc_fee,
        employment_types  = employment,
        eligible_states   = states,
        rate_type         = rate_type,
        rate_flagged      = flagged,
        rates_as_of       = rates_as_of,
        data_source       = "bankbazaar",
        source_url        = url,
    )


# ─── PaisaBazaar scraper ───────────────────────────────────────────────────────

_PB_BASE = "https://www.paisabazaar.com"
_PB_LOAN_SLUGS = {
    "MSME Loan":             "msme-loan",
    "Business Loan":         "business-loan",
    "Personal Loan":         "personal-loan",
    "Gold Loan":             "gold-loan",
    "Home Loan":             "home-loan",
    "Vehicle Loan":          "used-car-loan",
    "Education Loan":        "education-loan",
    "Loan Against Property": "loan-against-property",
    "Two Wheeler Loan":      "two-wheeler-loan",
}


def _scrape_paisabazaar(lender_name: str, loan_type: str) -> Optional[PolicyData]:
    loan_slug = _PB_LOAN_SLUGS.get(loan_type)
    if not loan_slug:
        return None
    lender_slug = _make_bb_slug(lender_name)
    url  = f"{_PB_BASE}/{lender_slug}-{loan_slug}/"
    soup = _fetch(url)
    if not soup:
        return None

    title = (soup.title.get_text() if soup.title else "").lower()
    if "not found" in title or "404" in title:
        return None

    text = soup.get_text(" ", strip=True)
    if len(text) < 500:
        return None

    log.info("  PaisaBazaar hit: %s | %s", lender_name, loan_type)

    ir_min, ir_max   = _extract_rates(text)
    ir_min, ir_max, flagged = _validate_rates(ir_min, ir_max)
    amt_min, amt_max = _extract_amounts(text)
    ten_min, ten_max = _extract_tenure(text)
    credit_min       = _extract_credit_score(text)
    age_min, age_max = _extract_age(text)
    proc_fee         = _extract_processing_fee(text)
    employment       = _extract_employment(text)
    states           = _extract_states(text)
    rate_type        = _extract_rate_type(text)
    rates_as_of      = _extract_effective_date(text)

    if not any([ir_min, amt_min, amt_max, ten_max]):
        return None

    return PolicyData(
        product_name      = f"{lender_name} {loan_type}",
        loan_type         = loan_type,
        loan_amount_min   = amt_min,
        loan_amount_max   = amt_max,
        interest_rate_min = ir_min,
        interest_rate_max = ir_max,
        tenure_min        = ten_min,
        tenure_max        = ten_max,
        credit_score_min  = credit_min,
        min_age           = age_min,
        max_age           = age_max,
        processing_fee    = proc_fee,
        employment_types  = employment,
        eligible_states   = states,
        rate_type         = rate_type,
        rate_flagged      = flagged,
        rates_as_of       = rates_as_of,
        data_source       = "paisabazaar",
        source_url        = url,
    )


# ─── Main entry point ─────────────────────────────────────────────────────────

def scrape_policies(
    lender_name:      str,
    website:          str = "",
    loan_types:       list[str] | None = None,
    firecrawl_key:    str | None = None,
    skip_aggregators: bool = False,
) -> list[PolicyData]:
    """
    Full policy scrape for one lender.

    Priority (own site = authoritative, freshest):
      1. Own website (non-JS)
      2. Firecrawl primary (JS-rendered own site)
      3. BankBazaar (aggregator fallback — may lag weeks)
      4. PaisaBazaar (aggregator fallback)
      5. Firecrawl fill (top up partials)

    Returns list of PolicyData sorted best-first by weighted score.
    """
    if loan_types is None:
        loan_types = ["MSME Loan", "Business Loan", "Personal Loan", "Micro Loan"]

    collected: dict[str, PolicyData] = {}
    _empty = PolicyData("", "")

    # Step 1: own website (fast, non-JS)
    if not skip_aggregators:
        own = _scrape_own_website(lender_name, website, loan_types)
        for pol in own:
            lt = pol.loan_type
            if _count_policy_fields(pol) > _count_policy_fields(collected.get(lt, _empty)):
                collected[lt] = pol

    # Step 2: Firecrawl primary (JS-rendered own site)
    if firecrawl_key and website:
        for lt in loan_types:
            if _count_policy_fields(collected.get(lt, _empty)) >= 9:
                continue
            fc_pol = _firecrawl_scrape_primary(lender_name, website, lt, firecrawl_key)
            if fc_pol and _count_policy_fields(fc_pol) > _count_policy_fields(collected.get(lt, _empty)):
                collected[lt] = fc_pol

    # Step 3: BankBazaar (aggregator fallback)
    if not skip_aggregators:
        for lt in loan_types:
            if _count_policy_fields(collected.get(lt, _empty)) >= 6:
                continue
            time.sleep(_DELAY)
            pol = _scrape_bankbazaar(lender_name, lt)
            if pol and _count_policy_fields(pol) > _count_policy_fields(collected.get(lt, _empty)):
                collected[lt] = pol

    # Step 4: PaisaBazaar (aggregator fallback)
    if not skip_aggregators:
        for lt in loan_types:
            if _count_policy_fields(collected.get(lt, _empty)) >= 6:
                continue
            time.sleep(_DELAY)
            pol = _scrape_paisabazaar(lender_name, lt)
            if pol and _count_policy_fields(pol) > _count_policy_fields(collected.get(lt, _empty)):
                collected[lt] = pol

    # Step 5: Firecrawl fill for remaining partials
    if firecrawl_key:
        for lt, pol in list(collected.items()):
            if _count_policy_fields(pol) < 6:
                collected[lt] = _firecrawl_enrich(pol, firecrawl_key)

    results = [p for p in collected.values() if _count_policy_fields(p) >= 2]
    results.sort(key=_count_policy_fields, reverse=True)
    return results
