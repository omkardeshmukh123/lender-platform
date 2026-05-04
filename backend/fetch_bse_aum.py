"""
fetch_bse_aum.py
================
Fetches AUM (Loans & Advances) from BSE annual financial statements
for listed NBFCs. Extends the CIN→scrip_code infrastructure in
enrichers/bse_xbrl.py to pull balance sheet data.

Logic:
  1. Query lenders with cin IS NOT NULL and is_listed = TRUE and aum_crores IS NULL
     (or all listed lenders with --force)
  2. BSE CIN → scrip_code lookup
  3. Fetch annual balance sheet from BSE API
  4. Extract "Loans" / "Loans and Advances" line → aum_crores
  5. Update lenders table (never overwrites existing unless --force)

Fields updated:
  aum_crores       — Loan book from balance sheet (Cr)
  aum_category     — Micro / Small / Mid / Large
  last_year_revenue — Total income from P&L (Cr), if missing
  data_source      — 'bse_financials'

Usage:
    python backend/fetch_bse_aum.py --dry-run
    python backend/fetch_bse_aum.py --limit 30
    python backend/fetch_bse_aum.py --force        # overwrite existing AUM
    python backend/fetch_bse_aum.py --id 123       # single lender by DB id
    python backend/fetch_bse_aum.py --stats        # just show coverage numbers
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

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

DATABASE_URL = os.environ.get('DATABASE_URL', '')
RATE_DELAY   = 1.2  # seconds between BSE requests

# ── BSE API constants ──────────────────────────────────────────────────────────
_BSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/124.0.0.0 Safari/537.36',
    'Referer': 'https://www.bseindia.com/',
}

# Returns a list of all equity scrips with CIN_NO, SCRIP_CD, LONG_NAME fields
_BSE_SCRIP_LIST_URL = (
    'https://api.bseindia.com/BseIndiaAPI/api/listofscripData/w'
    '?scripCode=&Group=&industry=&segment=EQ&status=A'
)

# Returns quarterly/annual financial results for a scrip
_BSE_FINANCIALS_URL = (
    'https://api.bseindia.com/BseIndiaAPI/api/StockFinancials/w'
    '?scripcode={code}&type={period}'
)

# XBRL FPC filing — same pattern as enrichers/bse_xbrl.py
_BSE_FPC_URL = 'https://www.bseindia.com/xml-data/corpfiling/{code}/FPC/'

# NSE balance sheet API (fallback for lenders listed on NSE)
_NSE_BS_URL = (
    'https://www.nseindia.com/api/financials-balance-sheet'
    '?index=annualReport&symbol={symbol}'
)

# AUM category thresholds (Crores)
def _aum_category(crores: float) -> str:
    if crores < 500:    return 'Micro'
    if crores < 5000:   return 'Small'
    if crores < 50000:  return 'Mid'
    return 'Large'


# ── DB helpers ────────────────────────────────────────────────────────────────

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


def _fetch_listed_lenders(conn, force: bool, lender_id: Optional[int],
                           limit: Optional[int]) -> List[Dict]:
    with conn.cursor() as cur:
        conditions = ["cin IS NOT NULL", "is_listed = TRUE", "approval_status = 'approved'"]
        if not force:
            conditions.append('aum_crores IS NULL')
        if lender_id:
            conditions.append(f'id = {lender_id}')
        where = ' AND '.join(conditions)
        sql = f"""
            SELECT id, company_name, cin, stock_symbol, aum_crores, last_year_revenue
            FROM lenders
            WHERE {where}
            ORDER BY id
            {"LIMIT " + str(limit) if limit else ""}
        """
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _update_lender(conn, lid: int, fields: Dict[str, Any], dry_run: bool) -> bool:
    if dry_run or not fields:
        return False
    set_parts = ', '.join(f'{k} = %s' for k in fields)
    vals = list(fields.values()) + [lid]
    with conn.cursor() as cur:
        cur.execute(f'UPDATE lenders SET {set_parts} WHERE id = %s', vals)
    conn.commit()
    return True


# ── BSE API ──────────────────────────────────────────────────────────────────

_scrip_cache: Dict[str, str] = {}  # CIN → scrip_code
_scrip_list_loaded = False


def _load_scrip_list(session: requests.Session) -> None:
    global _scrip_list_loaded
    if _scrip_list_loaded:
        return
    log.info('Loading BSE scrip list (one-time)…')
    try:
        resp = session.get(_BSE_SCRIP_LIST_URL, timeout=30, headers=_BSE_HEADERS)
        resp.raise_for_status()
        for item in resp.json().get('Table', []):
            cin = (item.get('CIN_NO') or '').strip().upper()
            code = str(item.get('SCRIP_CD') or '').strip()
            if cin and code:
                _scrip_cache[cin] = code
        log.info('Scrip list loaded: %d entries', len(_scrip_cache))
        _scrip_list_loaded = True
    except Exception as exc:
        log.warning('BSE scrip list failed: %s', exc)


def _cin_to_scrip(cin: str, session: requests.Session) -> Optional[str]:
    _load_scrip_list(session)
    return _scrip_cache.get(cin.upper().strip())


def _fetch_bse_financials(code: str, session: requests.Session) -> Dict[str, Any]:
    """
    Try two BSE endpoints for financial data.
    Returns dict with keys: aum_crores, last_year_revenue (both optional).
    """
    result: Dict[str, Any] = {}

    # Approach 1: StockFinancials (annual)
    try:
        url = _BSE_FINANCIALS_URL.format(code=code, period='annual')
        resp = session.get(url, timeout=15, headers=_BSE_HEADERS)
        if resp.status_code == 200:
            data = resp.json()
            aum, rev = _parse_financials_response(data)
            if aum:
                result['aum_crores'] = aum
            if rev:
                result['last_year_revenue'] = rev
            if result:
                return result
    except Exception as exc:
        log.debug('StockFinancials annual failed for %s: %s', code, exc)

    # Approach 2: StockFinancials (quarterly latest → annualised)
    try:
        url = _BSE_FINANCIALS_URL.format(code=code, period='quarterly')
        resp = session.get(url, timeout=15, headers=_BSE_HEADERS)
        if resp.status_code == 200:
            data = resp.json()
            aum, rev = _parse_financials_response(data)
            if aum:
                result['aum_crores'] = aum
            if rev:
                result['last_year_revenue'] = rev
            if result:
                return result
    except Exception as exc:
        log.debug('StockFinancials quarterly failed for %s: %s', code, exc)

    # Approach 3: FPC filing (has loan amount ranges — use max as AUM proxy if very large)
    try:
        fpc = _fetch_fpc(code, session)
        if fpc.get('loan_amount_max_lakhs', 0) > 0:
            # LoanAmountMax is the max ticket size, not AUM — skip for AUM
            pass
    except Exception:
        pass

    return result


def _parse_financials_response(data: Any) -> Tuple[Optional[float], Optional[float]]:
    """
    Parse BSE StockFinancials API response.
    Returns (aum_crores, revenue_crores) or (None, None).
    """
    aum_crores = None
    rev_crores  = None

    # The API returns nested structure; try common shapes
    rows: List[Dict] = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ('Table', 'Table1', 'data', 'result', 'financials'):
            if isinstance(data.get(key), list):
                rows = data[key]
                break

    # Loan-book line item keywords (case-insensitive partial match)
    _LOAN_KEYS = [
        'loans and advances', 'loans', 'net loans', 'loan book',
        'financial assets - loans', 'receivables under financing',
        'advances', 'net advances',
    ]
    _REV_KEYS = [
        'revenue from operations', 'total income', 'net interest income',
        'interest income', 'total revenue',
    ]

    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get('label') or row.get('name') or row.get('particular') or
                    row.get('head') or row.get('description') or '').lower().strip()
        raw_val = row.get('value') or row.get('amount') or row.get('current_year')
        try:
            val = float(str(raw_val).replace(',', '').strip())
        except (TypeError, ValueError):
            val = None

        if val is None or val <= 0:
            continue

        if aum_crores is None:
            for kw in _LOAN_KEYS:
                if kw in label:
                    # BSE reports in Crores or Lakhs — detect by magnitude
                    # NBFCs with AUM > ₹500Cr should report > 500 in Crores
                    # If value looks like Lakhs (< 100 for even small NBFCs), convert
                    aum_crores = _normalise_to_crores(val, label)
                    break

        if rev_crores is None:
            for kw in _REV_KEYS:
                if kw in label:
                    rev_crores = _normalise_to_crores(val, label)
                    break

    return aum_crores, rev_crores


def _normalise_to_crores(val: float, context: str) -> float:
    """
    BSE sometimes reports in Lakhs, sometimes Crores. Heuristic:
    - If value > 1_000_000, probably in Rupees → divide by 10_000_000 (Crores)
    - If value > 1_000, could be Lakhs → divide by 100 for Crores
    - Otherwise treat as Crores directly
    """
    if val > 1_000_000:
        return round(val / 1e7, 2)   # Rupees → Crores
    if val > 50_000:
        return round(val / 100, 2)   # Lakhs → Crores
    return round(val, 2)


def _fetch_fpc(code: str, session: requests.Session) -> Dict:
    try:
        url = _BSE_FPC_URL.format(code=code)
        resp = session.get(url, timeout=10, headers=_BSE_HEADERS)
        if resp.status_code == 200:
            d = resp.json()
            return {
                'interest_rate_min':    float(d.get('InterestRateMin') or 0),
                'interest_rate_max':    float(d.get('InterestRateMax') or 0),
                'loan_amount_max_lakhs': float(d.get('LoanAmountMaxLakhs') or 0),
            }
    except Exception:
        pass
    return {}


# ── Stats ────────────────────────────────────────────────────────────────────

def _print_stats(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE is_listed AND cin IS NOT NULL) AS listed_with_cin,
                COUNT(*) FILTER (WHERE is_listed AND cin IS NOT NULL AND aum_crores IS NOT NULL) AS listed_with_aum,
                COUNT(*) FILTER (WHERE is_listed AND cin IS NOT NULL AND aum_crores IS NULL) AS listed_missing_aum,
                COUNT(*) FILTER (WHERE aum_crores IS NOT NULL) AS total_with_aum,
                COUNT(*) AS total_lenders
            FROM lenders
            WHERE approval_status = 'approved'
        """)
        row = cur.fetchone()
    print()
    print('=== AUM Coverage (approved lenders) ===')
    print(f'  Total approved lenders  : {row[4]:,}')
    print(f'  Listed + have CIN       : {row[0]:,}')
    print(f'  Listed + have AUM       : {row[1]:,}')
    print(f'  Listed + missing AUM    : {row[2]:,}  ← target for this script')
    print(f'  All lenders with AUM    : {row[3]:,}')
    pct = round(row[3] / row[4] * 100, 1) if row[4] else 0
    print(f'  AUM coverage            : {pct}%')
    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    conn = _get_conn()

    if args.stats:
        _print_stats(conn)
        conn.close()
        return

    lenders = _fetch_listed_lenders(conn, args.force, args.id, args.limit)
    log.info('Listed lenders to process: %d', len(lenders))
    if not lenders:
        log.info('Nothing to do. Use --force to reprocess lenders that already have AUM.')
        conn.close()
        return

    session = requests.Session()
    session.headers.update(_BSE_HEADERS)

    stats = {'scrip_found': 0, 'aum_found': 0, 'rev_found': 0, 'updated': 0, 'no_scrip': 0}

    for lender in lenders:
        lid  = lender['id']
        name = lender['company_name']
        cin  = (lender.get('cin') or '').strip().upper()

        log.info('[%d] %s  (CIN: %s)', lid, name, cin or 'None')

        if not cin:
            log.info('    No CIN — skipping')
            stats['no_scrip'] += 1
            continue

        scrip = _cin_to_scrip(cin, session)
        if not scrip:
            log.info('    CIN not found in BSE scrip list')
            stats['no_scrip'] += 1
            time.sleep(0.3)
            continue

        log.info('    BSE scrip_code: %s', scrip)
        stats['scrip_found'] += 1

        time.sleep(RATE_DELAY)
        fin = _fetch_bse_financials(scrip, session)

        update: Dict[str, Any] = {}

        if fin.get('aum_crores') and (args.force or not lender.get('aum_crores')):
            aum = fin['aum_crores']
            update['aum_crores']   = aum
            update['aum_category'] = _aum_category(aum)
            update['data_source']  = 'bse_financials'
            stats['aum_found'] += 1
            log.info('    AUM: ₹%.0f Cr  (%s)', aum, update['aum_category'])

        if fin.get('last_year_revenue') and (args.force or not lender.get('last_year_revenue')):
            update['last_year_revenue'] = fin['last_year_revenue']
            stats['rev_found'] += 1
            log.info('    Revenue: ₹%.0f Cr', fin['last_year_revenue'])

        if not fin:
            log.info('    No financial data returned — API may need adjustment (see --debug)')

        if update:
            ok = _update_lender(conn, lid, update, args.dry_run)
            if ok:
                stats['updated'] += 1

    conn.close()

    print()
    print('=' * 55)
    print(f"Listed lenders processed : {len(lenders)}")
    print(f"BSE scrip_code found     : {stats['scrip_found']}")
    print(f"No scrip / not listed    : {stats['no_scrip']}")
    print(f"AUM extracted            : {stats['aum_found']}")
    print(f"Revenue extracted        : {stats['rev_found']}")
    print(f"Lenders updated          : {stats['updated']}")
    if args.dry_run:
        print('DRY RUN — nothing written to DB')


def main():
    p = argparse.ArgumentParser(
        description='Fetch AUM from BSE balance sheets for listed NBFCs'
    )
    p.add_argument('--dry-run', action='store_true', help='No DB writes')
    p.add_argument('--force',   action='store_true', help='Overwrite existing AUM')
    p.add_argument('--limit',   type=int,            help='Max lenders to process')
    p.add_argument('--id',      type=int,            help='Single lender by DB id')
    p.add_argument('--stats',   action='store_true', help='Show coverage stats and exit')
    p.add_argument('--debug',   action='store_true', help='Verbose API response logging')
    run(p.parse_args())


if __name__ == '__main__':
    main()
