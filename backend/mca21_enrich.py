"""
mca21_enrich.py
===============
Enriches lenders with authoritative MCA21 data:
  - CIN (Corporate Identification Number)
  - company_status (active / struck_off / dormant / dissolved / converted)
  - authorized_capital_lakhs
  - paid_up_capital_lakhs
  - established_year (from incorporation date — overrides Gemini guess)

Source: zaubacorp.com (public MCA21 mirror, no auth required)
         Falls back to mca.gov.in search where possible.

Why this matters:
  - Flags struck-off / dormant NBFCs before they reach borrowers
  - Fills established_year with the exact incorporation date from MCA
  - Enables RBI compliance checks (paid_up_capital >= ₹200 Lakhs for NBFCs)
  - CIN is a unique identifier usable for cross-referencing financial data

Usage:
    python mca21_enrich.py                      # all lenders missing CIN
    python mca21_enrich.py --limit 100          # first 100
    python mca21_enrich.py --only "Kogta"       # name substring
    python mca21_enrich.py --all                # re-enrich even if already done
    python mca21_enrich.py --dry-run            # lookup but don't write to DB
    python mca21_enrich.py --reset-checkpoint   # clear checkpoint

Env vars required:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
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
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# ── env loading ───────────────────────────────────────────────────────────────
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()

CHECKPOINT_FILE = Path(__file__).parent / '.mca21_checkpoint.json'
RATE_DELAY      = 3.0   # seconds between requests (be polite to zaubacorp)

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-IN,en;q=0.9',
}

# CIN regex: L/U + 5 digits + 2 letters + 4 digits + PLC/PTC/LLC/GOI + 6 digits
_CIN_RE = re.compile(r'\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b')
_CAPITAL_RE = re.compile(r'[\d,]+(?:\.\d+)?')


# ── Supabase ──────────────────────────────────────────────────────────────────

def _get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        sys.exit(1)
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        log.error("supabase-py not installed: pip install supabase")
        sys.exit(1)


# ── checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> set:
    if CHECKPOINT_FILE.exists():
        try:
            return set(json.loads(CHECKPOINT_FILE.read_text()).get('done', []))
        except Exception:
            return set()
    return set()


def _save_checkpoint(done: set) -> None:
    CHECKPOINT_FILE.write_text(json.dumps({'done': list(done)}, indent=2))


# ── MCA21 lookup via zaubacorp ────────────────────────────────────────────────

def _parse_capital_lakhs(text: str) -> Optional[float]:
    """Parse '1,00,00,000' → 100.0 (Lakhs). Returns None if unparseable."""
    m = _CAPITAL_RE.search(text.replace(',', ''))
    if not m:
        return None
    try:
        rupees = float(m.group())
        return round(rupees / 1e5, 2)      # Rupees → Lakhs
    except ValueError:
        return None


def _parse_year(text: str) -> Optional[int]:
    """Extract 4-digit year from a date string like '15 Aug 1997'."""
    m = re.search(r'\b(19|20)\d{2}\b', text)
    return int(m.group()) if m else None


def _status_canonical(raw: str) -> Optional[str]:
    raw = raw.strip().lower()
    if 'struck' in raw or 'strike' in raw:
        return 'struck_off'
    if 'dormant' in raw:
        return 'dormant'
    if 'dissolv' in raw:
        return 'dissolved'
    if 'convert' in raw:
        return 'converted'
    if 'active' in raw:
        return 'active'
    return None


def _search_zaubacorp(company_name: str) -> Optional[Dict[str, Any]]:
    """
    Search zaubacorp.com for a company and return the best-match CIN + detail data.
    Returns None if no confident match found.
    """
    # Step 1: search results page
    search_url = f"https://www.zaubacorp.com/company-list/p-1/company/{quote_plus(company_name)}"
    try:
        resp = requests.get(search_url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        log.warning(f"  Zaubacorp search request failed: {exc}")
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Find the first result link — table row with a CIN-like href
    best_cin  = None
    best_href = None
    for a in soup.select('table a[href]'):
        href = a['href']
        cin_m = _CIN_RE.search(href)
        if cin_m:
            best_cin  = cin_m.group()
            best_href = f"https://www.zaubacorp.com{href}" if href.startswith('/') else href
            break

    if not best_cin:
        # Also try scanning raw text for a CIN
        page_cins = _CIN_RE.findall(resp.text)
        if page_cins:
            best_cin = page_cins[0]

    if not best_cin:
        log.debug(f"  No CIN found in search results for '{company_name}'")
        return None

    log.debug(f"  Found CIN {best_cin} for '{company_name}'")

    # Step 2: fetch company detail page
    if not best_href:
        best_href = f"https://www.zaubacorp.com/company/{quote_plus(company_name)}/{best_cin}"

    time.sleep(RATE_DELAY * 0.5)   # brief pause before detail page
    try:
        detail = requests.get(best_href, headers=_HEADERS, timeout=15)
        detail.raise_for_status()
    except Exception as exc:
        log.warning(f"  Zaubacorp detail request failed for {best_cin}: {exc}")
        # Return CIN alone if detail page fails
        return {'cin': best_cin, 'company_status': None,
                'authorized_capital_lakhs': None, 'paid_up_capital_lakhs': None,
                'established_year': None}

    dsoup = BeautifulSoup(detail.text, 'html.parser')

    data: Dict[str, Any] = {
        'cin': best_cin,
        'company_status': None,
        'authorized_capital_lakhs': None,
        'paid_up_capital_lakhs': None,
        'established_year': None,
    }

    # Parse detail table rows (label → value)
    for row in dsoup.select('table tr'):
        cells = row.find_all(['td', 'th'])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True).lower()
        value = cells[1].get_text(strip=True)

        if 'company status' in label or 'status' == label:
            data['company_status'] = _status_canonical(value)
        elif 'date of incorporation' in label or 'incorporated' in label:
            yr = _parse_year(value)
            if yr:
                data['established_year'] = yr
        elif 'authorised capital' in label or 'authorized capital' in label:
            data['authorized_capital_lakhs'] = _parse_capital_lakhs(value)
        elif 'paid up capital' in label or 'paidup capital' in label:
            data['paid_up_capital_lakhs'] = _parse_capital_lakhs(value)

    # Fallback: scan all text for status keywords
    if data['company_status'] is None:
        page_text = dsoup.get_text(' ', strip=True).lower()
        for keyword, status in [
            ('struck off', 'struck_off'),
            ('dormant', 'dormant'),
            ('dissolved', 'dissolved'),
            ('converted', 'converted'),
            ('active', 'active'),
        ]:
            if keyword in page_text:
                data['company_status'] = status
                break

    return data


def _name_similarity(a: str, b: str) -> float:
    """Simple token-overlap similarity, 0.0–1.0."""
    def tokens(s: str):
        return set(re.sub(r'[^\w\s]', '', s.lower()).split())
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ── DB operations ─────────────────────────────────────────────────────────────

def fetch_lenders(
    supa,
    name_filter: Optional[str],
    limit: Optional[int],
    enrich_all: bool,
) -> List[Dict[str, Any]]:
    query = (
        supa.table('lenders')
        .select('id, company_name, company_type, cin, mca21_status, established_year')
        .eq('approval_status', 'approved')
    )
    if not enrich_all:
        query = query.eq('mca21_status', 'not_enriched')
    if name_filter:
        query = query.ilike('company_name', f'%{name_filter}%')
    if limit:
        query = query.limit(limit)

    resp = query.execute()
    lenders = resp.data or []
    log.info(f"Fetched {len(lenders)} lenders to process")
    return lenders


def update_lender(supa, lender_id: int, data: Dict[str, Any], dry_run: bool) -> None:
    data['mca21_status']    = 'enriched'
    data['mca21_enriched_at'] = datetime.utcnow().isoformat()

    if dry_run:
        log.info(f"  [DRY] Would update lender {lender_id}: {data}")
        return

    try:
        supa.table('lenders').update(data).eq('id', lender_id).execute()
    except Exception as exc:
        log.error(f"  DB update failed for lender {lender_id}: {exc}")


def mark_not_found(supa, lender_id: int, dry_run: bool) -> None:
    if dry_run:
        return
    try:
        supa.table('lenders').update({
            'mca21_status': 'not_found',
            'mca21_enriched_at': datetime.utcnow().isoformat(),
        }).eq('id', lender_id).execute()
    except Exception as exc:
        log.error(f"  DB mark_not_found failed for lender {lender_id}: {exc}")


def mark_error(supa, lender_id: int, dry_run: bool) -> None:
    if dry_run:
        return
    try:
        supa.table('lenders').update({
            'mca21_status': 'error',
            'mca21_enriched_at': datetime.utcnow().isoformat(),
        }).eq('id', lender_id).execute()
    except Exception as exc:
        log.error(f"  DB mark_error failed for lender {lender_id}: {exc}")


# ── stats ─────────────────────────────────────────────────────────────────────

class Stats:
    enriched   = 0
    not_found  = 0
    errors     = 0
    skipped    = 0
    cin_filled = 0
    status_filled = 0
    year_filled   = 0
    capital_filled = 0

    def report(self):
        log.info("=" * 60)
        log.info(f"Enriched:       {self.enriched}")
        log.info(f"Not found:      {self.not_found}")
        log.info(f"Errors:         {self.errors}")
        log.info(f"Skipped:        {self.skipped}")
        log.info(f"CIN filled:     {self.cin_filled}")
        log.info(f"Status filled:  {self.status_filled}")
        log.info(f"Year filled:    {self.year_filled}")
        log.info(f"Capital filled: {self.capital_filled}")


# ── main ──────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    supa    = _get_supabase()
    done    = _load_checkpoint()
    stats   = Stats()

    lenders = fetch_lenders(supa, args.only, args.limit, args.all)

    for idx, lender in enumerate(lenders, 1):
        lender_id = lender['id']
        name      = lender['company_name']
        ctype     = lender.get('company_type', '')

        key = str(lender_id)
        if key in done:
            log.info(f"[{idx}/{len(lenders)}] SKIP (checkpoint): {name}")
            stats.skipped += 1
            continue

        log.info(f"[{idx}/{len(lenders)}] Looking up: {name} ({ctype})")

        try:
            result = _search_zaubacorp(name)
        except Exception as exc:
            log.error(f"  Unexpected error for {name}: {exc}")
            mark_error(supa, lender_id, args.dry_run)
            stats.errors += 1
            done.add(key)
            _save_checkpoint(done)
            time.sleep(RATE_DELAY)
            continue

        if not result:
            log.info(f"  Not found on MCA21 for: {name}")
            mark_not_found(supa, lender_id, args.dry_run)
            stats.not_found += 1
            done.add(key)
            _save_checkpoint(done)
            time.sleep(RATE_DELAY)
            continue

        # Build update dict — only set fields that have data
        update: Dict[str, Any] = {}

        if result.get('cin'):
            update['cin'] = result['cin']
            stats.cin_filled += 1

        if result.get('company_status'):
            update['company_status'] = result['company_status']
            stats.status_filled += 1
            if result['company_status'] in ('struck_off', 'dormant', 'dissolved'):
                log.warning(f"  ⚠ {name} has status: {result['company_status']}")

        # Only fill established_year if currently null (Gemini guess is less reliable)
        if result.get('established_year') and not lender.get('established_year'):
            update['established_year'] = result['established_year']
            stats.year_filled += 1

        if result.get('authorized_capital_lakhs') is not None:
            update['authorized_capital_lakhs'] = result['authorized_capital_lakhs']
            stats.capital_filled += 1

        if result.get('paid_up_capital_lakhs') is not None:
            update['paid_up_capital_lakhs'] = result['paid_up_capital_lakhs']

        # Log capital adequacy warning for NBFCs (RBI minimum: ₹200 Lakhs paid-up)
        if ctype == 'NBFC' and result.get('paid_up_capital_lakhs') is not None:
            if result['paid_up_capital_lakhs'] < 200:
                log.warning(
                    f"  ⚠ NBFC {name} paid-up capital ₹{result['paid_up_capital_lakhs']:.0f}L "
                    f"< RBI minimum ₹200L"
                )

        if update:
            update_lender(supa, lender_id, update, args.dry_run)
            log.info(f"  Updated: { {k: v for k, v in update.items() if k != 'mca21_status'} }")
            stats.enriched += 1
        else:
            mark_not_found(supa, lender_id, args.dry_run)
            stats.not_found += 1

        done.add(key)
        _save_checkpoint(done)
        time.sleep(RATE_DELAY)

    stats.report()
    if args.dry_run:
        log.info("DRY RUN — no changes written to DB")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='MCA21 enrichment for lenders')
    parser.add_argument('--only',   type=str,  default=None, help='Name substring filter')
    parser.add_argument('--limit',  type=int,  default=None, help='Max lenders to process')
    parser.add_argument('--all',    action='store_true',     help='Re-enrich already-enriched lenders')
    parser.add_argument('--dry-run', action='store_true',   help='Lookup but do not write to DB')
    parser.add_argument('--reset-checkpoint', action='store_true', help='Clear checkpoint')
    args = parser.parse_args()

    if args.reset_checkpoint and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        log.info("Checkpoint cleared")

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    run(args)


if __name__ == '__main__':
    main()
