"""
scrape_grievance_officers.py
============================
Scrapes Grievance Redressal Officer (GRO) contact details from every NBFC's website.

RBI mandates all NBFCs publish GRO contact info on their website. GRO details
appear in two places:
  1. Fair Practice Code (FPC) page — most reliable, already crawled by fpc scraper
  2. Dedicated grievance / contact pages

Strategy per lender:
  1. Try FPC page (same slugs as scrape_fpc_pages.py) — GRO usually appears there
  2. Try dedicated grievance URL patterns
  3. Try homepage link crawl for grievance-looking links
  4. Firecrawl fallback for JS-heavy sites

Fields extracted per lender:
  name          — "Mr. Rajesh Kumar" / "Rajesh Kumar" etc.
  designation   — "Grievance Redressal Officer" / "Nodal Officer" etc.
  email         — from proximity to "grievance" keyword
  phone         — Indian mobile/landline near grievance context
  source_url    — URL where info was found
  source_type   — 'fpc_page' | 'grievance_page' | 'contact_page' | 'homepage'

Usage:
    python backend/scrape_grievance_officers.py --stats
    python backend/scrape_grievance_officers.py --dry-run --limit 10
    python backend/scrape_grievance_officers.py --limit 100
    python backend/scrape_grievance_officers.py --lender-id 42
    python backend/scrape_grievance_officers.py --force
    python backend/scrape_grievance_officers.py --reset-checkpoint
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

_TIMEOUT       = 12
_RATE_DELAY    = 1.5
_MIN_PAGE_CHARS = 200

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-IN,en;q=0.9',
}

# ── URL slugs ─────────────────────────────────────────────────────────────────
# FPC slugs — GRO is mandated to appear on the FPC page
_FPC_SLUGS = [
    '/fpc',
    '/fair-practice-code',
    '/fair-practice',
    '/fair-practices-code',
    '/disclosure/fpc',
    '/disclosures/fair-practice-code',
    '/regulatory/fair-practice-code',
    '/compliance/fpc',
    '/policies/fair-practice-code',
]

# Dedicated grievance page slugs
_GRIEVANCE_SLUGS = [
    '/grievance',
    '/grievance-redressal',
    '/grievance-officer',
    '/grievance-redressal-officer',
    '/gro',
    '/grievance-redressal-mechanism',
    '/complaints',
    '/complaint-redressal',
    '/customer-grievance',
    '/investor-grievance',
    '/nodal-officer',
    '/rbi-ombudsman',
]

# Contact page slugs (lower priority — GRO may appear here)
_CONTACT_SLUGS = [
    '/contact-us',
    '/contact',
    '/about-us/contact',
]

# Link text / href keywords that identify grievance links
_GRIEVANCE_LINK_KEYWORDS = [
    'grievance', 'gro', 'nodal officer', 'complaint', 'redressal',
]


# ── Extraction patterns ───────────────────────────────────────────────────────

# Context window (chars) around "grievance" keyword for targeted extraction
_CTX_WINDOW = 600

# Designation patterns — ordered by specificity
_DESIGNATION_PATTERNS = [
    (r'Grievance\s+Redressal\s+Officer',    'Grievance Redressal Officer'),
    (r'Chief\s+Grievance\s+Officer',        'Chief Grievance Officer'),
    (r'Grievance\s+Officer',                'Grievance Officer'),
    (r'Nodal\s+(?:Grievance\s+)?Officer',   'Nodal Grievance Officer'),
    (r'Customer\s+(?:Grievance|Service)\s+Officer', 'Customer Grievance Officer'),
    (r'\bGRO\b',                            'Grievance Redressal Officer'),
]

_TITLE_PREFIX = r'(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri\.?|Smt\.?)?\s*'

# Name patterns — title-case AND all-caps (both common in Indian NBFC docs)
_NAME_PATTERNS = [
    # Title-case: "Officer: Mr. Firstname Lastname"
    r'(?:Grievance\s+(?:Redressal\s+)?Officer|Nodal\s+Officer|\bGRO\b)'
    r'[\s:–\-]+' + _TITLE_PREFIX +
    r'([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20}){1,4})',
    # All-caps: "GRIEVANCE OFFICER : RAJESH KUMAR SHARMA"
    r'(?:GRIEVANCE\s+(?:REDRESSAL\s+)?OFFICER|NODAL\s+OFFICER|GRO)'
    r'[\s:–\-]+' + _TITLE_PREFIX +
    r'([A-Z]{2,20}(?:\s+[A-Z]{2,20}){1,4})',
    # "Name: Mr. Firstname Lastname" after section header
    r'Name\s*[:\-]\s*' + _TITLE_PREFIX +
    r'([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20}){1,4})',
    # All-caps version of Name field
    r'Name\s*[:\-]\s*' + _TITLE_PREFIX +
    r'([A-Z]{2,20}(?:\s+[A-Z]{2,20}){1,4})',
]

# Email pattern — standard
_EMAIL_RE = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
)

# Preferred GRO email indicators (checked against local-part)
_EMAIL_GRO_SIGNALS = ('grievance', 'gro', 'nodal', 'complaint', 'redressal')

# Emails that are almost certainly not the GRO contact
_EMAIL_SKIP = ('noreply', 'no-reply', 'donotreply', 'webmaster',
               'careers', 'recruitment', 'press', 'media', 'vendor')

# Indian phone: +91 mobile, 10-digit mobile, or STD+7-digit landline
# Requires word boundary before to avoid matching inside longer digit strings
_PHONE_RE = re.compile(
    r'(?<!\d)'
    r'('
    r'(?:\+91|91)?[\s\-]?[6-9]\d{9}'           # mobile: starts 6-9, 10 digits
    r'|0\d{2,4}[\s\-]?\d{6,8}'                  # landline: 0STD + 6-8 digits
    r')'
    r'(?!\d)'
)


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
            "approval_status = 'approved'",
            "website IS NOT NULL",
            "website != ''",
        ]
        if not force:
            # only lenders that don't yet have a grievance officer record
            conditions.append("""
                id NOT IN (SELECT lender_id FROM grievance_officers)
            """)
        if lender_id:
            conditions.append(f'id = {lender_id}')
        where = ' AND '.join(conditions)
        sql = f"""
            SELECT id, company_name, website
            FROM lenders
            WHERE {where}
            ORDER BY id
            {"LIMIT " + str(limit) if limit else ""}
        """
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _db_execute(sql: str, params: list, commit: bool = True) -> bool:
    """Open a fresh connection, run one statement, close. Retries 3x on failure."""
    for attempt in range(3):
        conn = None
        try:
            conn = _get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, params)
            if commit:
                conn.commit()
            return True
        except Exception as exc:
            log.debug('DB execute attempt %d failed: %s', attempt + 1, exc)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return False


def _upsert_gro(lender_id: int, data: Dict[str, Any], dry_run: bool) -> bool:
    if dry_run:
        return False
    now = datetime.now(timezone.utc).isoformat()
    data['last_verified_at'] = now
    cols = list(data.keys())
    vals = list(data.values())
    placeholders = ', '.join('%s' for _ in vals)
    set_clause = ', '.join(f'{c} = EXCLUDED.{c}' for c in cols if c != 'lender_id')
    sql = f"""
        INSERT INTO grievance_officers (lender_id, {', '.join(cols)})
        VALUES (%s, {placeholders})
        ON CONFLICT (lender_id) DO UPDATE SET {set_clause}
    """
    ok = _db_execute(sql, [lender_id] + vals)
    if not ok:
        log.error('DB upsert failed after retries for lender %d', lender_id)
    return ok


def _save_checkpoint(lender_id: int) -> None:
    sql = """
        INSERT INTO scraper_checkpoints (job_name, done_ids, updated_at)
        VALUES ('scrape_grievance_officers', ARRAY[%s::bigint], NOW())
        ON CONFLICT (job_name) DO UPDATE
        SET done_ids   = array_append(
                COALESCE(scraper_checkpoints.done_ids, ARRAY[]::bigint[]),
                EXCLUDED.done_ids[1]
            ),
            updated_at = NOW()
    """
    if not _db_execute(sql, [lender_id]):
        log.warning('Checkpoint save failed after retries for lender %d — will retry next run', lender_id)


def _load_checkpoint(conn) -> set:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT done_ids FROM scraper_checkpoints WHERE job_name = 'scrape_grievance_officers'"
            )
            row = cur.fetchone()
            return set(row[0] or []) if row else set()
    except Exception:
        return set()


def _reset_checkpoint(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM scraper_checkpoints WHERE job_name = 'scrape_grievance_officers'"
        )
    conn.commit()
    log.info('Checkpoint cleared')


def _print_stats(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*)                                           AS total_lenders,
                (SELECT COUNT(*) FROM grievance_officers)         AS with_gro,
                (SELECT COUNT(*) FROM grievance_officers WHERE email IS NOT NULL) AS with_email,
                (SELECT COUNT(*) FROM grievance_officers WHERE phone IS NOT NULL) AS with_phone,
                (SELECT COUNT(*) FROM grievance_officers WHERE name  IS NOT NULL) AS with_name
            FROM lenders
            WHERE approval_status = 'approved'
        """)
        row = cur.fetchone()
    total, with_gro, with_email, with_phone, with_name = row
    pct = round(with_gro / total * 100, 1) if total else 0
    print()
    print('=== Grievance Officer Coverage ===')
    print(f'  Approved lenders        : {total:,}')
    print(f'  With GRO record         : {with_gro:,}  ({pct}%)')
    print(f'  With name               : {with_name:,}')
    print(f'  With email              : {with_email:,}')
    print(f'  With phone              : {with_phone:,}')
    print(f'  Missing GRO             : {total - with_gro:,}  <-- scraper targets these')
    print()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get_html(session: requests.Session, url: str,
              expected_domain: Optional[str] = None,
              _tracker: Optional[List] = None) -> Optional[str]:
    try:
        resp = session.get(url, timeout=_TIMEOUT, headers=_HEADERS, allow_redirects=True)
        if _tracker is not None:
            _tracker.append(True)
        if resp.status_code != 200:
            return None
        ct = resp.headers.get('content-type', '').lower()
        if 'html' not in ct:
            return None
        if expected_domain:
            actual = urlparse(resp.url).netloc.lstrip('www.')
            if actual != expected_domain.lstrip('www.'):
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


def _extract_grievance_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, 'html.parser')
    found: List[str] = []
    base_domain = urlparse(base_url).netloc
    for tag in soup.find_all('a', href=True):
        href = tag['href'].strip()
        link_text = tag.get_text(strip=True).lower()
        combined = (href + ' ' + link_text).lower()
        if any(kw in combined for kw in _GRIEVANCE_LINK_KEYWORDS):
            full = urljoin(base_url, href)
            if urlparse(full).netloc == base_domain:
                found.append(full)
    return found


def _normalise_url(website: str) -> str:
    url = website.strip().rstrip('/')
    if not url.startswith('http'):
        url = 'https://' + url
    return url


# ── GRO extraction ────────────────────────────────────────────────────────────

def _has_grievance_content(text: str) -> bool:
    tl = text.lower()
    return 'grievance' in tl or 'nodal officer' in tl or ' gro ' in tl


def _extract_context_windows(text: str) -> List[str]:
    """
    Extract up to 8 deduplicated context windows around grievance keyword mentions.
    Capped to prevent regex slowdown on pages that repeat 'grievance' dozens of times.
    """
    seen: set = set()
    windows: List[str] = []
    tl = text.lower()
    for kw in ('grievance', 'nodal officer', ' gro '):
        start = 0
        while len(windows) < 8:
            pos = tl.find(kw, start)
            if pos == -1:
                break
            w_start = max(0, pos - 200)
            w_end   = min(len(text), pos + _CTX_WINDOW)
            snippet = text[w_start:w_end]
            key = snippet[:60]  # deduplicate near-identical windows
            if key not in seen:
                seen.add(key)
                windows.append(snippet)
            start = pos + 1
    return windows


def _extract_gro(text: str) -> Dict[str, Optional[str]]:
    """
    Extract GRO fields from page text.
    Returns dict with keys: name, designation, email, phone (all Optional[str]).
    """
    result: Dict[str, Optional[str]] = {
        'name': None, 'designation': None, 'email': None, 'phone': None,
    }

    if not _has_grievance_content(text):
        return result

    windows = _extract_context_windows(text)
    combined_ctx = ' '.join(windows)

    # Designation — patterns are (regex, canonical_label) tuples
    for pat, label in _DESIGNATION_PATTERNS:
        if re.search(pat, combined_ctx, re.I):
            result['designation'] = label
            break

    # Name — try each pattern, validate result is a real name
    for pat in _NAME_PATTERNS:
        m = re.search(pat, combined_ctx)
        if m:
            name = m.group(1).strip()
            words = name.split()
            # 2–5 words, no digits, not a common false-positive keyword
            _fp = {'LIMITED', 'PRIVATE', 'FINANCE', 'CAPITAL', 'BANK', 'LOAN',
                   'NBFC', 'INDIA', 'FINANCIAL', 'SERVICES', 'OFFICER', 'REDRESSAL'}
            if (2 <= len(words) <= 5
                    and not any(c.isdigit() for c in name)
                    and not any(w.upper() in _fp for w in words)):
                result['name'] = name
                break

    # Email — prefer addresses that contain a GRO signal in the local-part,
    # fall back to any non-generic address found in context
    all_emails = _EMAIL_RE.findall(combined_ctx)
    preferred, fallback = None, None
    for email in all_emails:
        el = email.lower()
        if any(s in el for s in _EMAIL_SKIP):
            continue
        local = el.split('@')[0]
        if any(sig in local for sig in _EMAIL_GRO_SIGNALS):
            preferred = el
            break
        if fallback is None:
            fallback = el
    result['email'] = preferred or fallback

    # Phone — new regex captures group(1); validate and normalise
    for window in windows:
        for m in _PHONE_RE.finditer(window):
            raw = re.sub(r'[\s\-]', '', m.group(1))
            # Mobile: strip +91/91 prefix, must be 10 digits starting 6-9
            mobile = re.sub(r'^(?:\+91|91)', '', raw)
            if len(mobile) == 10 and mobile[0] in '6789':
                result['phone'] = mobile
                break
            # Landline: keep as-is (starts with 0, 8-12 chars total)
            if raw.startswith('0') and 8 <= len(raw) <= 12:
                result['phone'] = raw
                break
        if result['phone']:
            break

    return result


# ── Page finder ───────────────────────────────────────────────────────────────

def _find_gro_page(session: requests.Session,
                   base_url: str,
                   _http_tracker: Optional[List] = None,
                   ) -> Tuple[Optional[str], Optional[str], str]:
    """
    Returns (page_url, page_text, source_type) or (None, None, '').
    Tries FPC pages first (GRO is mandated there), then grievance slugs,
    then contact slugs, then homepage link crawl.
    """
    domain = urlparse(base_url).netloc

    def _try_slugs(slugs: List[str], stype: str):
        for slug in slugs:
            url = base_url + slug
            html = _get_html(session, url, expected_domain=domain, _tracker=_http_tracker)
            if html:
                text = _html_to_text(html)
                if _has_grievance_content(text) and len(text) >= _MIN_PAGE_CHARS:
                    log.info('    GRO page found via %s slug: %s', stype, url)
                    return url, text, stype
            time.sleep(0.2)
        return None, None, ''

    # 1. FPC pages
    result = _try_slugs(_FPC_SLUGS, 'fpc_page')
    if result[0]:
        return result

    # 2. Dedicated grievance pages
    result = _try_slugs(_GRIEVANCE_SLUGS, 'grievance_page')
    if result[0]:
        return result

    # 3. Contact pages (lower priority)
    result = _try_slugs(_CONTACT_SLUGS, 'contact_page')
    if result[0]:
        return result

    # 4. Homepage link crawl
    home_html = _get_html(session, base_url, expected_domain=domain, _tracker=_http_tracker)
    if home_html:
        links = _extract_grievance_links(home_html, base_url)
        for link in links[:5]:
            html = _get_html(session, link, expected_domain=domain, _tracker=_http_tracker)
            if html:
                text = _html_to_text(html)
                if _has_grievance_content(text) and len(text) >= _MIN_PAGE_CHARS:
                    log.info('    GRO page found via homepage link: %s', link)
                    return link, text, 'homepage'
            time.sleep(0.3)

    # 5. scrape.do fallback
    if SCRAPE_DO_KEY:
        for slug in ['/grievance', '/fpc', '/fair-practice-code']:
            url = base_url + slug
            text = _firecrawl_get(url)
            if text and _has_grievance_content(text):
                log.info('    GRO page found via scrape.do: %s', url)
                return url, text, 'website'
            time.sleep(0.5)

    return None, None, ''


# ── Main ──────────────────────────────────────────────────────────────────────

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
        log.info('Nothing to do.')
        conn.close()
        return

    done = _load_checkpoint(conn)
    session = requests.Session()

    stats = {
        'page_found':   0,
        'gro_found':    0,
        'no_page':      0,
        'no_gro':       0,
        'upserted':     0,
    }

    for lender in lenders:
        lid  = lender['id']
        name = lender['company_name']
        site = lender['website'] or ''

        if lid in done:
            log.info('[%d] %s - skipped (checkpoint)', lid, name)
            continue

        log.info('[%d] %s', lid, name)

        base_url = _normalise_url(site)
        http_tracker: List = []
        page_url, page_text, source_type = _find_gro_page(
            session, base_url, _http_tracker=http_tracker
        )

        if not page_url:
            stats['no_page'] += 1
            if http_tracker:
                log.info('    No grievance page found — checkpointing')
                _save_checkpoint(lid)
            else:
                log.warning('    No HTTP responses — network failure, skipping checkpoint')
            time.sleep(_RATE_DELAY)
            continue

        stats['page_found'] += 1

        gro = _extract_gro(page_text)
        has_any = any(v for v in gro.values())

        if not has_any:
            log.info('    Grievance page found but no GRO details extracted: %s', page_url)
            stats['no_gro'] += 1
            _save_checkpoint(lid)
            time.sleep(_RATE_DELAY)
            continue

        stats['gro_found'] += 1
        log.info(
            '    GRO: name=%s | email=%s | phone=%s | designation=%s',
            gro['name'] or '?',
            gro['email'] or '?',
            gro['phone'] or '?',
            gro['designation'] or '?',
        )

        db_data: Dict[str, Any] = {k: v for k, v in gro.items() if v is not None}
        db_data['source_url']  = page_url
        db_data['source_type'] = source_type

        ok = _upsert_gro(lid, db_data, args.dry_run)
        if ok:
            stats['upserted'] += 1

        # Never checkpoint on dry-run — would poison real runs by skipping these lenders
        if not args.dry_run:
            if ok:
                _save_checkpoint(lid)
            else:
                log.warning('    Upsert failed — not checkpointing lender %d, will retry next run', lid)
        time.sleep(_RATE_DELAY)

    try:
        conn.close()
    except Exception:
        pass

    print()
    print('=' * 55)
    print(f"Lenders processed        : {len(lenders)}")
    print(f"Grievance page found     : {stats['page_found']}")
    print(f"No grievance page        : {stats['no_page']}")
    print(f"GRO details extracted    : {stats['gro_found']}")
    print(f"Page found, no GRO       : {stats['no_gro']}")
    print(f"Records upserted         : {stats['upserted']}")
    if args.dry_run:
        print('DRY RUN — nothing written to DB')


def main():
    p = argparse.ArgumentParser(
        description='Scrape Grievance Redressal Officer contacts from NBFC websites'
    )
    p.add_argument('--dry-run',          action='store_true', help='No DB writes')
    p.add_argument('--force',            action='store_true', help='Re-scrape even if record exists')
    p.add_argument('--limit',            type=int,            help='Max lenders to process')
    p.add_argument('--lender-id',        type=int,            help='Single lender by DB id')
    p.add_argument('--stats',            action='store_true', help='Coverage report only')
    p.add_argument('--reset-checkpoint', action='store_true', help='Clear checkpoint and restart')
    run(p.parse_args())


if __name__ == '__main__':
    main()
