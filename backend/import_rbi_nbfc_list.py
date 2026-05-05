"""
import_rbi_nbfc_list.py
=======================
Import RBI master NBFC list (Excel or CSV) into the database.

Two-step process:
  1. Upsert all rows into rbi_registry (canonical RBI reference store).
  2. Cross-reference rbi_registry against lenders by CIN (exact) or
     company_name (Levenshtein similarity ≥ threshold) and update
     lenders.rbi_category and lenders.cin where a confident match is found.

Expected Excel/CSV columns (configurable via --col-* flags):
  company_name          (required)
  rbi_registration_no   — certificate of registration number
  cin                   — Corporate Identity Number (MCA)
  rbi_category          — NBFC-MFI / NBFC-D / NBFC-ND-SI / etc.
  hq_state
  established_year

RBI NBFC list URL (download manually):
  https://rbi.org.in/Scripts/NBFC_List.aspx

Usage:
    python backend/import_rbi_nbfc_list.py --file rbi_nbfc_list.xlsx --dry-run
    python backend/import_rbi_nbfc_list.py --file rbi_nbfc_list.xlsx --apply
    python backend/import_rbi_nbfc_list.py --file rbi_nbfc_list.csv  --apply --sheet 0
    python backend/import_rbi_nbfc_list.py --file rbi_nbfc_list.xlsx --stats
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
_LEVENSHTEIN_THRESHOLD = 0.82  # similarity ratio for name fuzzy match


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_conn():
    if not DATABASE_URL:
        log.error('DATABASE_URL not set in .env')
        sys.exit(1)
    try:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    except Exception as exc:
        log.error('DB connection failed: %s', exc)
        sys.exit(1)


# ── File reading ───────────────────────────────────────────────────────────────

def _read_excel(path: Path, sheet: int | str) -> List[Dict[str, Any]]:
    try:
        import openpyxl
    except ImportError:
        log.error('openpyxl not installed. Run: pip install openpyxl')
        sys.exit(1)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if isinstance(sheet, int):
        ws = wb.worksheets[sheet]
    else:
        ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else f'col_{i}' for i, h in enumerate(rows[0])]
    return [dict(zip(headers, row)) for row in rows[1:] if any(c is not None for c in row)]


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def _load_file(path: Path, sheet: int | str) -> List[Dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in ('.xlsx', '.xls', '.xlsm'):
        return _read_excel(path, sheet)
    if suffix == '.csv':
        return _read_csv(path)
    log.error('Unsupported file type: %s (expected .xlsx or .csv)', suffix)
    sys.exit(1)


# ── Row normalisation ──────────────────────────────────────────────────────────

def _str(v: Any) -> str:
    return str(v).strip() if v is not None else ''


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _normalise_row(row: Dict[str, Any], col: Dict[str, str]) -> Optional[Dict[str, Any]]:
    name = _str(row.get(col['company_name'], ''))
    if not name:
        return None
    return {
        'company_name':          name,
        'rbi_registration_number': _str(row.get(col['rbi_registration_no'], '')),
        'cin':                   _str(row.get(col['cin'], '')),
        'regulatory_tier':       _str(row.get(col['rbi_category'], '')),
        'hq_state':              _str(row.get(col['hq_state'], '')),
        'established_year':      _int_or_none(row.get(col['established_year'])),
    }


# ── Fuzzy name match ───────────────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    try:
        from Levenshtein import ratio  # python-Levenshtein
        return ratio(a.lower(), b.lower())
    except ImportError:
        # stdlib fallback: Jaccard on trigrams
        def trigrams(s: str):
            s = s.lower()
            return {s[i:i+3] for i in range(len(s) - 2)}
        ta, tb = trigrams(a), trigrams(b)
        if not ta and not tb:
            return 1.0
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)


def _find_lender_id(
    cur,
    cin: str,
    company_name: str,
    lender_names: List[Tuple[int, str]],
) -> Optional[Tuple[int, str]]:
    """Return (lender_id, match_type) or None."""
    if cin:
        cur.execute("SELECT id FROM lenders WHERE cin = %s LIMIT 1", (cin,))
        row = cur.fetchone()
        if row:
            return row[0], 'cin_exact'
    # Fuzzy name match against cached list
    best_id, best_score = None, 0.0
    for lid, lname in lender_names:
        s = _similarity(company_name, lname)
        if s > best_score:
            best_score = s
            best_id = lid
    if best_score >= _LEVENSHTEIN_THRESHOLD:
        return best_id, f'name_fuzzy({best_score:.2f})'
    return None


# ── Stats ──────────────────────────────────────────────────────────────────────

def _show_stats(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM rbi_registry")
        registry_total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM lenders WHERE rbi_category IS NOT NULL AND rbi_category != ''")
        lenders_with_cat = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM lenders WHERE cin IS NOT NULL AND cin != ''")
        lenders_with_cin = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM lenders")
        lenders_total = cur.fetchone()[0]
    log.info('rbi_registry rows      : %d', registry_total)
    log.info('lenders total          : %d', lenders_total)
    log.info('lenders with rbi_cat   : %d / %d', lenders_with_cat, lenders_total)
    log.info('lenders with cin       : %d / %d', lenders_with_cin, lenders_total)


# ── Main import ────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.exists():
        log.error('File not found: %s', path)
        sys.exit(1)

    col = {
        'company_name':      args.col_name,
        'rbi_registration_no': args.col_reg,
        'cin':               args.col_cin,
        'rbi_category':      args.col_cat,
        'hq_state':          args.col_state,
        'established_year':  args.col_year,
    }

    raw_rows = _load_file(path, args.sheet if not str(args.sheet).isdigit() else int(args.sheet))
    log.info('Loaded %d raw rows from %s', len(raw_rows), path.name)

    records = [r for raw in raw_rows if (r := _normalise_row(raw, col)) is not None]
    log.info('Valid records after normalisation: %d', len(records))
    if not records:
        log.warning('No valid records — check --col-* column name flags')
        return

    conn = _get_conn()
    apply = args.apply

    try:
        with conn.cursor() as cur:
            if args.stats:
                _show_stats(conn)
                return

            # Step 1 — upsert rbi_registry
            registry_inserted = registry_updated = 0
            for rec in records:
                cur.execute(
                    "SELECT id FROM rbi_registry WHERE lower(company_name) = lower(%s)",
                    (rec['company_name'],),
                )
                existing = cur.fetchone()
                if existing:
                    if apply:
                        cur.execute(
                            """
                            UPDATE rbi_registry SET
                                cin = COALESCE(NULLIF(%s,''), cin),
                                rbi_registration_number = COALESCE(NULLIF(%s,''), rbi_registration_number),
                                regulatory_tier = COALESCE(NULLIF(%s,''), regulatory_tier),
                                hq_state = COALESCE(NULLIF(%s,''), hq_state),
                                established_year = COALESCE(%s, established_year)
                            WHERE id = %s
                            """,
                            (
                                rec['cin'], rec['rbi_registration_number'],
                                rec['regulatory_tier'], rec['hq_state'],
                                rec['established_year'], existing[0],
                            ),
                        )
                    registry_updated += 1
                else:
                    if apply:
                        cur.execute(
                            """
                            INSERT INTO rbi_registry
                                (company_name, cin, rbi_registration_number,
                                 regulatory_tier, hq_state, established_year)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (lower(company_name)) DO NOTHING
                            """,
                            (
                                rec['company_name'], rec['cin'] or None,
                                rec['rbi_registration_number'] or None,
                                rec['regulatory_tier'] or None,
                                rec['hq_state'] or None,
                                rec['established_year'],
                            ),
                        )
                    registry_inserted += 1

            log.info(
                '[rbi_registry] would insert=%d  would update=%d  (apply=%s)',
                registry_inserted, registry_updated, apply,
            )

            # Step 2 — cross-reference lenders
            cur.execute("SELECT id, company_name FROM lenders")
            lender_names: List[Tuple[int, str]] = cur.fetchall()

            matched = unmatched = lender_updated = 0
            for rec in records:
                result = _find_lender_id(cur, rec['cin'], rec['company_name'], lender_names)
                if not result:
                    unmatched += 1
                    log.debug('NO MATCH: %s', rec['company_name'])
                    continue
                lender_id, match_type = result
                matched += 1
                log.info(
                    'MATCH [%s] %s → lender_id=%d  cat=%s  cin=%s',
                    match_type, rec['company_name'], lender_id,
                    rec['regulatory_tier'], rec['cin'],
                )
                if apply:
                    cur.execute(
                        """
                        UPDATE lenders SET
                            rbi_category = COALESCE(NULLIF(%s,''), rbi_category),
                            cin          = COALESCE(NULLIF(%s,''), cin)
                        WHERE id = %s
                        """,
                        (rec['regulatory_tier'], rec['cin'] or None, lender_id),
                    )
                    lender_updated += 1

            log.info(
                '[lenders] matched=%d  unmatched=%d  updated=%d  (apply=%s)',
                matched, unmatched, lender_updated, apply,
            )

        if apply:
            conn.commit()
            log.info('Changes committed.')
        else:
            conn.rollback()
            log.info('DRY RUN — no changes written. Re-run with --apply to commit.')
    finally:
        conn.close()


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Import RBI NBFC list into rbi_registry + lenders')
    p.add_argument('--file',     required=True, help='Path to .xlsx or .csv file')
    p.add_argument('--sheet',    default='0', help='Sheet index (0-based) or name (Excel only)')
    p.add_argument('--apply',    action='store_true', help='Commit changes (default: dry-run)')
    p.add_argument('--stats',    action='store_true', help='Print coverage stats and exit')
    p.add_argument('--threshold', type=float, default=_LEVENSHTEIN_THRESHOLD,
                   help=f'Name similarity threshold (default {_LEVENSHTEIN_THRESHOLD})')
    # Column name overrides
    p.add_argument('--col-name',  default='Name of NBFC',         help='Column: company name')
    p.add_argument('--col-reg',   default='Registration Number',  help='Column: RBI reg number')
    p.add_argument('--col-cin',   default='CIN',                  help='Column: CIN')
    p.add_argument('--col-cat',   default='Category',             help='Column: NBFC category')
    p.add_argument('--col-state', default='State',                help='Column: HQ state')
    p.add_argument('--col-year',  default='Date of Registration', help='Column: established year')
    return p.parse_args()


if __name__ == '__main__':
    args = _parse()
    _LEVENSHTEIN_THRESHOLD = args.threshold
    run(args)
