"""
scrape_fpc_pages.py
===================
Targets the Fair Practice Code (FPC) page on every NBFC's website.
RBI mandates all NBFCs publish an FPC that legally must include
interest rate ranges — more reliable than generic homepage text.

Strategy per lender:
  1. Try common FPC URL patterns (fast, no JS needed)
  2. If none hit, parse homepage for FPC-looking links
  3. If FIRECRAWL_API_KEY set, Firecrawl fallback for JS-heavy sites
  4. Extract ALL rate mentions from FPC text, pick best match per loan type
  5. Update policies that are missing interest_rate_min

Fields updated on matching policies:
  interest_rate_min, interest_rate_max — from FPC page
  rate_type                            — fixed/floating/mclr_linked/repo_linked
  rate_flagged                         — True if rate outside loan-type bounds
  source_url                           — the FPC URL used
  data_source                          — 'fpc_page'
  last_verified_at                     — now

Usage:
    python backend/scrape_fpc_pages.py --dry-run
    python backend/scrape_fpc_pages.py --limit 50
    python backend/scrape_fpc_pages.py --lender-id 123
    python backend/scrape_fpc_pages.py --force
    python backend/scrape_fpc_pages.py --stats
    python backend/scrape_fpc_pages.py --reset-checkpoint
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
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
                    _k = _k.strip()
                    _v = _rest.strip().strip('"').strip("'")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

DATABASE_URL  = os.environ.get('DATABASE_URL', '')
SCRAPE_DO_KEY = os.environ.get('SCRAPE_DO_API_KEY', '')

_TIMEOUT         = 12
_RATE_DELAY      = 1.5
_RATE_HARD_MAX   = 42.0
_RATE_FLAG_ABOVE = 36.0
_MIN_PAGE_CHARS  = 300   # ignore pages shorter than this after stripping HTML

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-IN,en;q=0.9',
}

# ── FPC URL slugs ─────────────────────────────────────────────────────────────
# High-confidence: these paths almost always contain FPC content if they exist.
_FPC_SLUGS_SPECIFIC = [
    '/fpc',
    '/fair-practice-code',
    '/fair-practice',
    '/fair-practices',
    '/fair-practices-code',
    '/fair_practice_code',
    '/fair_practice',
    '/disclosure/fpc',
    '/disclosures/fair-practice-code',
    '/disclosures/fpc',
    '/about/fair-practice-code',
    '/about-us/fair-practice-code',
    '/regulatory/fair-practice-code',
    '/compliance/fpc',
    '/compliance/fair-practice-code',
    '/investor-relations/fair-practice-code',
    '/policies/fair-practice-code',
]

# Low-confidence: these paths exist for other reasons (privacy policy, general
# disclosures). Only accept them if "fair practice" appears explicitly in the text.
_FPC_SLUGS_BROAD = [
    '/regulatory-disclosures',
    '/regulatory-disclosure',
    '/rbi-disclosures',
    '/rbi-guidelines',
    '/policies',
    '/disclosures',
]

# Link text / href keywords that identify FPC links on a homepage
_FPC_LINK_KEYWORDS = [
    'fair practice', 'fair-practice', 'fpc',
    'rbi disclosure', 'rbi guideline',
]

# ── Loan-type rate bounds (realistic RBI-aligned ranges) ─────────────────────
# Outside these bounds → rate_flagged=True (NOT hard rejected — may still be valid)
_LOAN_TYPE_BOUNDS: Dict[str, Tuple[float, float]] = {
    'Home Loan':             (6.5,  15.0),
    'Loan Against Property': (8.0,  22.0),
    'MSME Loan':             (10.0, 36.0),
    'Business Loan':         (10.0, 36.0),
    'Personal Loan':         (10.0, 36.0),
    'Gold Loan':             (7.0,  26.0),
    'Vehicle Loan':          (7.0,  22.0),
    'Education Loan':        (7.0,  15.0),
    'Microfinance':          (18.0, 36.0),
    'Agricultural Loan':     (7.0,  14.0),
}

# Keywords that help us find loan-type-specific sections on an FPC page
_LOAN_TYPE_KEYWORDS: Dict[str, List[str]] = {
    'Home Loan':             ['home loan', 'housing loan', 'home finance'],
    'Loan Against Property': ['loan against property', 'lap', 'mortgage loan'],
    'MSME Loan':             ['msme', 'sme loan', 'small enterprise', 'medium enterprise'],
    'Business Loan':         ['business loan', 'working capital', 'term loan'],
    'Personal Loan':         ['personal loan', 'consumer loan'],
    'Gold Loan':             ['gold loan', 'loan against gold'],
    'Vehicle Loan':          ['vehicle loan', 'car loan', 'two wheeler', 'auto loan'],
    'Education Loan':        ['education loan', 'student loan'],
    'Microfinance':          ['microfinance', 'micro loan', 'jlg', 'joint liability'],
    'Agricultural Loan':     ['agriculture', 'kisan', 'farm loan'],
}


# ── DB ────────────────────────────────────────────────────────────────────────

def _get_conn():
    if not DATABASE_URL:
        log.error('DATABASE_URL not set in .env')
        sys.exit(1)
    try:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    except ImportError:
        log.error('psycopg2 not installed — pip install psycopg2-binary')
        sys.exit(1)


def _fetch_lenders(conn, force: bool, lender_id: Optional[int],
                   limit: Optional[int]) -> List[Dict]:
    with conn.cursor() as cur:
        conditions = [
            "l.approval_status = 'approved'",
            "l.website IS NOT NULL",
            "l.website != ''",
        ]
        if not force:
            conditions.append("""
                l.id IN (
                    SELECT DISTINCT lender_id FROM policies
                    WHERE interest_rate_min IS NULL AND approval_status = 'approved'
                )
            """)
        if lender_id:
            conditions.append(f'l.id = {lender_id}')
        where = ' AND '.join(conditions)
        sql = f"""
            SELECT l.id, l.company_name, l.website,
                   COUNT(p.id) AS policy_count,
                   COUNT(p.id) FILTER (WHERE p.interest_rate_min IS NULL) AS missing_rate_count
            FROM lenders l
            LEFT JOIN policies p ON p.lender_id = l.id AND p.approval_status = 'approved'
            WHERE {where}
            GROUP BY l.id, l.company_name, l.website
            ORDER BY missing_rate_count DESC, l.id
            {"LIMIT " + str(limit) if limit else ""}
        """
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_policies_for_lender(conn, lender_id: int, force: bool) -> List[Dict]:
    with conn.cursor() as cur:
        cond = 'lender_id = %s AND approval_status = %s'
        params: list = [lender_id, 'approved']
        if not force:
            cond += ' AND interest_rate_min IS NULL'
        cur.execute(
            'SELECT id, loan_type, product_name FROM policies WHERE ' + cond,
            params,
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _update_policy(conn, pid: int, rate_min: float, rate_max: Optional[float],
                   rate_type: Optional[str], rate_flagged: bool,
                   source_url: str, dry_run: bool) -> bool:
    if dry_run:
        return False
    now = datetime.now(timezone.utc).isoformat()
    fields: Dict[str, Any] = {
        'interest_rate_min': rate_min,
        'data_source':       'fpc_page',
        'source_url':        source_url,
        'last_verified_at':  now,
        'rate_flagged':      rate_flagged,
    }
    if rate_max is not None:
        fields['interest_rate_max'] = rate_max
    if rate_type:
        fields['rate_type'] = rate_type
    set_sql = ', '.join(f'{k} = %s' for k in fields)
    vals = list(fields.values()) + [pid]
    with conn.cursor() as cur:
        cur.execute(f'UPDATE policies SET {set_sql} WHERE id = %s', vals)
    conn.commit()
    return True


def _save_checkpoint(conn, lender_id: int) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO scraper_checkpoints (job_name, done_ids, updated_at)
                VALUES ('scrape_fpc_pages', ARRAY[%s::bigint], NOW())
                ON CONFLICT (job_name) DO UPDATE
                SET done_ids   = array_append(
                        COALESCE(scraper_checkpoints.done_ids, ARRAY[]::bigint[]),
                        EXCLUDED.done_ids[1]
                    ),
                    updated_at = NOW()
            """, [lender_id])
        conn.commit()
    except Exception as exc:
        log.debug('Checkpoint save failed: %s', exc)
        conn.rollback()


def _load_checkpoint(conn) -> set:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT done_ids FROM scraper_checkpoints WHERE job_name = 'scrape_fpc_pages'"
            )
            row = cur.fetchone()
            return set(row[0] or []) if row else set()
    except Exception:
        return set()


def _reset_checkpoint(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM scraper_checkpoints WHERE job_name = 'scrape_fpc_pages'"
        )
    conn.commit()
    log.info('Checkpoint cleared')


# ── FPC page detection ────────────────────────────────────────────────────────

def _is_fpc_page(text: str, require_fair_practice: bool = True) -> bool:
    """
    Validates that a stripped page text is actually an FPC page.
    Hard requirement: must say 'fair practice' (not just have rate keywords).
    Soft requirement: at least 2 supporting signals.
    """
    if len(text) < _MIN_PAGE_CHARS:
        return False
    tl = text.lower()
    # Hard gate: "fair practice" must appear explicitly — this is what distinguishes
    # an FPC page from any random loan product page that also mentions rates and fees.
    if require_fair_practice:
        if 'fair practice' not in tl and 'fair-practice' not in tl:
            return False
    # Soft signals confirming it has rate content worth extracting
    soft = ['interest rate', 'rate of interest', 'processing fee', 'prepayment']
    return sum(1 for s in soft if s in tl) >= 2


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _get_html(session: requests.Session, url: str,
              expected_domain: Optional[str] = None,
              _tracker: Optional[List] = None) -> Optional[str]:
    """
    Fetch a URL and return its text content.
    Rejects: non-200, PDFs and other non-HTML, off-domain redirects, tiny pages.
    _tracker: if provided, a True is appended whenever any HTTP response arrives
              (even a rejected one), so callers can detect live connectivity.
    """
    try:
        resp = session.get(url, timeout=_TIMEOUT, headers=_HEADERS, allow_redirects=True)
        if _tracker is not None:
            _tracker.append(True)  # got a response — network is up
        if resp.status_code != 200:
            return None
        ct = resp.headers.get('content-type', '').lower()
        if 'html' not in ct:
            log.debug('Non-HTML at %s (%s)', url, ct)
            return None
        # Don't accept pages served from a different domain (off-site redirect)
        if expected_domain:
            actual = urlparse(resp.url).netloc.lstrip('www.')
            if actual != expected_domain.lstrip('www.'):
                log.debug('Off-domain redirect: %s → %s', url, resp.url)
                return None
        if len(resp.text) < 500:
            return None
        return resp.text
    except Exception:
        return None


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header',
                     'noscript', 'iframe', 'form']):
        tag.decompose()
    return ' '.join(soup.get_text(separator=' ').split())


def _firecrawl_get(url: str) -> Optional[str]:
    if not SCRAPE_DO_KEY:
        return None
    try:
        resp = requests.get(
            'https://api.scrape.do',
            params={'token': SCRAPE_DO_KEY, 'url': url, 'render': 'true'},
            timeout=30,
        )
        if resp.status_code == 200 and len(resp.text) > 500:
            return _html_to_text(resp.text)
    except Exception as exc:
        log.debug('scrape.do failed for %s: %s', url, exc)
    return None


def _extract_fpc_links_from_html(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, 'html.parser')
    found: List[str] = []
    base_domain = urlparse(base_url).netloc
    for tag in soup.find_all('a', href=True):
        href = tag['href'].strip()
        link_text = tag.get_text(strip=True).lower()
        combined = (href + ' ' + link_text).lower()
        if any(kw in combined for kw in _FPC_LINK_KEYWORDS):
            full = urljoin(base_url, href)
            if urlparse(full).netloc == base_domain:
                found.append(full)
    return found


def _normalise_url(website: str) -> str:
    url = website.strip().rstrip('/')
    if not url.startswith('http'):
        url = 'https://' + url
    return url


# ── FPC page finder ───────────────────────────────────────────────────────────

def _find_fpc_page(session: requests.Session,
                   base_url: str,
                   _http_tracker: Optional[List] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (fpc_url, stripped_text) or (None, None).
    _http_tracker: caller passes an empty list; True is appended for every HTTP
    response received (even 404/non-HTML). Lets caller distinguish genuine
    "no FPC" from silent network failure.
    """
    domain = urlparse(base_url).netloc

    # 1. High-confidence slugs — require "fair practice" in text
    for slug in _FPC_SLUGS_SPECIFIC:
        url = base_url + slug
        html = _get_html(session, url, expected_domain=domain, _tracker=_http_tracker)
        if html:
            text = _html_to_text(html)
            if _is_fpc_page(text, require_fair_practice=True):
                log.info('    FPC found via slug: %s', url)
                return url, text
        time.sleep(0.2)

    # 2. Low-confidence slugs — still require "fair practice" (no relaxing here)
    for slug in _FPC_SLUGS_BROAD:
        url = base_url + slug
        html = _get_html(session, url, expected_domain=domain, _tracker=_http_tracker)
        if html:
            text = _html_to_text(html)
            if _is_fpc_page(text, require_fair_practice=True):
                log.info('    FPC found via broad slug: %s', url)
                return url, text
        time.sleep(0.2)

    # 3. Crawl homepage for links mentioning "fair practice"
    home_html = _get_html(session, base_url, expected_domain=domain, _tracker=_http_tracker)
    if home_html:
        fpc_links = _extract_fpc_links_from_html(home_html, base_url)
        for link in fpc_links[:5]:
            html = _get_html(session, link, expected_domain=domain, _tracker=_http_tracker)
            if html:
                text = _html_to_text(html)
                if _is_fpc_page(text, require_fair_practice=True):
                    log.info('    FPC found via homepage link: %s', link)
                    return link, text
            time.sleep(0.3)

    # 4. scrape.do fallback for JS-rendered sites
    if SCRAPE_DO_KEY:
        for slug in ['/fpc', '/fair-practice-code', '/fair-practice']:
            url = base_url + slug
            text = _firecrawl_get(url)
            if text and _is_fpc_page(text, require_fair_practice=True):
                log.info('    FPC found via scrape.do: %s', url)
                return url, text
            time.sleep(0.5)

    return None, None


# ── Rate extraction ───────────────────────────────────────────────────────────

def _extract_all_rates(text: str) -> List[Tuple[float, Optional[float], str]]:
    """
    Extract every rate mention from the text.
    Returns list of (rate_min, rate_max, context_snippet) sorted by confidence.
    context_snippet = 80 chars around the match (for loan-type matching later).
    """
    results: List[Tuple[float, Optional[float], str]] = []
    seen: set = set()

    patterns = [
        # Range: "X% to Y%" or "X% – Y%"
        r'(\d+(?:\.\d+)?)\s*%\s*(?:to|-|–|and)\s*(\d+(?:\.\d+)?)\s*%',
        # Starting from / minimum: "from X%"
        r'(?:starting|from|as low as|minimum|min\.?|w\.?e\.?f\.?)\s+(\d+(?:\.\d+)?)\s*%',
        # Single rate with p.a.: "X% p.a."
        r'(\d+(?:\.\d+)?)\s*%\s*p\.?a\.?(?:\.|per\s*annum)?',
    ]

    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            g = m.groups()
            lo: Optional[float] = None
            hi: Optional[float] = None

            if len(g) >= 2 and g[1] is not None:
                try:
                    lo, hi = float(g[0]), float(g[1])
                except ValueError:
                    continue
                if not (5 <= lo <= _RATE_HARD_MAX and 5 <= hi <= _RATE_HARD_MAX):
                    continue
                lo, hi = min(lo, hi), max(lo, hi)
            elif g[0] is not None:
                try:
                    lo = float(g[0])
                except ValueError:
                    continue
                if not (5 <= lo <= _RATE_HARD_MAX):
                    continue

            if lo is None:
                continue

            key = (round(lo, 1), round(hi, 1) if hi is not None else None)
            if key in seen:
                continue
            seen.add(key)

            start = max(0, m.start() - 80)
            end   = min(len(text), m.end() + 80)
            ctx   = ' '.join(text[start:end].split())
            results.append((lo, hi, ctx))

    return results


def _best_rate_for_loan_type(
    all_rates: List[Tuple[float, Optional[float], str]],
    loan_type: str,
) -> Tuple[Optional[float], Optional[float], str, bool]:
    """
    Pick the best rate for a given loan type.
    Priority:
      1. A rate whose surrounding context mentions this loan type's keywords
      2. A rate within the loan-type-specific plausibility bounds
      3. First rate found (general FPC rate for this lender)

    Returns (rate_min, rate_max, context, should_flag).
    should_flag = True when rate is outside loan-type bounds (still stored, but flagged).
    """
    if not all_rates:
        return None, None, '', False

    keywords = _LOAN_TYPE_KEYWORDS.get(loan_type, [])
    lo_bound, hi_bound = _LOAN_TYPE_BOUNDS.get(loan_type, (5.0, _RATE_HARD_MAX))

    # Priority 1: context mentions the loan type
    for lo, hi, ctx in all_rates:
        ctx_lower = ctx.lower()
        if any(kw in ctx_lower for kw in keywords):
            flagged = bool(lo > _RATE_FLAG_ABOVE or (lo < lo_bound - 2.0) or (lo > hi_bound + 2.0))
            return lo, hi, ctx, flagged

    # Priority 2: rate within loan-type bounds
    for lo, hi, ctx in all_rates:
        if lo_bound <= lo <= hi_bound:
            return lo, hi, ctx, False

    # Priority 3: fall back to first rate, flag if outside bounds
    lo, hi, ctx = all_rates[0]
    flagged = bool(lo > _RATE_FLAG_ABOVE or lo < lo_bound - 2.0 or lo > hi_bound + 2.0)
    return lo, hi, ctx, flagged


def _detect_rate_type(text: str) -> Optional[str]:
    tl = text.lower()
    if any(k in tl for k in ('fixed rate', 'fixed interest', 'at fixed')):
        return 'fixed'
    if any(k in tl for k in ('floating rate', 'variable rate', 'floating interest')):
        return 'floating'
    if 'mclr' in tl:
        return 'mclr_linked'
    if 'repo' in tl and 'linked' in tl:
        return 'repo_linked'
    return None


# ── Stats ─────────────────────────────────────────────────────────────────────

def _print_stats(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*)                                                 AS total_policies,
                COUNT(*) FILTER (WHERE interest_rate_min IS NOT NULL)   AS with_rate,
                COUNT(*) FILTER (WHERE interest_rate_min IS NULL)       AS missing_rate,
                COUNT(DISTINCT lender_id)                               AS total_lenders,
                COUNT(DISTINCT lender_id)
                    FILTER (WHERE interest_rate_min IS NOT NULL)        AS lenders_with_rate,
                COUNT(DISTINCT lender_id)
                    FILTER (WHERE interest_rate_min IS NULL)            AS lenders_missing_rate,
                COUNT(*) FILTER (WHERE data_source = 'fpc_page')       AS from_fpc,
                COUNT(*) FILTER (WHERE rate_flagged = TRUE)            AS flagged
            FROM policies
            WHERE approval_status = 'approved'
        """)
        row = cur.fetchone()
    pct = round(row[1] / row[0] * 100, 1) if row[0] else 0
    print()
    print('=== Interest Rate Coverage (approved policies) ===')
    print(f'  Total policies          : {row[0]:,}')
    print(f'  With interest rate      : {row[1]:,}  ({pct}%)')
    print(f'  Missing interest rate   : {row[2]:,}')
    print(f'  Total lenders           : {row[3]:,}')
    print(f'  Lenders with rate       : {row[4]:,}')
    print(f'  Lenders missing rate    : {row[5]:,}  <-- FPC scraper targets these')
    print(f'  Rates from FPC pages    : {row[6]:,}')
    print(f'  Flagged rates           : {row[7]:,}')
    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    conn = _get_conn()

    if args.reset_checkpoint:
        _reset_checkpoint(conn)

    if args.stats:
        _print_stats(conn)
        conn.close()
        return

    lenders = _fetch_lenders(conn, args.force, args.lender_id, args.limit)
    log.info('Lenders to process: %d', len(lenders))

    if not lenders:
        log.info('Nothing to do - all policies have rates or no websites found.')
        conn.close()
        return

    done = _load_checkpoint(conn)
    session = requests.Session()

    stats = {
        'fpc_found':        0,
        'rate_found':       0,
        'no_fpc':           0,
        'no_rate':          0,
        'policies_updated': 0,
        'policies_flagged': 0,
    }

    for lender in lenders:
        lid     = lender['id']
        name    = lender['company_name']
        site    = lender['website'] or ''
        missing = lender.get('missing_rate_count', 0)

        if lid in done:
            log.info('[%d] %s - skipped (checkpoint)', lid, name)
            continue

        log.info('[%d] %s  (%d policies missing rate)', lid, name, missing)

        base_url = _normalise_url(site)
        http_tracker: List = []
        fpc_url, fpc_text = _find_fpc_page(session, base_url, _http_tracker=http_tracker)

        if not fpc_url:
            stats['no_fpc'] += 1
            if http_tracker:
                log.info('    No FPC page found — checkpointing')
                _save_checkpoint(conn, lid)
            else:
                log.warning('    No HTTP responses received — network failure, skipping checkpoint')
            time.sleep(_RATE_DELAY)
            continue

        stats['fpc_found'] += 1

        all_rates = _extract_all_rates(fpc_text)
        if not all_rates:
            log.info('    FPC found but no rate extracted: %s', fpc_url)
            stats['no_rate'] += 1
            _save_checkpoint(conn, lid)
            time.sleep(_RATE_DELAY)
            continue

        stats['rate_found'] += 1
        rate_type = _detect_rate_type(fpc_text)

        # Per-policy rate selection - each loan type gets the most relevant rate
        policies = _fetch_policies_for_lender(conn, lid, args.force)
        updated_count = 0
        for policy in policies:
            pid       = policy['id']
            loan_type = policy.get('loan_type') or ''

            lo, hi, ctx, flagged = _best_rate_for_loan_type(all_rates, loan_type)
            if lo is None:
                continue

            # Hard reject: above RBI maximum
            if lo > _RATE_HARD_MAX:
                log.info('    [policy %d] Rate %.1f%% exceeds hard max - skipped', pid, lo)
                continue

            # Inherit flag from RBI flag-above threshold
            if lo > _RATE_FLAG_ABOVE:
                flagged = True

            rate_label = f'{lo}% - {hi}%' if hi else f'{lo}%'
            flag_note  = '  [FLAGGED]' if flagged else ''
            log.info('    [policy %d | %s] Rate: %s%s', pid, loan_type or 'unknown', rate_label, flag_note)
            if flagged:
                log.info('        context: ...%s...', ctx[:120])

            ok = _update_policy(conn, pid, lo, hi, rate_type, flagged, fpc_url, args.dry_run)
            if ok:
                updated_count += 1
                if flagged:
                    stats['policies_flagged'] += 1

        stats['policies_updated'] += updated_count
        log.info('    Updated %d policies', updated_count)

        _save_checkpoint(conn, lid)
        time.sleep(_RATE_DELAY)

    conn.close()

    print()
    print('=' * 55)
    print(f"Lenders processed        : {len(lenders)}")
    print(f"FPC page found           : {stats['fpc_found']}")
    print(f"No FPC page found        : {stats['no_fpc']}")
    print(f"Rates extracted          : {stats['rate_found']}")
    print(f"FPC found, no rate       : {stats['no_rate']}")
    print(f"Policies updated         : {stats['policies_updated']}")
    print(f"Policies flagged         : {stats['policies_flagged']}")
    if args.dry_run:
        print('DRY RUN — nothing written to DB')


def main():
    p = argparse.ArgumentParser(
        description='Scrape Fair Practice Code pages for interest rates'
    )
    p.add_argument('--dry-run',          action='store_true', help='No DB writes')
    p.add_argument('--force',            action='store_true', help='Re-scrape even if rate exists')
    p.add_argument('--limit',            type=int,            help='Max lenders to process')
    p.add_argument('--lender-id',        type=int,            help='Single lender by DB id')
    p.add_argument('--stats',            action='store_true', help='Coverage report only')
    p.add_argument('--reset-checkpoint', action='store_true', help='Clear checkpoint and restart')
    run(p.parse_args())


if __name__ == '__main__':
    main()
