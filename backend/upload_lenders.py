"""
upload_lenders.py
=================
Upserts lender CSVs (from run_rbi_extraction.py / run_nbfc_extraction.py)
into the Supabase lenders table, then rewrites the CSV with correct DB IDs
so run_policy_extraction.py can link policies to the right lenders.

Usage:
    python upload_lenders.py                        # upload both CSVs
    python upload_lenders.py --rbi-only             # RBI banks only
    python upload_lenders.py --nbfc-only            # NBFCs only
    python upload_lenders.py --dry-run              # show counts, no DB writes

Rules:
    - Existing lenders (matched by company_name) → UPDATE data, keep approval_status
    - New lenders                                 → INSERT with approval_status='pending'
    - approval_status is NEVER overwritten for existing rows
"""

import argparse, csv, json, logging, os, sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values, Json

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

ROOT      = Path(__file__).parent.parent
RBI_CSV   = ROOT / 'data' / 'output' / 'rbi_banks_extracted_v8.csv'
NBFC_CSV  = ROOT / 'data' / 'output' / 'nbfc_extracted_verified.csv'
DATABASE_URL = os.environ.get('DATABASE_URL', '')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
)

# ── DB column names that we upload (excludes id, created_at, last_updated) ────
UPSERT_COLS = [
    'company_name', 'company_type', 'website',
    'aum_crores', 'aum_category', 'last_year_revenue',
    'is_listed', 'stock_symbol',
    'primary_loan_segments', 'primary_product', 'product_types',
    'hq_location', 'hq_state', 'operating_states', 'operating_intensity', 'pan_india',
    'rbi_category', 'rbi_registration_number',
    'recent_funding', 'recent_funding_amount', 'recent_funding_year',
    'established_year', 'employee_count', 'branch_count',
    'ticket_size_min', 'ticket_size_max', 'has_subsidiaries',
    'phone', 'email', 'rural_presence',
    'quality_score', 'data_hash', 'data_source', 'extraction_status',
    'last_scraped_at',
]

# Columns that are JSONB in the DB — stored as JSON strings in CSV
JSONB_COLS = {'product_types'}

# Columns that are TEXT[] in the DB — must be sent as Python lists
TEXT_ARRAY_COLS = {'primary_loan_segments', 'operating_states'}

# Boolean columns
BOOL_COLS = {'is_listed', 'pan_india', 'has_subsidiaries', 'rural_presence'}

# Numeric columns
NUMERIC_COLS = {
    'aum_crores', 'last_year_revenue', 'recent_funding_amount',
    'ticket_size_min', 'ticket_size_max', 'quality_score',
}

INT_COLS = {'recent_funding_year', 'established_year', 'employee_count', 'branch_count'}


def _coerce(col: str, val: Any) -> Any:
    """Convert CSV string value to the correct Python type for psycopg2."""
    if val is None or val == '' or val == 'None':
        return None

    if col in TEXT_ARRAY_COLS:
        if isinstance(val, list):
            return val
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    if col in JSONB_COLS:
        if isinstance(val, (list, dict)):
            return Json(val)
        try:
            parsed = json.loads(val)
            return Json(parsed)
        except Exception:
            return Json({})

    if col in BOOL_COLS:
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ('true', '1', 'yes')

    if col in NUMERIC_COLS:
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    if col in INT_COLS:
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None

    # data_source must be one of the canonical DB values
    if col == 'data_source':
        _ds_map = {
            'scraper+gemini':     'scraper+gemini',
            'gemini_only':        'gemini_only',
            'manual':             'manual',
            'rbi_api':            'rbi_api',
            'scraper':            'scraper+gemini',
            'csv+gemini+scraper': 'scraper+gemini',
            'csv+gemini':         'gemini_only',
            'gemini_rbi':         'gemini_only',
        }
        return _ds_map.get(str(val).strip(), 'scraper+gemini')

    # aum_category must be one of the allowed values or NULL
    if col == 'aum_category':
        allowed = {'Micro', 'Small', 'Mid', 'Large'}
        v = str(val).strip()
        return v if v in allowed else None

    return str(val).strip() or None


def csv_to_rows(path: Path, default_company_type: Optional[str] = None) -> List[Dict]:
    """Read a lender CSV and return list of dicts with coerced values."""
    if not path.exists():
        logging.warning(f"CSV not found: {path}")
        return []

    rows = []
    with open(path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            name = row.get('company_name', '').strip()
            if not name:
                continue
            status = row.get('extraction_status', '').strip()
            if status and status not in ('success', 'partial', ''):
                continue  # skip failed extractions

            record = {}
            for col in UPSERT_COLS:
                raw = row.get(col)
                record[col] = _coerce(col, raw)

            # Ensure company_name and company_type are always set
            record['company_name'] = name
            if not record.get('company_type') and default_company_type:
                record['company_type'] = default_company_type
            if not record.get('company_type'):
                record['company_type'] = 'NBFC'

            record['extraction_status'] = 'success'
            record['last_scraped_at'] = 'NOW()'  # signal to use DB NOW()

            rows.append(record)

    logging.info(f"Loaded {len(rows)} rows from {path.name}")
    return rows


_VALID_COMPANY_TYPES = {
    'NBFC', 'Private Bank', 'PSU Bank', 'Foreign Bank',
    'Cooperative Bank', 'NBFC-MFI', 'Small Finance Bank',
}
_VALID_APPROVAL_STATUSES = {'pending', 'approved', 'rejected', 'needs_update'}
_VALID_EXTRACTION_STATUSES = {'pending', 'success', 'partial', 'failed'}


def validate_rows(rows: List[Dict]) -> List[Dict]:
    """
    Filter out rows that would violate DB CHECK constraints.
    Logs a warning per rejected row. Returns the clean subset.
    """
    clean = []
    for row in rows:
        name = row.get('company_name', '')

        # company_type must be in the canonical set
        ct = row.get('company_type')
        if ct and ct not in _VALID_COMPANY_TYPES:
            logging.warning(f"  Skipping '{name}': invalid company_type '{ct}'")
            continue

        # quality_score must be 0–1
        qs = row.get('quality_score')
        if qs is not None:
            try:
                qs_f = float(qs)
                if not (0.0 <= qs_f <= 1.0):
                    logging.warning(f"  '{name}': quality_score {qs_f} out of range — clamping")
                    row['quality_score'] = max(0.0, min(1.0, qs_f))
            except (TypeError, ValueError):
                row['quality_score'] = None

        # extraction_status must be valid if present
        es = row.get('extraction_status')
        if es and es not in _VALID_EXTRACTION_STATUSES:
            logging.warning(f"  '{name}': invalid extraction_status '{es}' — setting 'success'")
            row['extraction_status'] = 'success'

        # aum_crores must be non-negative
        aum = row.get('aum_crores')
        if aum is not None and float(aum) < 0:
            logging.warning(f"  '{name}': negative aum_crores — clearing")
            row['aum_crores'] = None

        # ticket_size_min <= ticket_size_max
        ts_min = row.get('ticket_size_min')
        ts_max = row.get('ticket_size_max')
        if ts_min is not None and ts_max is not None:
            try:
                if float(ts_min) > float(ts_max):
                    logging.warning(f"  '{name}': ticket_size_min > max — swapping")
                    row['ticket_size_min'], row['ticket_size_max'] = ts_max, ts_min
            except (TypeError, ValueError):
                pass

        # rbi_registration_number must contain a digit (all real RBI reg nos do)
        # Garbage fragments like 'PORATE' cause unique-key conflicts; null them out
        rrn = row.get('rbi_registration_number')
        if rrn and not any(c.isdigit() for c in str(rrn)):
            logging.warning(f"  '{name}': invalid rbi_registration_number '{rrn}' — clearing")
            row['rbi_registration_number'] = None

        clean.append(row)

    # Deduplicate rbi_registration_number — unique index only covers non-NULL values
    seen_rrn: set = set()
    for row in clean:
        rrn = row.get('rbi_registration_number')
        if rrn:
            if rrn in seen_rrn:
                logging.warning(f"  '{row.get('company_name')}': duplicate rbi_registration_number '{rrn}' — clearing")
                row['rbi_registration_number'] = None
            else:
                seen_rrn.add(rrn)

    skipped = len(rows) - len(clean)
    if skipped:
        logging.warning(f"Validation: dropped {skipped} rows before upload")
    return clean


def upsert_lenders(conn, rows: List[Dict], dry_run: bool = False) -> Dict[str, int]:
    """
    Upsert rows into lenders table.
    ON CONFLICT (company_name) → UPDATE all columns EXCEPT approval_status.
    Returns: {inserted, updated, total}
    """
    if not rows:
        return {'inserted': 0, 'updated': 0, 'total': 0}

    if dry_run:
        logging.info(f"DRY RUN: would upsert {len(rows)} lenders")
        return {'inserted': 0, 'updated': 0, 'total': len(rows)}

    # Build UPDATE SET clause for all columns except company_name and approval_status
    update_cols = [c for c in UPSERT_COLS if c != 'company_name']

    # Handle last_scraped_at specially (use DB NOW())
    set_parts = []
    for col in update_cols:
        if col == 'last_scraped_at':
            set_parts.append(f"{col} = NOW()")
        else:
            set_parts.append(f"{col} = EXCLUDED.{col}")
    set_clause = ',\n                '.join(set_parts)

    sql = f"""
        INSERT INTO lenders ({', '.join(UPSERT_COLS)})
        VALUES %s
        ON CONFLICT (company_name) DO UPDATE SET
                {set_clause}
        RETURNING id, company_name,
                  (xmax = 0) AS inserted
    """

    # Build value tuples (exclude last_scraped_at from positional — use NOW() literal)
    insert_cols = [c for c in UPSERT_COLS if c != 'last_scraped_at']
    insert_cols_with_ts = insert_cols + ['last_scraped_at']

    def make_tuple(row: Dict):
        vals = [row.get(c) for c in insert_cols]
        vals.append(None)  # placeholder for last_scraped_at (overridden by NOW() in SQL)
        return tuple(vals)

    # Build SQL with NOW() for last_scraped_at position
    col_list = ', '.join(insert_cols) + ', last_scraped_at'
    placeholder = '(' + ', '.join(['%s'] * len(insert_cols)) + ', NOW())'

    inserted = 0
    updated  = 0
    id_map   = {}  # company_name → db_id

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Use execute_values for bulk insert
        sql_bulk = f"""
            INSERT INTO lenders ({col_list})
            VALUES %s
            ON CONFLICT (company_name) DO UPDATE SET
                    {set_clause}
            RETURNING id, company_name, (xmax = 0) AS was_inserted
        """
        template = '(' + ', '.join(['%s'] * len(insert_cols)) + ', NOW())'
        tuples = [tuple(row.get(c) for c in insert_cols) for row in rows]

        results = execute_values(cur, sql_bulk, tuples, template=template, page_size=100, fetch=True)
        for r in results:
            id_map[r['company_name']] = r['id']
            if r['was_inserted']:
                inserted += 1
            else:
                updated += 1

    conn.commit()
    return {'inserted': inserted, 'updated': updated, 'total': inserted + updated, 'id_map': id_map}


def rewrite_csv_with_db_ids(path: Path, id_map: Dict[str, int]):
    """Rewrite the CSV replacing id column with actual DB IDs."""
    if not path.exists() or not id_map:
        return

    rows = []
    with open(path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            name = row.get('company_name', '').strip()
            if name in id_map:
                row['id'] = str(id_map[name])
            rows.append(row)

    # Ensure 'id' is first column
    if 'id' not in fieldnames:
        fieldnames = ['id'] + list(fieldnames)

    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    logging.info(f"Rewrote {path.name} with {len(id_map)} DB IDs")


def main():
    parser = argparse.ArgumentParser(description='Upload lender CSVs to Supabase')
    parser.add_argument('--rbi-only',  action='store_true')
    parser.add_argument('--nbfc-only', action='store_true')
    parser.add_argument('--dry-run',   action='store_true')
    args = parser.parse_args()

    if not DATABASE_URL:
        print('ERROR: DATABASE_URL not set in .env')
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)

    all_rows: List[Dict] = []
    sources: list = []

    if not args.nbfc_only:
        rbi_rows = csv_to_rows(RBI_CSV)
        if rbi_rows:
            all_rows.extend(rbi_rows)
            sources.append(('RBI', RBI_CSV, len(rbi_rows)))

    if not args.rbi_only:
        nbfc_rows = csv_to_rows(NBFC_CSV, default_company_type='NBFC')
        if nbfc_rows:
            all_rows.extend(nbfc_rows)
            sources.append(('NBFC', NBFC_CSV, len(nbfc_rows)))

    if not all_rows:
        print('No rows to upload. Run extraction scripts first.')
        sys.exit(0)

    print(f'\nUploading {len(all_rows)} lenders...')
    for src, path, count in sources:
        print(f'  {src}: {count} rows from {path.name}')

    all_rows = validate_rows(all_rows)
    print(f'  After validation: {len(all_rows)} rows')

    stats = upsert_lenders(conn, all_rows, dry_run=args.dry_run)
    conn.close()

    if not args.dry_run:
        id_map = stats.get('id_map', {})
        for _, path, _ in sources:
            rewrite_csv_with_db_ids(path, id_map)

    print(f'\nDone.')
    print(f'  Inserted : {stats["inserted"]}')
    print(f'  Updated  : {stats["updated"]}')
    print(f'  Total    : {stats["total"]}')
    if args.dry_run:
        print('  (DRY RUN — no changes made)')


if __name__ == '__main__':
    main()
