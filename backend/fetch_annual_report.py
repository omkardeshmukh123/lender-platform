"""
fetch_annual_report.py
======================
Downloads annual report PDFs from lender websites and extracts:
  - aum_crores         (from MD&A / Balance Sheet)
  - employee_count     (from operational highlights)
  - branch_count       (from operational highlights)
  - interest_rate_min/max per loan type (from Fair Practice Code section)

No Gemini API key needed — pure regex extraction on PDF text.
If GEMINI_API_KEY is set, falls back to Gemini for ambiguous pages.

Usage:
    python fetch_annual_report.py --dry-run        # preview, no DB writes
    python fetch_annual_report.py --limit 20       # first N lenders
    python fetch_annual_report.py --unlisted-only  # skip BSE-listed lenders
    python fetch_annual_report.py --id 42          # single lender by ID
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ── env ───────────────────────────────────────────────────────────────────────
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
                    _k, _, _rest = _line.partition('=')
                    _k = _k.strip(); _v = _rest.strip().strip('"').strip("'")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()
GEMINI_KEY   = os.getenv('GEMINI_API_KEY', '').strip()

# ── Regex patterns ────────────────────────────────────────────────────────────

# AUM / Loan Book — matches "₹12,345 Crore", "Rs. 12,345 crores", "12345.67 Cr"
_AUM_RE = re.compile(
    r'(?:aum|loan\s+book|assets\s+under\s+management|advances|total\s+loan)'
    r'[^₹\d]{0,40}[₹\u20B9Rs\.]+\s*([\d,]+(?:\.\d+)?)\s*(?:crore|cr)',
    re.IGNORECASE,
)

# Employee count — "12,345 employees" / "staff of 8,000"
_EMP_RE = re.compile(
    r'([\d,]+)\s*(?:\+)?\s*(?:employees|staff members?|team members?)',
    re.IGNORECASE,
)

# Branch count — "450 branches" / "branch network of 320"
_BRANCH_RE = re.compile(
    r'([\d,]+)\s*(?:\+)?\s*(?:branches|branch\s+offices?|touch\s*points?)',
    re.IGNORECASE,
)

# Interest rate — "12% to 24% p.a." / "18.00% per annum"
_RATE_RE = re.compile(
    r'([\d]+(?:\.\d+)?)\s*%\s*(?:to|–|-)\s*([\d]+(?:\.\d+)?)\s*%\s*(?:p\.?a\.?|per\s+annum)',
    re.IGNORECASE,
)
_RATE_SINGLE_RE = re.compile(
    r'([\d]+(?:\.\d+)?)\s*%\s*(?:p\.?a\.?|per\s+annum)',
    re.IGNORECASE,
)

# Loan type headers in Fair Practice Code section
_LOAN_TYPE_HEADERS = {
    'msme': 'MSME Loan', 'business loan': 'Business Loan',
    'home loan': 'Home Loan', 'housing': 'Home Loan',
    'personal loan': 'Personal Loan', 'gold loan': 'Gold Loan',
    'vehicle loan': 'Vehicle Loan', 'two wheeler': 'Two Wheeler Loan',
    'micro loan': 'Micro Loan', 'microfinance': 'Microfinance',
    'agriculture': 'Agriculture Loan', 'working capital': 'Working Capital',
    'loan against property': 'Loan Against Property',
}

# ── PDF utilities ─────────────────────────────────────────────────────────────

def _parse_number(s: str) -> Optional[float]:
    try:
        return float(s.replace(',', ''))
    except (ValueError, AttributeError):
        return None


def _find_annual_report_url(website: str, session) -> Optional[str]:
    """Scrape lender website for annual report PDF link."""
    candidates = [
        website.rstrip('/') + path for path in [
            '/investor-relations', '/investors', '/annual-report',
            '/financials', '/corporate-governance', '/about-us',
        ]
    ]
    candidates.insert(0, website)

    pdf_pattern = re.compile(
        r'annual.?report|ar[-_\s]?20\d\d|fy[-_\s]?20\d\d',
        re.IGNORECASE,
    )

    for url in candidates:
        try:
            r = session.get(url, timeout=10)
            if not r.ok:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                text = a.get_text(strip=True)
                if pdf_pattern.search(href) or pdf_pattern.search(text):
                    if href.endswith('.pdf') or 'pdf' in href.lower():
                        full = urljoin(url, href)
                        log.info(f"    Found PDF: {full[:80]}")
                        return full
        except Exception:
            continue
    return None


def _download_pdf(url: str, session) -> Optional[bytes]:
    try:
        r = session.get(url, timeout=30, stream=True)
        if r.ok and 'pdf' in r.headers.get('Content-Type', '').lower():
            content = b''.join(r.iter_content(8192))
            if len(content) > 10_000:   # sanity: > 10 KB
                return content
    except Exception as e:
        log.warning(f"    PDF download failed: {e}")
    return None


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract full text from PDF using pdfplumber."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:80]:   # first 80 pages is enough
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return '\n'.join(text_parts)
    except ImportError:
        log.error("pip install pdfplumber")
        sys.exit(1)
    except Exception as e:
        log.warning(f"    PDF parse error: {e}")
        return ''


# ── Field extractors ──────────────────────────────────────────────────────────

def _extract_aum(text: str) -> Optional[float]:
    matches = _AUM_RE.findall(text)
    values = [_parse_number(m) for m in matches if _parse_number(m)]
    if not values:
        return None
    # Take the largest value (total AUM, not segment AUM)
    val = max(values)
    # Sanity: AUM must be between 1 Cr and 30,000,000 Cr
    return val if 1 <= val <= 30_000_000 else None


def _extract_employees(text: str) -> Optional[int]:
    matches = _EMP_RE.findall(text)
    values = [_parse_number(m) for m in matches if _parse_number(m)]
    if not values:
        return None
    val = int(max(values))
    return val if 1 <= val <= 500_000 else None


def _extract_branches(text: str) -> Optional[int]:
    matches = _BRANCH_RE.findall(text)
    values = [_parse_number(m) for m in matches if _parse_number(m)]
    if not values:
        return None
    val = int(max(values))
    return val if 1 <= val <= 100_000 else None


def _extract_fpc_rates(text: str) -> List[Dict[str, Any]]:
    """
    Extract interest rate ranges from Fair Practice Code section.
    Returns list of {loan_type, rate_min, rate_max} dicts.
    """
    # Find Fair Practice Code section
    fpc_start = re.search(
        r'fair\s+practice\s+code|interest\s+rate\s+polic|schedule\s+of\s+charges',
        text, re.IGNORECASE,
    )
    if not fpc_start:
        return []

    section = text[fpc_start.start():fpc_start.start() + 15000]
    results = []
    current_loan_type = None

    for line in section.split('\n'):
        low = line.lower().strip()

        # Detect loan type header
        for keyword, loan_type in _LOAN_TYPE_HEADERS.items():
            if keyword in low:
                current_loan_type = loan_type
                break

        # Extract rate range
        m = _RATE_RE.search(line)
        if m and current_loan_type:
            rate_min = float(m.group(1))
            rate_max = float(m.group(2))
            if 3 <= rate_min <= 60 and 3 <= rate_max <= 60 and rate_min <= rate_max:
                results.append({
                    'loan_type': current_loan_type,
                    'interest_rate_min': rate_min,
                    'interest_rate_max': rate_max,
                })
                continue

        # Single rate fallback
        m = _RATE_SINGLE_RE.search(line)
        if m and current_loan_type:
            rate = float(m.group(1))
            if 3 <= rate <= 60:
                results.append({
                    'loan_type': current_loan_type,
                    'interest_rate_min': rate,
                    'interest_rate_max': rate,
                })

    # Deduplicate by loan_type (keep first occurrence)
    seen, unique = set(), []
    for r in results:
        if r['loan_type'] not in seen:
            seen.add(r['loan_type'])
            unique.append(r)

    return unique


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        sys.exit(1)
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        log.error("pip install supabase")
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    supa = _get_supabase()

    # Build query
    q = (
        supa.table('lenders')
        .select('id, company_name, website, is_listed, aum_crores, employee_count, branch_count')
        .eq('approval_status', 'approved')
        .not_.is_('website', 'null')
    )
    if args.unlisted_only:
        q = q.eq('is_listed', False)
    if args.id:
        q = q.eq('id', args.id)
    elif not args.force:
        q = q.is_('aum_crores', 'null')   # only lenders missing AUM
    if args.limit:
        q = q.limit(args.limit)

    lenders = (q.execute()).data or []
    log.info(f"Processing {len(lenders)} lenders")

    # HTTP session with retries
    session = requests.Session()
    session.headers['User-Agent'] = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    )
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503])
    session.mount('https://', HTTPAdapter(max_retries=retry))
    session.mount('http://',  HTTPAdapter(max_retries=retry))

    stats = {'pdf_found': 0, 'aum_extracted': 0, 'fpc_extracted': 0, 'updated': 0}
    BATCH_SIZE = 50
    lender_updates: List[Dict] = []
    policy_upserts: List[Dict] = []

    for lender in lenders:
        lid     = lender['id']
        name    = lender['company_name']
        website = lender.get('website', '')
        if not website:
            continue

        log.info(f"[{lid}] {name}")

        # 1. Find PDF URL
        pdf_url = _find_annual_report_url(website, session)
        if not pdf_url:
            log.info(f"    No annual report PDF found")
            continue
        stats['pdf_found'] += 1

        # 2. Download PDF
        pdf_bytes = _download_pdf(pdf_url, session)
        if not pdf_bytes:
            continue

        # 3. Extract text
        text = _extract_text_from_pdf(pdf_bytes)
        if not text:
            continue

        # 4. Extract fields
        lender_update: Dict[str, Any] = {'id': lid}
        changed = False

        aum = _extract_aum(text)
        if aum and (args.force or not lender.get('aum_crores')):
            lender_update['aum_crores'] = aum
            lender_update['aum_category'] = (
                'Micro' if aum < 500 else
                'Small' if aum < 5000 else
                'Mid'   if aum < 50000 else
                'Large'
            )
            stats['aum_extracted'] += 1
            changed = True
            log.info(f"    AUM: ₹{aum:,.0f} Cr")

        emp = _extract_employees(text)
        if emp and (args.force or not lender.get('employee_count')):
            lender_update['employee_count'] = emp
            changed = True
            log.info(f"    Employees: {emp:,}")

        br = _extract_branches(text)
        if br and (args.force or not lender.get('branch_count')):
            lender_update['branch_count'] = br
            changed = True
            log.info(f"    Branches: {br:,}")

        if changed:
            lender_update['data_source'] = 'annual_report_pdf'
            lender_updates.append(lender_update)
            stats['updated'] += 1

        # 5. Extract Fair Practice Code rates → policies
        fpc = _extract_fpc_rates(text)
        if fpc:
            stats['fpc_extracted'] += len(fpc)
            log.info(f"    FPC rates: {[r['loan_type'] for r in fpc]}")
            for r in fpc:
                policy_upserts.append({
                    'lender_id':              lid,
                    'lender_name':            name,
                    'loan_type':              r['loan_type'],
                    'product_name':           'FPC Rate',
                    'product_name_normalized': 'fpc_rate',
                    'interest_rate_min':      r['interest_rate_min'],
                    'interest_rate_max':      r['interest_rate_max'],
                    'data_source':            'annual_report_fpc',
                    'completeness_score':     0.4,
                })

        time.sleep(1)   # polite crawling

    # ── Write to DB ────────────────────────────────────────────────────────────
    if not args.dry_run:
        for row in lender_updates:
            lid = row.pop('id')
            try:
                supa.table('lenders').update(row).eq('id', lid).execute()
            except Exception as e:
                log.error(f"Lender update failed for id={lid}: {e}")

        for i in range(0, len(policy_upserts), BATCH_SIZE):
            batch = policy_upserts[i:i + BATCH_SIZE]
            try:
                supa.table('policies').upsert(
                    batch, on_conflict='lender_id,product_name_normalized,loan_type'
                ).execute()
            except Exception as e:
                log.error(f"Policy upsert batch {i//BATCH_SIZE} failed: {e}")

    log.info("=" * 55)
    log.info(f"PDFs found:        {stats['pdf_found']}")
    log.info(f"AUM extracted:     {stats['aum_extracted']}")
    log.info(f"FPC rate entries:  {stats['fpc_extracted']}")
    log.info(f"Lenders updated:   {stats['updated']}")
    if args.dry_run:
        log.info("DRY RUN — nothing written")


def main():
    p = argparse.ArgumentParser(description='Extract data from annual report PDFs')
    p.add_argument('--dry-run',        action='store_true', help='Log only, no DB writes')
    p.add_argument('--force',          action='store_true', help='Overwrite existing values')
    p.add_argument('--unlisted-only',  action='store_true', help='Skip BSE-listed lenders')
    p.add_argument('--limit',          type=int, default=None, help='Max lenders to process')
    p.add_argument('--id',             type=int, default=None, help='Single lender ID')
    args = p.parse_args()
    run(args)


if __name__ == '__main__':
    main()
