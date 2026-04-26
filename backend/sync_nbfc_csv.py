"""
sync_nbfc_csv.py
================
Phase 1a: Sync authoritative data from nbfc_names.csv and the RBI NBFC Excel
into the lenders table. No external API calls. Runs in ~60 seconds.

Sources (run in order, never overwrite non-null existing values):

  Source A — RBI NBFC Excel (data/input/rbi_nbfc_list.xlsx)
    9,188 NBFCs registered with RBI (as on Dec 2025).
    Columns used: NBFC Name, Classification, CIN, Layer.
    From CIN we decode for free: hq_state, established_year, is_listed.
    Fills: cin, rbi_category, regulatory_tier, hq_state, established_year,
           is_listed, mca21_status='enriched'

  Source B — nbfc_names.csv (data/input/nbfc_names.csv)
    934 rows from our internal list. Some have real CoR registration numbers,
    emails, phones, and website URLs containing CINs.
    Fills: rbi_registration_number, email, phone, website,
           cin (from URL), hq_state, established_year (from URL-extracted CIN)

Usage:
    python backend/sync_nbfc_csv.py               # run both sources
    python backend/sync_nbfc_csv.py --dry-run     # preview only, no DB writes
    python backend/sync_nbfc_csv.py --skip-rbi-excel  # skip Source A
    python backend/sync_nbfc_csv.py --skip-nbfc-csv   # skip Source B
    python backend/sync_nbfc_csv.py --approve     # enrich + auto-approve
    python backend/sync_nbfc_csv.py --min-score 0.45
    python backend/sync_nbfc_csv.py --limit 50    # first N rows from each source
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

# ── env ───────────────────────────────────────────────────────
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

DATABASE_URL  = os.environ.get('DATABASE_URL', '')
ROOT          = Path(__file__).parent.parent
NBFC_CSV      = ROOT / 'data' / 'input' / 'nbfc_names.csv'
RBI_NBFC_XLSX = ROOT / 'data' / 'input' / 'rbi_nbfc_list.xlsx'

# RBI Layer → regulatory_tier mapping
_RBI_LAYER_MAP: Dict[str, str] = {
    'upper':  'ND-UL',
    'middle': 'ND-ML',
    'base':   'ND-BL',
}

# RBI Classification → rbi_category mapping
_RBI_CLASS_MAP: Dict[str, str] = {
    'p2p':    'NBFC-P2P',
    'mfi':    'NBFC-MFI',
    'ifc':    'NBFC-IFC',
    'factor': 'NBFC-Factor',
    'aa':     'NBFC-AA',
    'icc':    'NBFC-ICC',
    'cic':    'NBFC-ICC',
    'd':      'NBFC-D',
    'nd':     'NBFC-ND',
    'nd-si':  'NBFC-ND-SI',
    'arc':    'ARC',
}

# ── CIN parsing ───────────────────────────────────────────────
# CIN format: L/U + 5-digit NIC + 2-letter state + 4-digit year +
#             PLC/PTC/LLC/... + 6-digit seq
# L = Listed on stock exchange, U = Unlisted
_CIN_RE = re.compile(r'\b([LU]\d{5}[A-Z]{2}\d{4}[A-Z]{2,3}\d{6})\b')

# MCA state codes → canonical Indian state names
_MCA_STATE_CODES: Dict[str, str] = {
    'AN': 'Andaman and Nicobar Islands',
    'AP': 'Andhra Pradesh',
    'AR': 'Arunachal Pradesh',
    'AS': 'Assam',
    'BR': 'Bihar',
    'CH': 'Chandigarh',
    'CG': 'Chhattisgarh',
    'CT': 'Chhattisgarh',
    'DD': 'Dadra and Nagar Haveli',
    'DL': 'Delhi',
    'DN': 'Dadra and Nagar Haveli',
    'GA': 'Goa',
    'GJ': 'Gujarat',
    'HR': 'Haryana',
    'HP': 'Himachal Pradesh',
    'JK': 'Jammu & Kashmir',
    'JH': 'Jharkhand',
    'KA': 'Karnataka',
    'KL': 'Kerala',
    'LD': 'Lakshadweep',
    'MP': 'Madhya Pradesh',
    'MH': 'Maharashtra',
    'MN': 'Manipur',
    'ML': 'Meghalaya',
    'MZ': 'Mizoram',
    'NL': 'Nagaland',
    'OD': 'Odisha',
    'OR': 'Odisha',
    'PY': 'Puducherry',
    'PB': 'Punjab',
    'RJ': 'Rajasthan',
    'SK': 'Sikkim',
    'TN': 'Tamil Nadu',
    'TG': 'Telangana',
    'TR': 'Tripura',
    'UP': 'Uttar Pradesh',
    'UK': 'Uttarakhand',
    'UT': 'Uttarakhand',
    'WB': 'West Bengal',
}

# RBI CoR patterns (not placeholders like "ROW-123")
# CoR examples: "05.01915", "N.13.02437", "DNBS.PD.No.052/..."
_PLACEHOLDER_RE = re.compile(r'^ROW-\d+$', re.IGNORECASE)
_COR_RE = re.compile(
    r'^(?:'
    r'\d{2}\.\d{5}'                   # 05.01915
    r'|[A-Z]\.\d{2}\.\d{5}'          # N.13.02437
    r'|DNBS\b'                        # DNBS-based CoR
    r'|[A-Z]{1,4}-\d{3,}'            # e.g. B-13.02437
    r')',
    re.IGNORECASE,
)

# Quality fields (must match enrich_lenders.py for comparable scores)
_QUALITY_FIELDS = [
    'company_name', 'website', 'hq_state', 'company_type',
    'primary_loan_segments', 'operating_states', 'phone', 'email',
    'aum_crores', 'established_year', 'employee_count',
    'rbi_registration_number', 'rbi_category', 'operating_intensity', 'hq_location',
    'cin',
]


# ── CIN helpers ───────────────────────────────────────────────

def extract_cin(url: str) -> Optional[str]:
    """Extract a CIN from a URL string (vakilsearch, charteredone, etc.)."""
    if not url:
        return None
    m = _CIN_RE.search(url)
    return m.group(1) if m else None


def parse_cin(cin: str) -> Dict[str, Any]:
    """
    Decode a CIN into its components.
    Returns dict with: is_listed, state_code, state, year, company_class
    """
    if not cin or len(cin) < 15:
        return {}

    result: Dict[str, Any] = {}

    # First character: L=listed, U=unlisted
    listing_char = cin[0].upper()
    result['is_listed'] = (listing_char == 'L')

    # Characters 6–7 (index 6:8): state code
    state_code = cin[6:8].upper()
    result['state_code'] = state_code
    state = _MCA_STATE_CODES.get(state_code)
    if state:
        result['hq_state'] = state

    # Characters 8–11 (index 8:12): year of incorporation
    year_str = cin[8:12]
    try:
        yr = int(year_str)
        if 1850 <= yr <= datetime.now().year:
            result['established_year'] = yr
    except ValueError:
        pass

    return result


# ── RBI NBFC Excel loader ────────────────────────────────────

def load_rbi_nbfc_excel(path: Path) -> List[Dict]:
    """
    Parse the RBI NBFC Excel file into a list of normalised dicts.

    RBI Excel format (as of Dec 2025):
      Row 0: title row (skip)
      Row 1: headers — Sl. No. | NBFC Name | Regional Office |
                       Whether have CoR... | Classification |
                       Corporate Identification Number | Layer
      Row 2+: data

    Returns list of dicts with keys: name, cin, classification, layer, city
    """
    if not path.exists():
        log.warning('[rbi_excel] File not found: %s', path)
        return []

    try:
        import openpyxl
    except ImportError:
        log.error('[rbi_excel] openpyxl not installed: pip install openpyxl')
        return []

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        log.error('[rbi_excel] Cannot open %s: %s', path.name, exc)
        return []

    # Use the first sheet (List of NBFCs)
    ws = wb.worksheets[0]
    all_rows = [[str(c.value or '').strip() for c in row] for row in ws.iter_rows()]

    if len(all_rows) < 3:
        log.warning('[rbi_excel] Unexpected format — too few rows')
        return []

    # Find header row (contains 'NBFC Name' or 'Company Name')
    header_row_idx = None
    for i, row in enumerate(all_rows[:5]):
        if any('name' in str(c).lower() for c in row):
            header_row_idx = i
            break

    if header_row_idx is None:
        log.warning('[rbi_excel] Could not find header row')
        return []

    headers = [str(h).lower().strip() for h in all_rows[header_row_idx]]

    # Map column positions
    col: Dict[str, int] = {}
    for i, h in enumerate(headers):
        if ('nbfc name' in h or 'company name' in h or 'name' == h) and 'name' not in col:
            col['name'] = i
        elif 'identification number' in h or 'cin' == h:
            col['cin'] = i
        elif 'classif' in h:
            col['classification'] = i
        elif 'layer' in h:
            col['layer'] = i
        elif 'regional' in h or 'office' in h:
            col['city'] = i

    if 'name' not in col:
        log.warning('[rbi_excel] Could not map name column from headers: %s', headers[:8])
        return []

    log.info('[rbi_excel] Column map: %s', col)

    entries = []
    for row in all_rows[header_row_idx + 1:]:
        if len(row) <= max(col.values(), default=0):
            continue
        name = row[col['name']].strip()
        if not name or name.lower() in ('none', 'nan', ''):
            continue

        entry: Dict[str, str] = {'name': name}
        for field, idx in col.items():
            if field != 'name' and idx < len(row):
                entry[field] = row[idx].strip()

        entries.append(entry)

    log.info('[rbi_excel] Loaded %d NBFCs from %s', len(entries), path.name)
    return entries


def enrich_from_rbi_excel(rbi_row: Dict, db_record: Dict) -> Tuple[Dict, List[str]]:
    """
    Derive enrichment updates from a single RBI Excel row.
    Returns (updates_dict, list_of_filled_fields).
    """
    updates: Dict[str, Any] = {}
    filled:  List[str] = []

    def fill(field: str, value: Any):
        if not _is_empty(value) and _is_empty(db_record.get(field)):
            updates[field] = value
            filled.append(field)

    # CIN
    cin = rbi_row.get('cin', '').strip().upper()
    if _CIN_RE.search(cin):
        m = _CIN_RE.search(cin)
        cin = m.group(1) if m else cin
        fill('cin', cin)
        # Decode CIN → state, year, listing
        cin_data = parse_cin(cin)
        fill('established_year', cin_data.get('established_year'))
        fill('hq_state',         cin_data.get('hq_state'))
        if 'is_listed' in cin_data:
            fill('is_listed', cin_data['is_listed'])

        if 'cin' in filled:
            updates['mca21_status']      = 'enriched'
            updates['mca21_enriched_at'] = 'NOW()'

    # RBI category from Classification column
    class_raw = rbi_row.get('classification', '').strip().lower()
    # Try direct map first, then partial match
    rbi_cat = _RBI_CLASS_MAP.get(class_raw)
    if not rbi_cat:
        for key, val in _RBI_CLASS_MAP.items():
            if key in class_raw:
                rbi_cat = val
                break
    if rbi_cat:
        fill('rbi_category', rbi_cat)

    # Regulatory tier from Layer column
    layer_raw = rbi_row.get('layer', '').strip().lower()
    reg_tier = _RBI_LAYER_MAP.get(layer_raw)
    if reg_tier:
        fill('regulatory_tier', reg_tier)

    # HQ city from Regional Office
    city = rbi_row.get('city', '').strip()
    if city and city.lower() not in ('none', 'nan', ''):
        fill('hq_location', city)

    # Recompute quality score if anything changed
    if updates:
        preview = {**db_record, **updates}
        updates['quality_score'] = _compute_quality(preview)

    return updates, filled


# ── RBI CoR validation ────────────────────────────────────────

def is_real_cor(reg: str) -> bool:
    """Return True if reg looks like a real RBI Certificate of Registration."""
    if not reg or not reg.strip():
        return False
    reg = reg.strip()
    if _PLACEHOLDER_RE.match(reg):
        return False
    return bool(_COR_RE.match(reg))


# ── AUM parsing ───────────────────────────────────────────────

def parse_aum(aum_str: str) -> Optional[float]:
    """
    Parse AUM values from CSV (may be in crores, already numeric or text).
    Returns float in crores, or None.
    """
    if not aum_str or str(aum_str).strip() in ('', 'NULL', 'None', 'null'):
        return None
    try:
        return float(str(aum_str).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


# ── Name normalisation for fuzzy matching ─────────────────────

_STOP = {'limited', 'ltd', 'private', 'pvt', 'financial', 'finance',
         'capital', 'services', 'solutions', 'company', 'india', 'nbfc',
         'the', 'and', 'of', 'a', 'an', 'leasing', 'investments'}


def _normalise(name: str) -> str:
    n = name.lower()
    n = re.sub(r'[^\w\s]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def _sig_tokens(name: str) -> set:
    return {t for t in _normalise(name).split() if t not in _STOP and len(t) >= 3}


def name_similarity(a: str, b: str) -> float:
    ta, tb = _sig_tokens(a), _sig_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


# ── DB helpers ────────────────────────────────────────────────

def get_db():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL not set in .env')
    return psycopg2.connect(DATABASE_URL, connect_timeout=15)


def fetch_all_lenders(conn) -> List[Dict]:
    sql = """
        SELECT id, company_name, company_type, website,
               rbi_registration_number, rbi_category,
               hq_state, hq_location, aum_crores, email, phone,
               established_year, is_listed,
               cin, company_status, mca21_status,
               primary_loan_segments, operating_states,
               operating_intensity, employee_count,
               quality_score, approval_status
        FROM lenders
        WHERE company_type IN ('NBFC', 'NBFC-MFI', 'NBFC-D', 'NBFC-ND')
        ORDER BY company_name
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def _is_empty(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str) and val.strip() == '':
        return True
    if isinstance(val, list) and len(val) == 0:
        return True
    return False


def _compute_quality(record: Dict) -> float:
    present = sum(1 for f in _QUALITY_FIELDS if not _is_empty(record.get(f)))
    return round(present / len(_QUALITY_FIELDS), 3)


def apply_update(conn, lender_id: int, updates: Dict[str, Any],
                 dry_run: bool) -> str:
    """
    Apply updates to a lender row. Never overwrites non-null values.
    Returns 'updated', 'no_change', or 'dry_run'.
    """
    if not updates:
        return 'no_change'
    if dry_run:
        return 'dry_run'

    set_clauses = []
    values = []

    _float_fields = {'aum_crores', 'quality_score'}
    _int_fields   = {'established_year'}
    _bool_fields  = {'is_listed'}

    for col, val in updates.items():
        if val is None:
            continue
        if col in _float_fields:
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
        elif col in _int_fields:
            try:
                val = int(val)
            except (TypeError, ValueError):
                continue
        elif col in _bool_fields:
            val = bool(val)
        set_clauses.append(f'{col} = %s')
        values.append(val)

    if not set_clauses:
        return 'no_change'

    set_clauses.append('last_scraped_at = NOW()')
    sql = f"UPDATE lenders SET {', '.join(set_clauses)} WHERE id = %s"
    values.append(lender_id)

    with conn.cursor() as cur:
        cur.execute(sql, values)
    conn.commit()
    return 'updated'


def bulk_approve(conn, min_score: float, dry_run: bool) -> int:
    if dry_run:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM lenders
                WHERE approval_status != 'approved'
                AND quality_score >= %s
                AND hq_state IS NOT NULL
                AND primary_loan_segments IS NOT NULL
                AND array_length(primary_loan_segments, 1) > 0
            """, (min_score,))
            return cur.fetchone()[0]

    with conn.cursor() as cur:
        cur.execute("""
            UPDATE lenders
            SET approval_status = 'approved'
            WHERE approval_status != 'approved'
            AND quality_score >= %s
            AND hq_state IS NOT NULL
            AND primary_loan_segments IS NOT NULL
            AND array_length(primary_loan_segments, 1) > 0
        """, (min_score,))
        count = cur.rowcount
    conn.commit()
    return count


# ── CSV loader ────────────────────────────────────────────────

def load_nbfc_csv(path: Path) -> List[Dict]:
    """Load nbfc_names.csv into a list of dicts."""
    rows = []
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k.strip(): (v.strip() if v else '') for k, v in row.items()})
    log.info('Loaded %d rows from %s', len(rows), path.name)
    return rows


# ── Enrichment logic per NBFC ─────────────────────────────────

def enrich_from_csv_row(csv_row: Dict, db_record: Dict) -> Tuple[Dict, List[str]]:
    """
    Derive enrichment updates from a single CSV row.
    Returns (updates_dict, list_of_filled_fields).
    Updates dict: only fields that are currently empty in DB and have CSV data.
    """
    updates: Dict[str, Any] = {}
    filled: List[str] = []

    def fill(field: str, value: Any):
        if not _is_empty(value) and _is_empty(db_record.get(field)):
            updates[field] = value
            filled.append(field)

    # 1. CIN from website URL
    website_url = csv_row.get('original_website', '') or csv_row.get('validated_urls', '')
    cin = extract_cin(website_url)
    if cin:
        fill('cin', cin)
        # Decode CIN for derived fields
        cin_data = parse_cin(cin)
        fill('established_year', cin_data.get('established_year'))
        fill('hq_state',         cin_data.get('hq_state'))
        if 'is_listed' in cin_data:
            fill('is_listed', cin_data['is_listed'])

        # mca21_status: mark enriched if we got CIN
        if 'cin' in filled:
            updates['mca21_status']    = 'enriched'
            updates['mca21_enriched_at'] = 'NOW()'  # handled specially in apply_update

    # 2. RBI CoR registration number
    reg = csv_row.get('registration_number', '')
    if is_real_cor(reg):
        fill('rbi_registration_number', reg)

    # 3. AUM
    aum = parse_aum(csv_row.get('aum', ''))
    if aum and aum > 0:
        fill('aum_crores', aum)
        # Recompute aum_category
        if 'aum_crores' in filled:
            cat = ('Micro' if aum < 500 else
                   'Small' if aum < 5000 else
                   'Mid'   if aum < 50000 else 'Large')
            updates['aum_category'] = cat
            filled.append('aum_category')

    # 4. Contact
    fill('email', csv_row.get('contact_email') or None)
    fill('phone', csv_row.get('contact_phone') or None)

    # 5. State from CSV state column (if not already filled from CIN)
    state_raw = csv_row.get('state', '').strip()
    if state_raw and state_raw.upper() not in ('NULL', 'NONE', ''):
        fill('hq_state', state_raw)

    # 6. Website (if DB is empty)
    website = csv_row.get('original_website', '').strip()
    if website and website.startswith('http'):
        fill('website', website)

    # 7. Recompute quality score if anything changed
    if updates:
        preview = {**db_record, **updates}
        updates['quality_score'] = _compute_quality(preview)

    return updates, filled


# ── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Sync RBI NBFC Excel + nbfc_names.csv into lenders table')
    parser.add_argument('--dry-run',        action='store_true', help='No DB writes')
    parser.add_argument('--limit',          type=int, default=None,
                        help='Process only first N rows from each source')
    parser.add_argument('--approve',        action='store_true',
                        help='Auto-approve lenders >= min-score after sync')
    parser.add_argument('--min-score',      type=float, default=0.45,
                        help='Quality score threshold for approval (default 0.45)')
    parser.add_argument('--threshold',      type=float, default=0.55,
                        help='Name similarity threshold for matching (default 0.55)')
    parser.add_argument('--skip-rbi-excel', action='store_true',
                        help='Skip RBI NBFC Excel (Source A)')
    parser.add_argument('--skip-nbfc-csv',  action='store_true',
                        help='Skip nbfc_names.csv (Source B)')
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error('DATABASE_URL not set in .env')
        sys.exit(1)

    if not NBFC_CSV.exists():
        log.error('CSV not found: %s', NBFC_CSV)
        sys.exit(1)

    conn = get_db()

    # Load all NBFCs from DB once (avoid N queries)
    log.info('Loading lenders from DB...')
    db_lenders = fetch_all_lenders(conn)
    log.info('DB: %d NBFCs loaded', len(db_lenders))

    # Load CSV
    csv_rows = load_nbfc_csv(NBFC_CSV)
    if args.limit:
        csv_rows = csv_rows[:args.limit]

    # Stats
    stats = {
        'matched':          0,
        'unmatched':        0,
        'updated':          0,
        'no_change':        0,
        'cin_found':        0,
        'rbi_cat_found':    0,
        'reg_tier_found':   0,
        'cor_found':        0,
        'aum_found':        0,
        'email_found':      0,
    }

    # ── Source A: RBI NBFC Excel ───────────────────────────────
    if not args.skip_rbi_excel:
        log.info('=== Source A: RBI NBFC Excel ===')
        rbi_rows = load_rbi_nbfc_excel(RBI_NBFC_XLSX)
        if args.limit:
            rbi_rows = rbi_rows[:args.limit]

        for i, rbi_row in enumerate(rbi_rows, 1):
            rbi_name = rbi_row.get('name', '').strip()
            if not rbi_name:
                continue

            # Fuzzy match to DB
            best_score = 0.0
            best_db    = None
            for db_rec in db_lenders:
                score = name_similarity(rbi_name, db_rec['company_name'])
                if score > best_score:
                    best_score = score
                    best_db = db_rec

            if best_score < args.threshold or not best_db:
                stats['unmatched'] += 1
                continue

            stats['matched'] += 1

            updates, filled = enrich_from_rbi_excel(rbi_row, best_db)
            if not updates:
                stats['no_change'] += 1
                continue

            if 'cin' in filled:          stats['cin_found']      += 1
            if 'rbi_category' in filled: stats['rbi_cat_found']  += 1
            if 'regulatory_tier' in filled: stats['reg_tier_found'] += 1

            if filled:
                log.info('[rbi %d] %-50s filled: %s',
                         i, best_db['company_name'][:50], ', '.join(filled))

            mca21_at = updates.pop('mca21_enriched_at', None)
            action = apply_update(conn, best_db['id'], updates, args.dry_run)
            stats[action] = stats.get(action, 0) + 1

            if mca21_at and not args.dry_run and action == 'updated':
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE lenders SET mca21_enriched_at = NOW() WHERE id = %s",
                        (best_db['id'],)
                    )
                conn.commit()

            # Update in-memory record so Source B sees updated values
            if action == 'updated' and not args.dry_run:
                best_db.update({k: v for k, v in updates.items()
                                if k not in ('quality_score',)})

        rbi_updated = stats.get('updated', 0)
        log.info('Source A done: %d matched, %d updated so far',
                 stats['matched'], rbi_updated)

    # ── Source B: nbfc_names.csv ────────────────────────────────
    if not args.skip_nbfc_csv:
        log.info('=== Source B: nbfc_names.csv ===')

        for i, csv_row in enumerate(csv_rows, 1):
            csv_name = csv_row.get('company_name', '').strip()
            if not csv_name:
                continue

            # Fuzzy match CSV name → DB record
            best_score = 0.0
            best_db    = None
            for db_rec in db_lenders:
                score = name_similarity(csv_name, db_rec['company_name'])
                if score > best_score:
                    best_score = score
                    best_db = db_rec

            if best_score < args.threshold or not best_db:
                log.debug('[%d] no match for "%s" (best=%.2f)', i, csv_name, best_score)
                stats['unmatched'] += 1
                continue

            stats['matched'] += 1
            log.debug('[%d/%d] "%s" → "%s" (%.2f)',
                      i, len(csv_rows), csv_name, best_db['company_name'], best_score)

            updates, filled = enrich_from_csv_row(csv_row, best_db)

            if not updates:
                stats['no_change'] += 1
                continue

            # Track what was filled
            if 'cin' in filled:            stats['cin_found']   += 1
            if 'rbi_registration_number' in filled: stats['cor_found'] += 1
            if 'aum_crores' in filled:     stats['aum_found']   += 1
            if 'email' in filled:          stats['email_found'] += 1

            if filled:
                log.info('[%d] %-55s filled: %s', i, best_db['company_name'][:55],
                         ', '.join(filled))

            # Handle mca21_enriched_at sentinel
            mca21_at = updates.pop('mca21_enriched_at', None)

            action = apply_update(conn, best_db['id'], updates, args.dry_run)
            stats[action] = stats.get(action, 0) + 1

            # Write mca21_enriched_at separately (uses NOW())
            if mca21_at and not args.dry_run and action == 'updated':
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE lenders SET mca21_enriched_at = NOW() WHERE id = %s",
                        (best_db['id'],)
                    )
                conn.commit()

    # Auto-approve
    newly_approved = 0
    if args.approve:
        log.info('Running auto-approve (min_score=%.2f)...', args.min_score)
        newly_approved = bulk_approve(conn, args.min_score, args.dry_run)

    conn.close()

    # Summary
    print(f'\n{"="*62}')
    print('NBFC SYNC COMPLETE')
    print(f'  Total updated        : {stats.get("updated", 0)}')
    print(f'  No change            : {stats.get("no_change", 0)}')
    print(f'  -- Fields filled --')
    print(f'  CIN extracted        : {stats["cin_found"]}')
    print(f'  RBI category synced  : {stats["rbi_cat_found"]}')
    print(f'  Regulatory tier      : {stats["reg_tier_found"]}')
    print(f'  RBI CoR synced       : {stats["cor_found"]}')
    print(f'  AUM synced           : {stats["aum_found"]}')
    print(f'  Email synced         : {stats["email_found"]}')
    if args.dry_run:
        print(f'  Would update         : {stats.get("dry_run", 0)}')
        print('  (DRY RUN - no DB writes)')
    if args.approve:
        print(f'  Newly approved       : {newly_approved}')
    print(f'{"="*62}')


if __name__ == '__main__':
    main()
