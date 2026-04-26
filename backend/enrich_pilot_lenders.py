"""
enrich_pilot_lenders.py
=======================
Enrich 20 pilot lenders (10 NBFCs + 10 banks) from three sources:
  1. Website scraper  — contact info, loan products, states
  2. RBI NBFC list    — registration number, rbi_category (NBFCs only)
  3. Gemini           — financial fields still null after above

Merge rule: NEVER overwrite an existing non-null DB value.
Priority:   existing_db > rbi_official > scraper_HIGH > gemini > scraper_LOW

Usage:
    python enrich_pilot_lenders.py              # enrich only
    python enrich_pilot_lenders.py --approve    # enrich + approve all 20
    python enrich_pilot_lenders.py --dry-run    # show plan, no DB writes
    python enrich_pilot_lenders.py --only SBFC Finance   # single lender
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.errors
from psycopg2.extras import RealDictCursor, Json
import requests

# ── env ────────────────────────────────────────────────────────
_ENV = Path(__file__).resolve().parent.parent / '.env'
try:
    from dotenv import load_dotenv
    load_dotenv(_ENV, override=False)
except ImportError:
    if _ENV.exists():
        with open(_ENV, encoding='utf-8') as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _sep, _rest = _line.partition('=')
                    _k = _k.strip(); _v = _rest.strip().strip('"').strip("'")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL', '')
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_URL   = 'https://generativelanguage.googleapis.com/v1beta/models'
GEMINI_MODEL = 'gemini-2.5-flash'

# ─────────────────────────────────────────────────────────────
# PILOT LISTS
# ─────────────────────────────────────────────────────────────

NBFC_PILOTS: List[Dict[str, str]] = [
    {"name": "Sindhuja Microcredit",         "website": "https://www.sindhujamicrocredit.com",   "type": "NBFC-MFI"},
    {"name": "Cashpor Micro Credit",          "website": "https://www.cashpor.in",                 "type": "NBFC-MFI"},
    {"name": "ICL Fincorp",                   "website": "https://iclfincorp.com",                 "type": "NBFC"},
    {"name": "Kogta Financial",               "website": "https://www.kogtafinancial.com",         "type": "NBFC"},
    {"name": "SBFC Finance",                  "website": "https://www.sbfc.in",                    "type": "NBFC"},
    {"name": "Kisetsu Saison Finance",        "website": "https://www.creditSaison.in",            "type": "NBFC"},
    {"name": "Shalibhadra Capital",           "website": "https://www.shalibhadracapital.com",     "type": "NBFC"},
    {"name": "Sakthi Finance",                "website": "https://www.sakthifinance.com",          "type": "NBFC"},
    {"name": "Chaitanya India Fin Credit",    "website": "https://www.chaitanyaindia.in",          "type": "NBFC-MFI"},
    {"name": "IIFL Samasta",                  "website": "https://www.samasta.co.in",              "type": "NBFC-MFI"},
]

BANK_PILOTS: List[Dict[str, str]] = [
    {"name": "State Bank of India",  "website": "https://www.sbi.co.in",           "type": "PSU Bank"},
    {"name": "Bank of Baroda",       "website": "https://www.bankofbaroda.in",      "type": "PSU Bank"},
    {"name": "Punjab National Bank", "website": "https://www.pnbindia.in",          "type": "PSU Bank"},
    {"name": "Canara Bank",          "website": "https://www.canarabank.com",       "type": "PSU Bank"},
    {"name": "Bank of India",        "website": "https://www.bankofindia.co.in",    "type": "PSU Bank"},
    {"name": "HDFC Bank",            "website": "https://www.hdfcbank.com",         "type": "Private Bank"},
    {"name": "ICICI Bank",           "website": "https://www.icicibank.com",        "type": "Private Bank"},
    {"name": "Axis Bank",            "website": "https://www.axisbank.com",         "type": "Private Bank"},
    {"name": "Kotak Mahindra Bank",  "website": "https://www.kotak.com",            "type": "Private Bank"},
    {"name": "Federal Bank",         "website": "https://www.federalbank.co.in",    "type": "Private Bank"},
]

ALL_PILOTS = NBFC_PILOTS + BANK_PILOTS

ALL_INDIA_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi",
    "Jammu & Kashmir", "Ladakh", "Puducherry", "Chandigarh",
    "Dadra and Nagar Haveli", "Lakshadweep", "Andaman and Nicobar Islands",
]

VALID_PRODUCTS = [
    "MSME Loan", "Personal Loan", "Home Loan", "Business Loan",
    "Vehicle Loan", "Gold Loan", "Education Loan", "Micro Loan",
    "Loan Against Property", "Working Capital", "Agriculture Loan",
    "Credit Card", "Consumer Durable Loan", "EV Loan", "Two Wheeler Loan",
    "Microfinance", "Rural Loan", "Supply Chain Finance",
]

VALID_RBI_CATEGORIES = [
    "NBFC-D", "NBFC-ND", "NBFC-ND-SI", "NBFC-MFI", "NBFC-IFC",
    "NBFC-Factor", "NBFC-ICC", "NBFC-P2P", "NBFC-AA", "NBFC-IC",
]


# ─────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────

def get_db():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL not set in .env')
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def fetch_db_records(conn, names: List[str]) -> Dict[str, Dict]:
    """Return current DB records keyed by company_name (lower-case for matching)."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, company_name, company_type, website,
                   aum_crores, aum_category, last_year_revenue,
                   is_listed, stock_symbol,
                   primary_loan_segments, primary_product, product_types,
                   hq_location, hq_state, operating_states, operating_intensity, pan_india,
                   rbi_category, rbi_registration_number,
                   established_year, employee_count, branch_count,
                   phone, email, rural_presence,
                   quality_score, approval_status, data_source
            FROM lenders
            WHERE company_name = ANY(%s)
        """, (names,))
        rows = cur.fetchall()
    return {r['company_name']: dict(r) for r in rows}


def _is_empty(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, (list,)) and len(val) == 0:
        return True
    if isinstance(val, dict) and len(val) == 0:
        return True
    if isinstance(val, str) and val.strip() == '':
        return True
    return False


# ─────────────────────────────────────────────────────────────
# SOURCE 1: WEBSITE SCRAPER
# ─────────────────────────────────────────────────────────────

def run_scraper(name: str, website: str) -> Optional[Dict]:
    """Run SingleLenderScraper and return its raw dict output."""
    try:
        from scraper.lender_scraper import SingleLenderScraper
        scraper = SingleLenderScraper()
        result = scraper.scrape(name, website)
        log.info('  [scraper] %s fields extracted', sum(
            1 for v in result.values()
            if v not in (None, '', [], False, {}) and not str(v).startswith('_')
        ))
        return result
    except Exception as exc:
        log.warning('  [scraper] failed: %s', exc)
        return None


# ─────────────────────────────────────────────────────────────
# SOURCE 2: RBI NBFC LIST (registration + category)
# ─────────────────────────────────────────────────────────────

_rbi_cache: Optional[List[Dict]] = None  # module-level cache so we fetch once


def _fetch_rbi_nbfc_list() -> List[Dict]:
    """
    Fetch the RBI registered NBFC list and return list of
    {name, registration_number, category, state}.
    Cached in memory for the lifetime of the process.
    """
    global _rbi_cache
    if _rbi_cache is not None:
        return _rbi_cache

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning('[rbi_lookup] beautifulsoup4 not installed — skipping RBI enrichment')
        _rbi_cache = []
        return _rbi_cache

    url = 'https://www.rbi.org.in/Scripts/BS_NBFCList.aspx'
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    results: List[Dict] = []
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if not resp.ok:
            log.warning('[rbi_lookup] HTTP %s — skipping', resp.status_code)
            _rbi_cache = []
            return _rbi_cache

        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table', id=re.compile(r'gvList', re.I)) or soup.find('table', class_=re.compile(r'tablebg', re.I))
        if not table:
            # Try any table with Company Name column
            for t in soup.find_all('table'):
                headers_row = t.find('tr')
                if headers_row and 'Company' in headers_row.get_text():
                    table = t
                    break

        if not table:
            log.warning('[rbi_lookup] could not find NBFC list table on RBI page')
            _rbi_cache = []
            return _rbi_cache

        rows = table.find_all('tr')
        # Parse header row to find column positions
        header_cells = rows[0].find_all(['th', 'td']) if rows else []
        col_map: Dict[str, int] = {}
        for i, cell in enumerate(header_cells):
            text = cell.get_text(strip=True).lower()
            if 'company' in text or 'name' in text:
                col_map['name'] = i
            elif 'registr' in text and 'no' in text:
                col_map['reg_no'] = i
            elif 'categor' in text or 'type' in text:
                col_map['category'] = i
            elif 'state' in text:
                col_map['state'] = i

        for row in rows[1:]:
            cells = row.find_all('td')
            if not cells:
                continue
            entry: Dict[str, str] = {}
            if 'name' in col_map and col_map['name'] < len(cells):
                entry['name'] = cells[col_map['name']].get_text(strip=True)
            if 'reg_no' in col_map and col_map['reg_no'] < len(cells):
                entry['registration_number'] = cells[col_map['reg_no']].get_text(strip=True)
            if 'category' in col_map and col_map['category'] < len(cells):
                entry['category'] = cells[col_map['category']].get_text(strip=True)
            if 'state' in col_map and col_map['state'] < len(cells):
                entry['state'] = cells[col_map['state']].get_text(strip=True)
            if entry.get('name'):
                results.append(entry)

        log.info('[rbi_lookup] loaded %d entries from RBI NBFC list', len(results))
    except Exception as exc:
        log.warning('[rbi_lookup] failed to fetch RBI list: %s', exc)
        results = []

    _rbi_cache = results
    return _rbi_cache


def _fuzzy_match(query: str, candidates: List[Dict], threshold: float = 0.6) -> Optional[Dict]:
    """
    Simple token-overlap fuzzy match.
    Returns the best candidate dict or None if no match exceeds threshold.
    """
    q_tokens = set(re.sub(r'[^a-z0-9 ]', '', query.lower()).split())
    best_score = 0.0
    best_match = None
    for entry in candidates:
        name = entry.get('name', '')
        e_tokens = set(re.sub(r'[^a-z0-9 ]', '', name.lower()).split())
        if not q_tokens or not e_tokens:
            continue
        overlap = len(q_tokens & e_tokens)
        score = overlap / max(len(q_tokens), len(e_tokens))
        if score > best_score:
            best_score = score
            best_match = entry
    return best_match if best_score >= threshold else None


def rbi_nbfc_lookup(name: str) -> Dict[str, Optional[str]]:
    """
    Look up a company in the RBI NBFC list.
    Returns {rbi_registration_number, rbi_category} — None for each if not found.
    """
    rbi_list = _fetch_rbi_nbfc_list()
    if not rbi_list:
        return {'rbi_registration_number': None, 'rbi_category': None}

    match = _fuzzy_match(name, rbi_list)
    if not match:
        log.info('  [rbi_lookup] no match for "%s"', name)
        return {'rbi_registration_number': None, 'rbi_category': None}

    log.info('  [rbi_lookup] matched "%s" -> "%s"', name, match.get('name'))
    reg = match.get('registration_number', '') or ''
    cat = match.get('category', '') or ''

    # Validate registration number format — must contain a digit
    if reg and not any(c.isdigit() for c in reg):
        log.warning('  [rbi_lookup] ignoring invalid reg_no: %s', reg)
        reg = ''

    # Normalize category to allowed values
    cat_norm: Optional[str] = None
    cat_upper = cat.upper().replace(' ', '-')
    for allowed in VALID_RBI_CATEGORIES:
        if allowed.upper() in cat_upper or cat_upper in allowed.upper():
            cat_norm = allowed
            break

    return {
        'rbi_registration_number': reg or None,
        'rbi_category': cat_norm,
    }


# ─────────────────────────────────────────────────────────────
# SOURCE 3: GEMINI ENRICHMENT
# ─────────────────────────────────────────────────────────────

_SYSTEM_INSTRUCTION = """\
You are a data extraction engine for an Indian financial lender database.

RULES:
1. Return null for any field you cannot verify with HIGH confidence.
2. Do NOT estimate, infer, or guess numeric values (AUM, revenue, employees).
3. Do NOT hallucinate registration numbers, emails, phone numbers, or websites.
4. Numbers are in Indian Crores (1 Crore = 10 million) unless stated otherwise.
5. Constrained fields must use ONLY the allowed values listed.
6. Prefer official sources: RBI filings, SEBI, company annual reports.
7. Null is ALWAYS safer than an incorrect value.

OUTPUT: Return ONLY valid JSON — no markdown, no explanation, no comments.
"""


def _make_gemini_prompt(
    name: str,
    website: str,
    company_type: str,
    null_fields: List[str],
    scraper_data: Optional[Dict],
    rbi_data: Optional[Dict],
) -> str:
    products_str = ', '.join(f'"{p}"' for p in VALID_PRODUCTS)
    valid_rbi    = ', '.join(VALID_RBI_CATEGORIES)
    states_str   = ', '.join(f'"{s}"' for s in ALL_INDIA_STATES)

    verified_lines = []
    if scraper_data:
        conf = scraper_data.get('_confidence', {})
        for field, label in [
            ('phone', 'phone'), ('email', 'email'), ('hq_state', 'hq_state'),
            ('established_year', 'established_year'), ('employee_count', 'employee_count'),
            ('rbi_registration', 'rbi_registration_number'),
        ]:
            val = scraper_data.get(field)
            c = conf.get(field, {}).get('confidence', 'LOW')
            if val not in (None, '', [], False) and c in ('HIGH', 'MEDIUM'):
                verified_lines.append(f'  {label}: {val}')
        tags = scraper_data.get('loan_tags', [])
        if tags:
            verified_lines.append(f'  loan_products: {tags}')
        states = scraper_data.get('operating_states', [])
        if states:
            verified_lines.append(f'  operating_states: {states}')

    if rbi_data:
        if rbi_data.get('rbi_registration_number'):
            verified_lines.append(f'  rbi_registration_number: {rbi_data["rbi_registration_number"]}')
        if rbi_data.get('rbi_category'):
            verified_lines.append(f'  rbi_category: {rbi_data["rbi_category"]}')

    verified_block = ''
    if verified_lines:
        verified_block = (
            '\n\nPRE-VERIFIED DATA (scraped / RBI official — treat as FACTUAL):\n'
            + '\n'.join(verified_lines)
            + '\n\nFor pre-verified fields above: echo the same value in the JSON output.'
        )
    else:
        verified_block = (
            '\n\nWARNING: No website data was scraped. '
            'Return null for contact/location/state fields unless verifiable from '
            'official RBI/SEBI/MCA registries. Do NOT guess.'
        )

    # Only request the null fields
    schema_parts = {
        'aum_crores':             'number | null',
        'last_year_revenue':      'number | null',
        'is_listed':              'true | false',
        'stock_symbol':           'string | null',
        'primary_loan_segments':  f'subset of [{products_str}] | []',
        'primary_product':        'string | null',
        'hq_city':                'string | null',
        'hq_state':               'full Indian state name | null',
        'operating_states':       f'array of state names from [{states_str}] | ["PAN_INDIA"] | []',
        'operating_intensity':    '"Pan India" | "Regional" | "Single State" | null',
        'rbi_category':           f'one of [{valid_rbi}] | null',
        'rbi_registration_number':'RBI CoR number e.g. N.13.02437 | null',
        'established_year':       '4-digit year | null',
        'employee_count':         'integer | null',
        'branch_count':           'integer | null',
        'phone':                  'string | null',
        'email':                  'string | null',
        'rural_presence':         'true | false | null',
    }
    requested = {k: v for k, v in schema_parts.items() if k in null_fields}
    if not requested:
        return ''

    schema_json = json.dumps(
        {k: f'<{v}>' for k, v in requested.items()},
        indent=2,
    )

    return (
        f'Extract data for: {name} ({company_type})\n'
        f'Website: {website or "unknown"}\n'
        f'{verified_block}\n\n'
        f'Return ONLY these null fields as JSON (null if unknown):\n{schema_json}'
    )


def _parse_gemini_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    text = re.sub(r'```json\s*\n?', '', text)
    text = re.sub(r'```\s*\n?', '', text)
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # brace-match
    start = text.find('{')
    end   = text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except Exception:
            pass
    return None


def gemini_enrich(
    name: str,
    website: str,
    company_type: str,
    null_fields: List[str],
    scraper_data: Optional[Dict],
    rbi_data: Optional[Dict],
) -> Optional[Dict]:
    """Call Gemini only for fields that are still null after scraper + RBI lookup."""
    if not GEMINI_KEY:
        log.warning('  [gemini] GEMINI_API_KEY not set — skipping')
        return None
    if not null_fields:
        log.info('  [gemini] nothing to enrich')
        return None

    prompt = _make_gemini_prompt(name, website, company_type, null_fields, scraper_data, rbi_data)
    if not prompt:
        return None

    url     = f'{GEMINI_URL}/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}'
    payload = {
        'systemInstruction': {'parts': [{'text': _SYSTEM_INSTRUCTION}]},
        'contents':          [{'parts': [{'text': prompt}]}],
        'generationConfig':  {'temperature': 0, 'maxOutputTokens': 4096},
    }

    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code == 429:
                wait = 90 * attempt
                log.warning('  [gemini] rate-limited, waiting %ds', wait)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                log.warning('  [gemini] HTTP %s (attempt %d)', resp.status_code, attempt)
                time.sleep(5 * attempt)
                continue

            body = resp.json()
            parts = (body.get('candidates', [{}])[0]
                     .get('content', {}).get('parts', []))
            raw = next((p['text'] for p in parts if p.get('text')), '')
            parsed = _parse_gemini_json(raw)
            if parsed:
                filled = sum(1 for v in parsed.values() if v not in (None, '', [], False))
                log.info('  [gemini] filled %d fields', filled)
                return parsed
            log.warning('  [gemini] JSON parse failed (attempt %d)', attempt)
            time.sleep(3)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.warning('  [gemini] error: %s', exc)
            time.sleep(5 * attempt)

    return None


# ─────────────────────────────────────────────────────────────
# MERGE LOGIC
# ─────────────────────────────────────────────────────────────

def _parse_states(val: Any) -> List[str]:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return [s.strip() for s in val.split(',') if s.strip()]
    return []


def _parse_segments(val: Any) -> List[str]:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return [s.strip() for s in val.split(',') if s.strip()]
    return []


def merge_enrichment(
    db: Dict,
    scraper: Optional[Dict],
    rbi: Optional[Dict],
    gemini: Optional[Dict],
) -> Dict:
    """
    Merge all sources into the DB record.
    Rule: only fill null DB fields — never overwrite non-null.
    Priority per field: rbi > scraper(HIGH/MED) > gemini > scraper(LOW)
    """
    result = dict(db)

    def fill(field: str, value: Any):
        """Set result[field] only if it's currently empty."""
        if _is_empty(result.get(field)) and not _is_empty(value):
            result[field] = value

    # ── RBI data (highest authority for registration + category) ──
    if rbi:
        fill('rbi_registration_number', rbi.get('rbi_registration_number'))
        fill('rbi_category',            rbi.get('rbi_category'))

    # ── Scraper data (HIGH/MEDIUM confidence contact + products) ──
    if scraper:
        conf = scraper.get('_confidence', {})
        def conf_level(f): return conf.get(f, {}).get('confidence', 'LOW')

        for scraper_field, db_field in [
            ('phone',            'phone'),
            ('email',            'email'),
            ('hq_state',         'hq_state'),
            ('established_year', 'established_year'),
            ('employee_count',   'employee_count'),
            ('branch_count',     'branch_count'),
        ]:
            val = scraper.get(scraper_field)
            c   = conf_level(scraper_field)
            if c in ('HIGH', 'MEDIUM') and not _is_empty(val):
                fill(db_field, val)

        # loan tags → primary_loan_segments
        tags = scraper.get('loan_tags', [])
        if tags:
            valid_tags = [t for t in tags if t in VALID_PRODUCTS]
            fill('primary_loan_segments', valid_tags if valid_tags else None)

        # operating states
        raw_states = scraper.get('operating_states', [])
        if raw_states:
            if 'PAN_INDIA' in raw_states:
                fill('operating_states', sorted(ALL_INDIA_STATES))
                fill('pan_india', True)
            else:
                fill('operating_states', raw_states)
                if not _is_empty(raw_states):
                    is_pan = len([s for s in raw_states if s in ALL_INDIA_STATES]) >= 20
                    fill('pan_india', is_pan)

        # hq_location (city)
        hq_city = scraper.get('hq_city') or scraper.get('location')
        if hq_city and conf_level('hq_city') in ('HIGH', 'MEDIUM'):
            fill('hq_location', hq_city)

        # rbi registration (scraper)
        rbi_reg = scraper.get('rbi_registration')
        if rbi_reg and any(c.isdigit() for c in str(rbi_reg)):
            fill('rbi_registration_number', rbi_reg)

    # ── Gemini data (fills financial fields) ──
    if gemini:
        for g_field, db_field in [
            ('aum_crores',             'aum_crores'),
            ('last_year_revenue',      'last_year_revenue'),
            ('is_listed',              'is_listed'),
            ('stock_symbol',           'stock_symbol'),
            ('primary_product',        'primary_product'),
            ('hq_city',                'hq_location'),
            ('hq_state',               'hq_state'),
            ('operating_intensity',    'operating_intensity'),
            ('rbi_category',           'rbi_category'),
            ('rbi_registration_number','rbi_registration_number'),
            ('established_year',       'established_year'),
            ('employee_count',         'employee_count'),
            ('branch_count',           'branch_count'),
            ('phone',                  'phone'),
            ('email',                  'email'),
            ('rural_presence',         'rural_presence'),
        ]:
            val = gemini.get(g_field)
            if val not in (None, '', [], False):
                fill(db_field, val)

        # Gemini segments
        g_segs = gemini.get('primary_loan_segments', [])
        if g_segs:
            valid = [s for s in g_segs if s in VALID_PRODUCTS]
            fill('primary_loan_segments', valid if valid else None)

        # Gemini states
        g_states = gemini.get('operating_states', [])
        if g_states:
            if g_states == ['PAN_INDIA']:
                fill('operating_states', sorted(ALL_INDIA_STATES))
                fill('pan_india', True)
            else:
                valid_states = [s for s in g_states if s in ALL_INDIA_STATES]
                fill('operating_states', valid_states)
                if len(valid_states) >= 20:
                    fill('pan_india', True)

    # ── Scraper LOW confidence (last resort) ──
    if scraper:
        for scraper_field, db_field in [
            ('phone', 'phone'), ('email', 'email'), ('hq_state', 'hq_state'),
        ]:
            val = scraper.get(scraper_field)
            if not _is_empty(val):
                fill(db_field, val)

    # ── Recompute aum_category ──
    aum = result.get('aum_crores')
    if aum is not None:
        try:
            aum_f = float(aum)
            if aum_f < 500:
                result['aum_category'] = 'Micro'
            elif aum_f < 5000:
                result['aum_category'] = 'Small'
            elif aum_f < 50000:
                result['aum_category'] = 'Mid'
            else:
                result['aum_category'] = 'Large'
        except (TypeError, ValueError):
            pass

    # ── Recompute quality_score ──
    scored_fields = [
        'company_name', 'website', 'hq_state', 'company_type',
        'primary_loan_segments', 'operating_states', 'phone', 'email',
        'aum_crores', 'established_year', 'employee_count', 'rbi_registration_number',
        'rbi_category', 'operating_intensity', 'hq_location',
    ]
    present = sum(1 for f in scored_fields if not _is_empty(result.get(f)))
    result['quality_score'] = round(present / len(scored_fields), 3)

    result['data_source']     = 'scraper+gemini'
    result['extraction_status'] = 'success'

    return result


# ─────────────────────────────────────────────────────────────
# UPSERT BACK TO DB
# ─────────────────────────────────────────────────────────────

_UPSERT_FIELDS = [
    'company_name', 'company_type', 'website',
    'aum_crores', 'aum_category', 'last_year_revenue',
    'is_listed', 'stock_symbol',
    'primary_loan_segments', 'primary_product',
    'hq_location', 'hq_state', 'operating_states', 'operating_intensity', 'pan_india',
    'rbi_category', 'rbi_registration_number',
    'established_year', 'employee_count', 'branch_count',
    'phone', 'email', 'rural_presence',
    'quality_score', 'data_source', 'extraction_status',
]

# These must be sent as JSON/array types
_TEXT_ARRAY = {'primary_loan_segments', 'operating_states'}
_BOOL_FIELDS = {'is_listed', 'pan_india', 'rural_presence'}


def _coerce_for_db(field: str, val: Any) -> Any:
    if val is None or val == '' or val == 'None':
        return None
    if field in _TEXT_ARRAY:
        if isinstance(val, list):
            return val
        try:
            return json.loads(val)
        except Exception:
            return []
    if field in _BOOL_FIELDS:
        if isinstance(val, bool):
            return val
        return str(val).lower() in ('true', '1', 'yes')
    if field in ('aum_crores', 'last_year_revenue', 'quality_score'):
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    if field in ('established_year', 'employee_count', 'branch_count'):
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return None
    return val


def upsert_record(conn, record: Dict, dry_run: bool = False) -> str:
    """Upsert a single enriched lender record. Returns 'inserted' | 'updated'."""
    name = record['company_name']
    if dry_run:
        log.info('  [dry-run] would upsert "%s"', name)
        return 'dry_run'

    fields = [f for f in _UPSERT_FIELDS if f != 'company_name']
    set_clauses = ', '.join(f'{f} = EXCLUDED.{f}' for f in fields) + ', last_scraped_at = NOW()'
    col_list    = 'company_name, ' + ', '.join(fields) + ', last_scraped_at'
    placeholders= '%s, ' + ', '.join(['%s'] * len(fields)) + ', NOW()'

    values = [name] + [_coerce_for_db(f, record.get(f)) for f in fields]

    sql = f"""
        INSERT INTO lenders ({col_list})
        VALUES ({placeholders})
        ON CONFLICT (company_name) DO UPDATE SET {set_clauses}
        RETURNING (xmax = 0) AS was_inserted
    """

    def _execute(exclude_fields=None):
        _fields = [f for f in fields if f not in (exclude_fields or [])]
        _set    = ', '.join(f'{f} = EXCLUDED.{f}' for f in _fields) + ', last_scraped_at = NOW()'
        _cols   = 'company_name, ' + ', '.join(_fields) + ', last_scraped_at'
        _ph     = '%s, ' + ', '.join(['%s'] * len(_fields)) + ', NOW()'
        _vals   = [name] + [_coerce_for_db(f, record.get(f)) for f in _fields]
        _sql    = f"""
            INSERT INTO lenders ({_cols})
            VALUES ({_ph})
            ON CONFLICT (company_name) DO UPDATE SET {_set}
            RETURNING (xmax = 0) AS was_inserted
        """
        fresh = get_db()
        try:
            with fresh.cursor() as cur:
                cur.execute(_sql, _vals)
                row = cur.fetchone()
            fresh.commit()
            return row
        finally:
            fresh.close()

    # Always open a fresh connection per upsert — avoids PgBouncer idle-timeout drops
    try:
        row = _execute()
    except psycopg2.errors.UniqueViolation:
        # RBI registration number conflicts with another lender — retry without it
        log.warning('  [upsert] unique conflict on rbi_registration_number — retrying without it')
        row = _execute(exclude_fields=['rbi_registration_number'])
    return 'inserted' if (row and row[0]) else 'updated'


def approve_pilots(names: List[str], dry_run: bool = False):
    """Set approval_status = 'approved' for the given lender names."""
    if dry_run:
        log.info('[dry-run] would approve %d lenders', len(names))
        return
    fresh = get_db()
    try:
        with fresh.cursor() as cur:
            cur.execute(
                "UPDATE lenders SET approval_status = 'approved' WHERE company_name = ANY(%s)",
                (names,)
            )
            updated = cur.rowcount
        fresh.commit()
    finally:
        fresh.close()
    log.info('Approved %d lenders', updated)


def _enrich_one_with_flags(
    pilot: Dict[str, str],
    db_record: Optional[Dict],
    is_nbfc: bool,
    skip_scraper: bool = False,
    skip_gemini: bool = False,
    skip_rbi: bool = False,
    dry_run: bool = False,
) -> Dict:
    """Wrapper around enrich_one that respects --skip-* flags."""
    name    = pilot['name']
    website = pilot['website']
    ctype   = pilot['type']

    current = db_record or {'company_name': name, 'company_type': ctype, 'website': website}
    if not current.get('website'):
        current['website'] = website

    log.info('[%s] enriching...', name)

    time.sleep(1.5)
    scraper_data = None if skip_scraper else run_scraper(name, current['website'])

    rbi_data: Optional[Dict] = None
    if is_nbfc and not skip_rbi:
        rbi_data = rbi_nbfc_lookup(name)

    partial = merge_enrichment(current, scraper_data, rbi_data, None)
    all_enrichable_fields = [
        'aum_crores', 'last_year_revenue', 'is_listed', 'stock_symbol',
        'primary_loan_segments', 'primary_product', 'hq_location', 'hq_state',
        'operating_states', 'operating_intensity', 'rbi_category',
        'rbi_registration_number', 'established_year', 'employee_count',
        'branch_count', 'phone', 'email', 'rural_presence',
    ]
    null_fields = [f for f in all_enrichable_fields if _is_empty(partial.get(f))]
    log.info('  [status] %d fields still null after scraper+RBI: %s',
             len(null_fields), null_fields[:8])

    time.sleep(1.5)
    gemini_data = None
    if not skip_gemini:
        gemini_data = gemini_enrich(name, current['website'], ctype, null_fields,
                                    scraper_data, rbi_data)

    enriched = merge_enrichment(current, scraper_data, rbi_data, gemini_data)
    enriched['company_name'] = name
    enriched['company_type'] = ctype

    filled_after = sum(1 for f in all_enrichable_fields if not _is_empty(enriched.get(f)))
    log.info('  [result] %d/%d enrichable fields filled | quality_score=%.2f',
             filled_after, len(all_enrichable_fields),
             enriched.get('quality_score', 0))

    return enriched


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Enrich 20 pilot lenders')
    parser.add_argument('--approve',  action='store_true',
                        help='Approve all pilot lenders in DB after enrichment')
    parser.add_argument('--dry-run',  action='store_true',
                        help='Simulate enrichment without writing to DB')
    parser.add_argument('--only',     type=str, default=None,
                        help='Enrich a single lender by name (substring match)')
    parser.add_argument('--skip-scraper', action='store_true',
                        help='Skip website scraping (Gemini-only mode)')
    parser.add_argument('--skip-gemini',  action='store_true',
                        help='Skip Gemini enrichment (scraper+RBI only)')
    parser.add_argument('--skip-rbi',     action='store_true',
                        help='Skip RBI NBFC list lookup')
    parser.add_argument('--delay', type=float, default=3.5,
                        help='Seconds between Gemini calls (default 3.5)')
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error('DATABASE_URL not set in .env')
        sys.exit(1)

    pilots = ALL_PILOTS
    if args.only:
        pilots = [p for p in ALL_PILOTS if args.only.lower() in p['name'].lower()]
        if not pilots:
            log.error('No pilot found matching "%s"', args.only)
            sys.exit(1)
        log.info('Filtered to: %s', [p['name'] for p in pilots])

    conn = get_db()

    # ── Fetch current DB state ────────────────────────────────
    names = [p['name'] for p in pilots]
    db_records = fetch_db_records(conn, names)
    log.info('Found %d/%d pilots already in DB', len(db_records), len(pilots))

    # ── Prefetch RBI list once (module-level cache) ───────────
    if not args.skip_rbi:
        log.info('Prefetching RBI NBFC list...')
        _fetch_rbi_nbfc_list()

    # ── Enrichment loop ───────────────────────────────────────
    results: List[Dict[str, Any]] = []
    stats = {'inserted': 0, 'updated': 0, 'dry_run': 0, 'errors': 0}

    nbfc_names = {p['name'] for p in NBFC_PILOTS}

    for i, pilot in enumerate(pilots, 1):
        name     = pilot['name']
        is_nbfc  = name in nbfc_names
        db_rec   = db_records.get(name)

        log.info('\n[%d/%d] -- %s --', i, len(pilots), name)

        try:
            enriched = _enrich_one_with_flags(
                pilot, db_rec, is_nbfc,
                skip_scraper=args.skip_scraper,
                skip_gemini=args.skip_gemini,
                skip_rbi=args.skip_rbi,
                dry_run=args.dry_run,
            )
            results.append(enriched)
            action = upsert_record(conn, enriched, dry_run=args.dry_run)
            stats[action] = stats.get(action, 0) + 1
            log.info('  [db] %s', action)
        except KeyboardInterrupt:
            log.warning('\nInterrupted -- saving partial results')
            break
        except Exception as exc:
            log.error('  [error] %s: %s', name, exc)
            stats['errors'] += 1

        # Respect Gemini rate limit
        if not args.skip_gemini and i < len(pilots):
            time.sleep(args.delay)

    # ── Approve ───────────────────────────────────────────────
    if args.approve and results:
        approved_names = [r['company_name'] for r in results]
        log.info('\nApproving %d lenders...', len(approved_names))
        approve_pilots(approved_names, dry_run=args.dry_run)

    conn.close()

    # ── Summary ───────────────────────────────────────────────
    print(f'\n{"="*60}')
    print(f'ENRICHMENT COMPLETE — {len(pilots)} pilots')
    print(f'  Inserted : {stats.get("inserted", 0)}')
    print(f'  Updated  : {stats.get("updated", 0)}')
    print(f'  Errors   : {stats.get("errors", 0)}')
    if args.dry_run:
        print('  (DRY RUN — no DB writes)')
    if args.approve:
        print(f'  Approved : {len(results)} lenders')
    print(f'{"="*60}')

    if results:
        print('\nQuality scores:')
        for r in sorted(results, key=lambda x: x.get('quality_score', 0), reverse=True):
            qs = r.get('quality_score', 0)
            segs = r.get('primary_loan_segments', []) or []
            states_count = len(r.get('operating_states') or [])
            print(f'  {r["company_name"]:<35} score={qs:.2f}  '
                  f'products={len(segs)}  states={states_count}')


if __name__ == '__main__':
    main()
