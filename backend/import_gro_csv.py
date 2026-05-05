"""
import_gro_csv.py
================
Import Grievance Officer (GRO) contacts from a CSV file into the
grievance_officers table.

Expected CSV columns (configurable via --col-* flags):
  lender_cin      — CIN of the lender (preferred match key)
  lender_name     — company name (fallback match key)
  name            — GRO name
  designation     — GRO designation / title
  email           — GRO email
  phone           — GRO phone
  source_url      — URL where GRO was found
  source_type     — website | rbi_circular | annual_report | manual

On conflict (unique lender_id), existing record is overwritten and
last_verified_at is set to NOW().

Usage:
    python backend/import_gro_csv.py --file gro_contacts.csv --dry-run
    python backend/import_gro_csv.py --file gro_contacts.csv --apply
    python backend/import_gro_csv.py --file gro_contacts.csv --apply --source-type rbi_circular
    python backend/import_gro_csv.py --stats
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
_LEVENSHTEIN_THRESHOLD = 0.82
_VALID_SOURCE_TYPES = {'website', 'rbi_circular', 'annual_report', 'manual'}


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


# ── Fuzzy name match ───────────────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    try:
        from Levenshtein import ratio
        return ratio(a.lower(), b.lower())
    except ImportError:
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
    if cin:
        cur.execute("SELECT id FROM lenders WHERE cin = %s LIMIT 1", (cin,))
        row = cur.fetchone()
        if row:
            return row[0], 'cin_exact'
    if not company_name:
        return None
    best_id, best_score = None, 0.0
    for lid, lname in lender_names:
        s = _similarity(company_name, lname)
        if s > best_score:
            best_score = s
            best_id = lid
    if best_score >= _LEVENSHTEIN_THRESHOLD:
        return best_id, f'name_fuzzy({best_score:.2f})'
    return None


# ── Row normalisation ──────────────────────────────────────────────────────────

def _str(v: Any) -> str:
    return str(v).strip() if v is not None else ''


def _load_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def _normalise_row(row: Dict[str, str], col: Dict[str, str], default_source_type: str) -> Dict[str, str]:
    source_type = _str(row.get(col['source_type'], '')) or default_source_type
    if source_type not in _VALID_SOURCE_TYPES:
        source_type = default_source_type
    return {
        'lender_cin':   _str(row.get(col['lender_cin'], '')),
        'lender_name':  _str(row.get(col['lender_name'], '')),
        'name':         _str(row.get(col['name'], '')),
        'designation':  _str(row.get(col['designation'], '')),
        'email':        _str(row.get(col['email'], '')),
        'phone':        _str(row.get(col['phone'], '')),
        'source_url':   _str(row.get(col['source_url'], '')),
        'source_type':  source_type,
    }


# ── Stats ──────────────────────────────────────────────────────────────────────

def _show_stats(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM grievance_officers")
        gro_total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM lenders")
        lenders_total = cur.fetchone()[0]
        cur.execute(
            "SELECT source_type, COUNT(*) FROM grievance_officers GROUP BY source_type ORDER BY COUNT(*) DESC"
        )
        by_source = cur.fetchall()
    log.info('GRO records total      : %d / %d lenders', gro_total, lenders_total)
    for source_type, count in by_source:
        log.info('  %-20s : %d', source_type, count)


# ── Main import ────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    conn = _get_conn()

    if args.stats:
        _show_stats(conn)
        conn.close()
        return

    path = Path(args.file)
    if not path.exists():
        log.error('File not found: %s', path)
        sys.exit(1)

    col = {
        'lender_cin':   args.col_cin,
        'lender_name':  args.col_name,
        'name':         args.col_gro_name,
        'designation':  args.col_designation,
        'email':        args.col_email,
        'phone':        args.col_phone,
        'source_url':   args.col_source_url,
        'source_type':  args.col_source_type,
    }

    raw_rows = _load_csv(path)
    log.info('Loaded %d rows from %s', len(raw_rows), path.name)

    records = [_normalise_row(r, col, args.source_type) for r in raw_rows]
    usable = [r for r in records if r['lender_cin'] or r['lender_name']]
    log.info('Rows with lender identifier: %d / %d', len(usable), len(records))

    apply = args.apply

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, company_name FROM lenders")
            lender_names: List[Tuple[int, str]] = cur.fetchall()

            matched = unmatched = inserted = updated = 0

            for rec in usable:
                result = _find_lender_id(cur, rec['lender_cin'], rec['lender_name'], lender_names)
                if not result:
                    unmatched += 1
                    log.debug('NO MATCH: cin=%s  name=%s', rec['lender_cin'], rec['lender_name'])
                    continue

                lender_id, match_type = result
                matched += 1

                # Check if GRO record already exists
                cur.execute("SELECT id FROM grievance_officers WHERE lender_id = %s", (lender_id,))
                existing = cur.fetchone()
                action = 'UPDATE' if existing else 'INSERT'

                log.info(
                    '[%s][%s] lender_id=%d  gro=%s <%s>',
                    action, match_type, lender_id, rec['name'], rec['email'],
                )

                if apply:
                    cur.execute(
                        """
                        INSERT INTO grievance_officers
                            (lender_id, name, designation, email, phone,
                             source_url, source_type, last_verified_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (lender_id) DO UPDATE SET
                            name             = EXCLUDED.name,
                            designation      = EXCLUDED.designation,
                            email            = EXCLUDED.email,
                            phone            = EXCLUDED.phone,
                            source_url       = EXCLUDED.source_url,
                            source_type      = EXCLUDED.source_type,
                            last_verified_at = NOW()
                        """,
                        (
                            lender_id,
                            rec['name'] or None,
                            rec['designation'] or None,
                            rec['email'] or None,
                            rec['phone'] or None,
                            rec['source_url'] or None,
                            rec['source_type'],
                        ),
                    )
                    if existing:
                        updated += 1
                    else:
                        inserted += 1

            log.info(
                'matched=%d  unmatched=%d  inserted=%d  updated=%d  (apply=%s)',
                matched, unmatched, inserted, updated, apply,
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
    p = argparse.ArgumentParser(description='Import GRO contacts from CSV into grievance_officers')
    p.add_argument('--file',        help='Path to CSV file')
    p.add_argument('--apply',       action='store_true', help='Commit changes (default: dry-run)')
    p.add_argument('--stats',       action='store_true', help='Print GRO coverage stats and exit')
    p.add_argument('--source-type', default='manual',
                   choices=sorted(_VALID_SOURCE_TYPES),
                   help='Default source_type when column is missing (default: manual)')
    p.add_argument('--threshold',   type=float, default=_LEVENSHTEIN_THRESHOLD,
                   help=f'Name similarity threshold (default {_LEVENSHTEIN_THRESHOLD})')
    # Column name overrides
    p.add_argument('--col-cin',         default='lender_cin',   help='Column: lender CIN')
    p.add_argument('--col-name',        default='lender_name',  help='Column: lender company name')
    p.add_argument('--col-gro-name',    default='name',         help='Column: GRO name')
    p.add_argument('--col-designation', default='designation',  help='Column: GRO designation')
    p.add_argument('--col-email',       default='email',        help='Column: GRO email')
    p.add_argument('--col-phone',       default='phone',        help='Column: GRO phone')
    p.add_argument('--col-source-url',  default='source_url',   help='Column: source URL')
    p.add_argument('--col-source-type', default='source_type',  help='Column: source type')
    return p.parse_args()


if __name__ == '__main__':
    args = _parse()
    _LEVENSHTEIN_THRESHOLD = args.threshold
    if not args.stats and not args.file:
        print('error: --file is required unless --stats is used', file=sys.stderr)
        sys.exit(1)
    run(args)
