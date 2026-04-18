"""
fetch_bse_financials.py
========================
Enriches listed lenders with financial data from BSE public API.

For each BSE-listed lender:
  1. Matches company name → BSE scrip code via BSE search API
  2. Fetches latest annual financial result (JSON)
  3. Extracts: AUM (Net Advances), employee_count (if available)
  4. Upserts into lenders table

No API key required — BSE API is public.

Fields updated:
  aum_crores       — Net Advances from balance sheet (best AUM proxy)
  aum_category     — derived from aum_crores
  data_source      — 'bse_financial_result'

Usage:
    python fetch_bse_financials.py --dry-run          # preview
    python fetch_bse_financials.py --limit 20         # first N
    python fetch_bse_financials.py --force            # overwrite existing
    python fetch_bse_financials.py --id 42            # single lender
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from difflib import SequenceMatcher

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

CHECKPOINT_FILE = Path(__file__).parent / '.fetch_bse_checkpoint.json'

# ── BSE API endpoints ─────────────────────────────────────────────────────────
BSE_SEARCH_URL    = 'https://api.bseindia.com/BseIndiaAPI/api/GetScripsOnSearch/w'
BSE_FINANCIALS_URL = 'https://api.bseindia.com/BseIndiaAPI/api/FinancialResultNew/w'
BSE_COMPANY_URL   = 'https://api.bseindia.com/BseIndiaAPI/api/CompanyProfile/w'

HEADERS = {
    'User-Agent':  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer':     'https://www.bseindia.com/',
    'Accept':      'application/json, text/plain, */*',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _parse_crores(val) -> Optional[float]:
    """Parse BSE financial value (in Lakhs) → Crores."""
    try:
        lakhs = float(str(val).replace(',', ''))
        return round(lakhs / 100, 2)  # BSE reports in Lakhs
    except (ValueError, TypeError):
        return None


def _aum_category(crores: float) -> str:
    if crores < 500:    return 'Micro'
    if crores < 5000:   return 'Small'
    if crores < 50000:  return 'Mid'
    return 'Large'


# ── BSE API calls ─────────────────────────────────────────────────────────────

def _search_scrip(company_name: str, session) -> Optional[str]:
    """Return BSE scrip code for a company name, or None."""
    # Try progressively shorter search terms
    terms = [company_name]
    # Add shortened version: "HDFC Bank Limited" → "HDFC Bank"
    words = company_name.split()
    if len(words) > 2:
        terms.append(' '.join(words[:3]))
    if len(words) > 1:
        terms.append(words[0])

    for term in terms:
        try:
            r = session.get(
                BSE_SEARCH_URL,
                params={'strSearchText': term, 'LeadingCharacter': term[:1]},
                headers=HEADERS, timeout=10,
            )
            if not r.ok:
                continue
            data = r.json()
            results = data if isinstance(data, list) else data.get('Table', [])
            if not results:
                continue

            # Match by name similarity — take best match above 0.6
            best_code, best_score = None, 0.6
            for item in results:
                bse_name = item.get('SCRIP_NAME') or item.get('scrip_name', '')
                score = _similarity(company_name, bse_name)
                if score > best_score:
                    best_score = score
                    best_code = str(item.get('SCRIP_CD') or item.get('scrip_cd', ''))

            if best_code:
                return best_code
        except Exception as e:
            log.debug(f"BSE search error for '{term}': {e}")
            continue

    return None


def _fetch_financials(scrip_code: str, session) -> Optional[Dict]:
    """Fetch latest annual financial result for a scrip code."""
    try:
        r = session.get(
            BSE_FINANCIALS_URL,
            params={
                'scripcode': scrip_code,
                'period':    'Annual',
                'flag':      'C',   # Consolidated
            },
            headers=HEADERS, timeout=15,
        )
        if not r.ok:
            # Try standalone if consolidated not available
            r = session.get(
                BSE_FINANCIALS_URL,
                params={'scripcode': scrip_code, 'period': 'Annual', 'flag': 'S'},
                headers=HEADERS, timeout=15,
            )
        if not r.ok:
            return None
        data = r.json()
        # BSE returns list of periods — take most recent
        results = data if isinstance(data, list) else data.get('Table', [])
        return results[0] if results else None
    except Exception as e:
        log.debug(f"BSE financials error for {scrip_code}: {e}")
        return None


def _extract_aum_from_result(result: Dict) -> Optional[float]:
    """
    Extract Net Advances (AUM proxy) from BSE financial result dict.
    BSE key names vary — try common ones.
    """
    aum_keys = [
        'NetAdvances', 'Advances', 'ADVANCES', 'LoanBook',
        'NetLoansAndAdvances', 'LoansAndAdvances',
        'TotalAssets',   # fallback — less precise
    ]
    for key in aum_keys:
        val = result.get(key)
        if val not in (None, '', 0, '0'):
            crores = _parse_crores(val)
            # Sanity: AUM between 1 Cr and 30M Cr
            if crores and 1 <= crores <= 30_000_000:
                return crores
    return None


def _fetch_company_details(scrip_code: str, session) -> Dict:
    """Fetch company profile for employee count etc."""
    try:
        r = session.get(
            BSE_COMPANY_URL,
            params={'scripcode': scrip_code},
            headers=HEADERS, timeout=10,
        )
        if r.ok:
            data = r.json()
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> set:
    if CHECKPOINT_FILE.exists():
        try:
            return set(json.loads(CHECKPOINT_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_checkpoint(done: set) -> None:
    try:
        CHECKPOINT_FILE.write_text(json.dumps(list(done)))
    except Exception:
        pass


# ── Supabase ──────────────────────────────────────────────────────────────────

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
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    supa = _get_supabase()

    # Fetch listed lenders
    q = (
        supa.table('lenders')
        .select('id, company_name, aum_crores, employee_count, cin, stock_symbol')
        .eq('approval_status', 'approved')
        .eq('is_listed', True)
    )
    if args.id:
        q = q.eq('id', args.id)
    elif not args.force:
        q = q.is_('aum_crores', 'null')
    if args.limit:
        q = q.limit(args.limit)

    lenders = (q.execute()).data or []
    log.info(f"Listed lenders to process: {len(lenders)}")

    done = _load_checkpoint()

    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503])
    session.mount('https://', HTTPAdapter(max_retries=retry))
    session.mount('http://',  HTTPAdapter(max_retries=retry))

    stats = {'matched': 0, 'aum_found': 0, 'updated': 0, 'not_found': 0}
    BATCH = 50
    updates: List[Dict[str, Any]] = []

    for lender in lenders:
        lid  = lender['id']
        name = lender['company_name']

        if str(lid) in done:
            log.info(f"[{lid}] {name} — skipped (checkpoint)")
            continue

        log.info(f"[{lid}] {name}")

        # 1. Find scrip code — prefer stock_symbol if we have it
        scrip_code = lender.get('stock_symbol') or _search_scrip(name, session)
        if not scrip_code:
            log.info(f"    Not found on BSE")
            stats['not_found'] += 1
            done.add(str(lid))
            continue
        stats['matched'] += 1
        log.info(f"    Scrip: {scrip_code}")

        # 2. Fetch financial result
        result = _fetch_financials(scrip_code, session)
        if not result:
            log.info(f"    No financial result")
            done.add(str(lid))
            continue

        # 3. Extract AUM
        update: Dict[str, Any] = {'id': lid}
        aum = _extract_aum_from_result(result)
        if aum and (args.force or not lender.get('aum_crores')):
            update['aum_crores']   = aum
            update['aum_category'] = _aum_category(aum)
            stats['aum_found'] += 1
            log.info(f"    AUM: ₹{aum:,.0f} Cr  ({update['aum_category']})")

        # 4. Period/year info for audit trail
        period = result.get('period_end') or result.get('PeriodEnd') or ''
        if period:
            log.info(f"    Period: {period}")

        if len(update) > 1:   # has fields beyond just 'id'
            update['data_source'] = 'bse_financial_result'
            updates.append(update)
            stats['updated'] += 1

        done.add(str(lid))
        _save_checkpoint(done)
        time.sleep(0.5)   # polite — BSE rate limits aggressively

    # ── Write ──────────────────────────────────────────────────────────────────
    if not args.dry_run and updates:
        for i in range(0, len(updates), BATCH):
            batch = updates[i:i + BATCH]
            try:
                supa.table('lenders').upsert(batch, on_conflict='id').execute()
                log.info(f"Upserted batch {i//BATCH + 1} ({len(batch)} rows)")
            except Exception as e:
                log.error(f"Upsert failed: {e}")

    log.info("=" * 55)
    log.info(f"BSE matched:     {stats['matched']}")
    log.info(f"Not on BSE:      {stats['not_found']}")
    log.info(f"AUM extracted:   {stats['aum_found']}")
    log.info(f"Lenders updated: {stats['updated']}")
    if args.dry_run:
        log.info("DRY RUN — nothing written")
    else:
        # Clear checkpoint on full successful run
        if not args.limit and not args.id:
            CHECKPOINT_FILE.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description='Fetch AUM from BSE financial results')
    p.add_argument('--dry-run', action='store_true', help='No DB writes')
    p.add_argument('--force',   action='store_true', help='Overwrite existing AUM')
    p.add_argument('--limit',   type=int,            help='Max lenders to process')
    p.add_argument('--id',      type=int,            help='Single lender ID')
    run(p.parse_args())


if __name__ == '__main__':
    main()
