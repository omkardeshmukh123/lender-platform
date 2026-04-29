"""
seed_rbi_registry.py
====================
One-time script: loads data/input/rbi_nbfc_list.xlsx into the rbi_registry table.
Skips rows whose company_name already exists in the lenders table (curated data).

Usage:
    python backend/seed_rbi_registry.py               # run for real
    python backend/seed_rbi_registry.py --dry-run     # preview only
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sync_nbfc_csv import load_rbi_nbfc_excel, parse_cin

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / '.env', override=False)
except ImportError:
    pass

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

ROOT          = Path(__file__).parent.parent
RBI_NBFC_XLSX = ROOT / 'data' / 'input' / 'rbi_nbfc_list.xlsx'

_RBI_LAYER_MAP = {'upper': 'ND-UL', 'middle': 'ND-ML', 'base': 'ND-BL'}


def main() -> None:
    parser = argparse.ArgumentParser(description='Seed rbi_registry from RBI NBFC Excel')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no DB writes')
    args = parser.parse_args()

    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        log.error('DATABASE_URL not set')
        sys.exit(1)

    log.info('Loading RBI Excel from %s', RBI_NBFC_XLSX)
    entries = load_rbi_nbfc_excel(RBI_NBFC_XLSX)
    log.info('Loaded %d rows from Excel', len(entries))

    conn = psycopg2.connect(db_url)
    cur  = conn.cursor()

    cur.execute("SELECT lower(company_name) FROM lenders")
    existing_lender_names = {row[0] for row in cur.fetchall()}
    log.info('Found %d existing lenders in DB (will skip these)', len(existing_lender_names))

    cur.execute("SELECT lower(company_name) FROM rbi_registry")
    existing_registry_names = {row[0] for row in cur.fetchall()}
    log.info('Found %d existing rbi_registry rows', len(existing_registry_names))

    rows_to_insert = []
    skipped_lenders  = 0
    skipped_registry = 0

    for entry in entries:
        name = (entry.get('name') or '').strip()
        if not name:
            continue

        name_lower = name.lower()

        if name_lower in existing_lender_names:
            skipped_lenders += 1
            continue

        if name_lower in existing_registry_names:
            skipped_registry += 1
            continue

        cin  = entry.get('cin') or None
        tier = _RBI_LAYER_MAP.get((entry.get('layer') or '').lower())
        cor  = entry.get('cor') or entry.get('rbi_registration_number') or None

        hq_state         = None
        established_year = None
        if cin:
            decoded          = parse_cin(cin)
            hq_state         = decoded.get('hq_state')
            established_year = decoded.get('established_year')

        rows_to_insert.append((name, cin, cor, tier, hq_state, established_year))
        existing_registry_names.add(name_lower)

    log.info(
        'To insert: %d | skipped (already in lenders): %d | skipped (already in registry): %d',
        len(rows_to_insert), skipped_lenders, skipped_registry,
    )

    if args.dry_run:
        if rows_to_insert:
            log.info('DRY RUN sample (first 5):')
            for r in rows_to_insert[:5]:
                log.info('  %s', r)
        log.info('DRY RUN — no writes made')
        conn.close()
        return

    if rows_to_insert:
        execute_values(
            cur,
            """
            INSERT INTO rbi_registry
              (company_name, cin, rbi_registration_number, regulatory_tier, hq_state, established_year)
            VALUES %s
            ON CONFLICT (lower(company_name)) DO NOTHING
            """,
            rows_to_insert,
        )
        conn.commit()
        log.info('Done. Inserted %d rows into rbi_registry.', len(rows_to_insert))
    else:
        log.info('Nothing new to insert.')

    conn.close()


if __name__ == '__main__':
    main()
