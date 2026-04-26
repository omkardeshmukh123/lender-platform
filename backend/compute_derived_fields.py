"""
compute_derived_fields.py
==========================
Backfills derived / computed fields across all lenders in the DB.
All fields are derived from data that already exists — no scraping needed.

Fields computed:
  operating_intensity   — "Pan India" / "Regional" / "Single State" from operating_states
  aum_category          — Micro / Small / Mid / Large from aum_crores
  business_sector       — MSME / Retail / Housing / Gold / Vehicle / etc from primary_loan_segments
  hq_state              — extracted from hq_location "City, State" when hq_state is null
  pan_india             — True if operating_states covers all 28+ states or is_pan_india text present

Rules:
  - Never overwrite non-null existing values (unless --force is used)
  - Dry-run mode logs changes without writing

Usage:
    python compute_derived_fields.py                  # safe backfill
    python compute_derived_fields.py --force          # overwrite existing values too
    python compute_derived_fields.py --dry-run        # log only, no writes
    python compute_derived_fields.py --limit 100      # first N lenders
    python compute_derived_fields.py --only "NBFC"    # filter by company_type
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
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

# ── AUM category thresholds (₹ Crores) ───────────────────────────────────────
# Micro  < 500 Cr
# Small  500 – 5,000 Cr
# Mid    5,000 – 50,000 Cr
# Large  > 50,000 Cr
def _aum_category(crores: Optional[float]) -> Optional[str]:
    if crores is None:
        return None
    if crores < 500:
        return 'Micro'
    if crores < 5_000:
        return 'Small'
    if crores < 50_000:
        return 'Mid'
    return 'Large'


# ── Operating intensity from operating_states count ───────────────────────────
_TOTAL_INDIAN_STATES = 36   # 28 states + 8 UTs

def _operating_intensity(states: list, pan_india: bool) -> Optional[str]:
    if pan_india:
        return 'Pan India'
    n = len(states) if states else 0
    if n == 0:
        return None
    if n >= 20:
        return 'Pan India'
    if n >= 5:
        return 'Regional'
    return 'Single State'


# ── Business sector from primary_loan_segments ────────────────────────────────
_SECTOR_MAP: Dict[str, str] = {
    'MSME Loan':              'MSME',
    'Business Loan':          'MSME',
    'Working Capital':        'MSME',
    'Supply Chain Finance':   'MSME',
    'Personal Loan':          'Retail',
    'Consumer Durable Loan':  'Retail',
    'Credit Card':            'Retail',
    'Education Loan':         'Retail',
    'Home Loan':              'Housing',
    'Loan Against Property':  'Housing',
    'Gold Loan':              'Gold',
    'Vehicle Loan':           'Vehicle',
    'Two Wheeler Loan':       'Vehicle',
    'EV Loan':                'Vehicle',
    'Agriculture Loan':       'Agriculture',
    'Rural Loan':             'Agriculture',
    'Micro Loan':             'Microfinance',
    'Microfinance':           'Microfinance',
}

def _business_sector(segments: list) -> Optional[str]:
    if not segments:
        return None
    counts: Dict[str, int] = {}
    for seg in segments:
        sector = _SECTOR_MAP.get(seg)
        if sector:
            counts[sector] = counts.get(sector, 0) + 1
    if not counts:
        return None
    # return dominant sector
    return max(counts, key=counts.__getitem__)


# ── hq_state from hq_location "City, State" ───────────────────────────────────
_INDIA_STATES = {
    'andhra pradesh', 'arunachal pradesh', 'assam', 'bihar', 'chhattisgarh',
    'goa', 'gujarat', 'haryana', 'himachal pradesh', 'jharkhand', 'karnataka',
    'kerala', 'madhya pradesh', 'maharashtra', 'manipur', 'meghalaya', 'mizoram',
    'nagaland', 'odisha', 'punjab', 'rajasthan', 'sikkim', 'tamil nadu',
    'telangana', 'tripura', 'uttar pradesh', 'uttarakhand', 'west bengal',
    'delhi', 'jammu & kashmir', 'jammu and kashmir', 'ladakh', 'puducherry',
    'chandigarh', 'dadra and nagar haveli', 'daman and diu', 'lakshadweep',
    'andaman and nicobar islands',
}

_STATE_CANONICAL: Dict[str, str] = {
    'delhi': 'Delhi',
    'new delhi': 'Delhi',
    'mumbai': 'Maharashtra',
    'pune': 'Maharashtra',
    'bengaluru': 'Karnataka',
    'bangalore': 'Karnataka',
    'chennai': 'Tamil Nadu',
    'hyderabad': 'Telangana',
    'kolkata': 'West Bengal',
    'ahmedabad': 'Gujarat',
    'surat': 'Gujarat',
    'jaipur': 'Rajasthan',
    'lucknow': 'Uttar Pradesh',
    'chandigarh': 'Chandigarh',
}

_INDIA_STATES_CANONICAL: Dict[str, str] = {s.lower(): s for s in [
    'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
    'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka',
    'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
    'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu',
    'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
    'Delhi', 'Jammu & Kashmir', 'Jammu and Kashmir', 'Ladakh', 'Puducherry',
    'Chandigarh', 'Dadra and Nagar Haveli', 'Daman and Diu', 'Lakshadweep',
    'Andaman and Nicobar Islands',
]}

def _extract_hq_state(hq_location: Optional[str]) -> Optional[str]:
    if not hq_location:
        return None
    parts = [p.strip() for p in hq_location.split(',')]
    # Try last part as state
    for part in reversed(parts):
        low = part.lower()
        if low in _INDIA_STATES_CANONICAL:
            return _INDIA_STATES_CANONICAL[low]
        # Check canonical city → state map
        if low in _STATE_CANONICAL:
            return _STATE_CANONICAL[low]
    return None


# ── pan_india from operating_states ───────────────────────────────────────────
def _should_be_pan_india(states: list, current: bool) -> bool:
    if current:
        return True
    return len(states) >= _TOTAL_INDIAN_STATES // 2


# ── Supabase helpers ──────────────────────────────────────────────────────────
def _get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set in .env")
        sys.exit(1)
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        log.error("pip install supabase")
        sys.exit(1)


def _parse_jsonb(val) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    try:
        return json.loads(val)
    except Exception:
        return []


# ── main ──────────────────────────────────────────────────────────────────────
def run(args: argparse.Namespace) -> None:
    supa = _get_supabase()

    query = (
        supa.table('lenders')
        .select('id, company_name, company_type, hq_location, hq_state, '
                'operating_states, pan_india, aum_crores, aum_category, '
                'primary_loan_segments, operating_intensity, business_sector')
    )
    if args.only:
        query = query.eq('company_type', args.only)
    if args.limit:
        query = query.limit(args.limit)

    resp    = query.execute()
    lenders = resp.data or []
    log.info(f"Processing {len(lenders)} lenders")

    stats = {
        'operating_intensity': 0,
        'aum_category': 0,
        'business_sector': 0,
        'hq_state': 0,
        'pan_india': 0,
        'total_updated': 0,
    }

    BATCH_SIZE = 50
    updates: List[Dict[str, Any]] = []

    for lender in lenders:
        lid    = lender['id']
        name   = lender['company_name']
        update: Dict[str, Any] = {}

        states   = _parse_jsonb(lender.get('operating_states'))
        segments = _parse_jsonb(lender.get('primary_loan_segments'))
        pan      = bool(lender.get('pan_india', False))

        # operating_intensity
        if args.force or not lender.get('operating_intensity'):
            val = _operating_intensity(states, pan)
            if val and val != lender.get('operating_intensity'):
                update['operating_intensity'] = val
                stats['operating_intensity'] += 1

        # aum_category
        if args.force or not lender.get('aum_category'):
            val = _aum_category(lender.get('aum_crores'))
            if val and val != lender.get('aum_category'):
                update['aum_category'] = val
                stats['aum_category'] += 1

        # business_sector
        if args.force or not lender.get('business_sector'):
            val = _business_sector(segments)
            if val and val != lender.get('business_sector'):
                update['business_sector'] = val
                stats['business_sector'] += 1

        # hq_state
        if args.force or not lender.get('hq_state'):
            val = _extract_hq_state(lender.get('hq_location'))
            if val and val != lender.get('hq_state'):
                update['hq_state'] = val
                stats['hq_state'] += 1

        # pan_india
        if (not pan or args.force) and _should_be_pan_india(states, pan):
            update['pan_india'] = True
            stats['pan_india'] += 1

        if update:
            log.info(f"  {name}: {list(update.keys())}")
            if not args.dry_run:
                updates.append({'id': lid, **update})
            stats['total_updated'] += 1

    # Batch upsert
    if not args.dry_run and updates:
        for i in range(0, len(updates), BATCH_SIZE):
            batch = updates[i : i + BATCH_SIZE]
            try:
                supa.table('lenders').upsert(batch, on_conflict='id').execute()
            except Exception as exc:
                log.error(f"Upsert batch {i//BATCH_SIZE} failed: {exc}")

    log.info("=" * 55)
    log.info(f"Lenders updated:       {stats['total_updated']}")
    log.info(f"operating_intensity:   {stats['operating_intensity']}")
    log.info(f"aum_category:          {stats['aum_category']}")
    log.info(f"business_sector:       {stats['business_sector']}")
    log.info(f"hq_state:              {stats['hq_state']}")
    log.info(f"pan_india:             {stats['pan_india']}")
    if args.dry_run:
        log.info("DRY RUN — nothing written")


def main():
    parser = argparse.ArgumentParser(description='Backfill derived lender fields')
    parser.add_argument('--only',    type=str,  default=None, help='Filter by company_type (e.g. NBFC)')
    parser.add_argument('--limit',   type=int,  default=None, help='Max lenders to process')
    parser.add_argument('--force',   action='store_true',     help='Overwrite existing values')
    parser.add_argument('--dry-run', action='store_true',     help='Log changes, no DB writes')
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
