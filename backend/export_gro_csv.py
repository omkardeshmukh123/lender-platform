"""
export_gro_csv.py
=================
Export grievance_officers table joined with lender names to CSV.

Usage:
    python backend/export_gro_csv.py
    python backend/export_gro_csv.py --output reports/gro_export.csv
    python backend/export_gro_csv.py --all     # include lenders with no GRO (NULL rows)
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

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

DATABASE_URL = os.environ.get('DATABASE_URL', '')


def main():
    p = argparse.ArgumentParser(description='Export GRO data to CSV')
    p.add_argument('--output', default='', help='Output CSV path (default: auto-named in current dir)')
    p.add_argument('--all',    action='store_true', help='Include lenders with no GRO record')
    args = p.parse_args()

    if not DATABASE_URL:
        print('ERROR: DATABASE_URL not set'); sys.exit(1)

    try:
        import psycopg2
    except ImportError:
        print('ERROR: pip install psycopg2-binary'); sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()

    if args.all:
        sql = """
            SELECT
                l.id                AS lender_id,
                l.company_name,
                l.lender_type,
                l.website,
                g.name              AS gro_name,
                g.designation,
                g.email,
                g.phone,
                g.source_url,
                g.source_type,
                g.last_verified_at
            FROM lenders l
            LEFT JOIN grievance_officers g ON g.lender_id = l.id
            WHERE l.approval_status = 'approved'
            ORDER BY l.company_name
        """
    else:
        sql = """
            SELECT
                l.id                AS lender_id,
                l.company_name,
                l.lender_type,
                l.website,
                g.name              AS gro_name,
                g.designation,
                g.email,
                g.phone,
                g.source_url,
                g.source_type,
                g.last_verified_at
            FROM grievance_officers g
            JOIN lenders l ON l.id = g.lender_id
            WHERE l.approval_status = 'approved'
            ORDER BY l.company_name
        """

    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    conn.close()

    if not rows:
        print('No GRO records found yet.')
        sys.exit(0)

    out = args.output or f"gro_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    Path(out).parent.mkdir(parents=True, exist_ok=True)

    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)

    print(f'Exported {len(rows)} rows → {out}')


if __name__ == '__main__':
    main()
