"""
fetch_bse_financials.py
========================
Enriches listed lenders with financial data from Screener.in.

For each BSE/NSE-listed lender missing AUM:
  1. Searches Screener.in for the company
  2. Extracts: AUM (Net Advances / Loan Book), last_year_revenue
  3. Upserts into lenders table

Fields updated:
  aum_crores       — Loan book or Net Advances (best AUM proxy)
  aum_category     — derived from aum_crores
  last_year_revenue
  stock_symbol     — Screener slug (NSE/BSE ticker)
  data_source      — 'screener'

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
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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


# ── AUM category ──────────────────────────────────────────────────────────────

def _aum_category(crores: float) -> str:
    if crores < 500:    return 'Micro'
    if crores < 5000:   return 'Small'
    if crores < 50000:  return 'Mid'
    return 'Large'


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
    sys.path.insert(0, str(Path(__file__).parent))
    from enrich_lenders import screener_enrich

    supa = _get_supabase()

    # Fetch listed lenders
    q = (
        supa.table('lenders')
        .select('id, company_name, aum_crores, last_year_revenue, stock_symbol')
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
    try:
        session.get('https://www.screener.in/', timeout=10)
    except Exception:
        pass

    stats = {'found': 0, 'aum_found': 0, 'updated': 0, 'not_found': 0}
    BATCH = 50
    updates: List[Dict[str, Any]] = []

    for lender in lenders:
        lid  = lender['id']
        name = lender['company_name']

        if str(lid) in done:
            log.info(f"[{lid}] {name} — skipped (checkpoint)")
            continue

        log.info(f"[{lid}] {name}")

        # Strip trailing punctuation — Screener search fails on "Ltd." vs "Ltd"
        clean_name = name.rstrip('. ')
        result = screener_enrich(clean_name, session)
        if not result:
            log.info(f"    Not found on Screener")
            stats['not_found'] += 1
            done.add(str(lid))
            _save_checkpoint(done)
            time.sleep(1.0)
            continue

        stats['found'] += 1
        update: Dict[str, Any] = {'id': lid}

        aum = result.get('aum_crores')
        if aum and (args.force or not lender.get('aum_crores')):
            update['aum_crores']   = aum
            update['aum_category'] = _aum_category(float(aum))
            stats['aum_found'] += 1
            log.info(f"    AUM: ₹{aum:,.0f} Cr  ({update['aum_category']})")

        rev = result.get('last_year_revenue')
        if rev and (args.force or not lender.get('last_year_revenue')):
            update['last_year_revenue'] = rev

        slug = result.get('stock_symbol')
        if slug and (args.force or not lender.get('stock_symbol')):
            update['stock_symbol'] = slug

        if len(update) > 1:
            updates.append(update)
            stats['updated'] += 1

        if not args.dry_run:
            done.add(str(lid))
            _save_checkpoint(done)

        time.sleep(1.5)

    # ── Write ──────────────────────────────────────────────────────────────────
    if not args.dry_run and updates:
        ok = 0
        for row in updates:
            lid = row.pop('id')
            try:
                supa.table('lenders').update(row).eq('id', lid).execute()
                ok += 1
            except Exception as e:
                log.error(f"Update failed for id={lid}: {e}")
        if ok:
            log.info(f"Updated {ok} rows")

    log.info("=" * 55)
    log.info(f"Screener matched: {stats['found']}")
    log.info(f"Not found:        {stats['not_found']}")
    log.info(f"AUM extracted:    {stats['aum_found']}")
    log.info(f"Lenders updated:  {stats['updated']}")
    if args.dry_run:
        log.info("DRY RUN — nothing written")
    else:
        if not args.limit and not args.id:
            CHECKPOINT_FILE.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description='Fetch AUM from Screener.in for listed lenders')
    p.add_argument('--dry-run', action='store_true', help='No DB writes')
    p.add_argument('--force',   action='store_true', help='Overwrite existing AUM')
    p.add_argument('--limit',   type=int,            help='Max lenders to process')
    p.add_argument('--id',      type=int,            help='Single lender ID')
    run(p.parse_args())


if __name__ == '__main__':
    main()
