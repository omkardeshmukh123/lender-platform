"""
audit_hallucinations.py
========================
Scans all approved lenders and policies in the DB and nulls out values
that are statistically impossible or outside the guardrails' own validated
range bounds. Zero hallucination = only values that could plausibly be true
for Indian NBFCs/banks remain in the database.

What gets nulled:
  POLICIES:
    interest_rate_min/max  — outside 3–60% p.a., or below loan-type floor/above ceiling
    loan_amount_min/max    — negative, zero, or above ₹10,000 Cr (1M Lakhs) — unrealistic
    tenure_min/max         — outside 1–360 months (1 month – 30 years)
    credit_score_min/max   — outside 300–900 (CIBIL range)
    processing_fee         — above 10% (RBI guideline cap area)
    min_age/max_age        — outside 18–80 years
    loan_amount_min > max  — min/max inversion
    interest_rate_min > max
    tenure_min > max

  LENDERS:
    aum_crores             — negative or > ₹30 lakh Cr (entire Indian banking system AUM)
    established_year       — before 1850 or after current year
    employee_count         — negative or > 500,000
    branch_count           — negative or > 100,000
    quality_score          — outside 0–1

  GEMINI-ONLY numeric fields in policies:
    If data_source = 'gemini' AND completeness_score < 0.2:
    null out interest_rate_min/max and loan_amount_min/max
    (Gemini is unreliable for specific numbers without structured page evidence)

Usage:
    python audit_hallucinations.py --dry-run     # report only
    python audit_hallucinations.py               # apply nulls
    python audit_hallucinations.py --policies    # policies only
    python audit_hallucinations.py --lenders     # lenders only
"""

from __future__ import annotations

import argparse
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
                    _k, _sep, _rest = _line.partition('=')
                    _k = _k.strip(); _v = _rest.strip().strip('"').strip("'")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v

from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()

# ── Same bounds as guardrails.py ──────────────────────────────────────────────

RATE_MIN   = 3.0
RATE_MAX   = 60.0
TENURE_MIN = 1       # months
TENURE_MAX = 360     # months (30 years)
CIBIL_MIN  = 300
CIBIL_MAX  = 900
AGE_MIN    = 18
AGE_MAX    = 80
FEE_MAX    = 10.0    # % processing fee — RBI guideline area
AMOUNT_MAX = 1_000_000  # Lakhs = ₹10,000 Cr — no single retail policy exceeds this

# Per-loan-type interest rate ranges (from guardrails.py _LOAN_TYPE_RATE_RANGES)
LOAN_TYPE_RATE: Dict[str, Tuple[float, float]] = {
    'Home Loan':               (6.5,  15.0),
    'Loan Against Property':   (7.0,  18.0),
    'Vehicle Loan':            (7.0,  20.0),
    'Two Wheeler Loan':        (8.0,  28.0),
    'EV Loan':                 (7.0,  18.0),
    'Education Loan':          (7.0,  15.0),
    'Gold Loan':               (7.0,  26.0),
    'Personal Loan':           (10.0, 40.0),
    'MSME Loan':               (9.0,  30.0),
    'Business Loan':           (9.0,  36.0),
    'Working Capital':         (9.0,  36.0),
    'Agriculture Loan':        (4.0,  18.0),
    'Micro Loan':              (12.0, 36.0),
    'Microfinance':            (18.0, 36.0),
    'Consumer Durable Loan':   (10.0, 30.0),
    'Supply Chain Finance':    (8.0,  24.0),
    'Rural Loan':              (7.0,  26.0),
    'Credit Card':             (24.0, 48.0),
}

CURRENT_YEAR = datetime.now().year

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

# ── Helpers ───────────────────────────────────────────────────────────────────

def _out_of_range(val: Any, lo: float, hi: float) -> bool:
    if val is None:
        return False
    try:
        return not (lo <= float(val) <= hi)
    except (TypeError, ValueError):
        return False


def _inverted(a: Any, b: Any) -> bool:
    """True if a > b (min/max inversion)."""
    if a is None or b is None:
        return False
    try:
        return float(a) > float(b)
    except (TypeError, ValueError):
        return False


# ── Policy audit ──────────────────────────────────────────────────────────────

class PolicyStats:
    total = 0
    flagged = 0
    nulled: Dict[str, int] = {}

    def record(self, field: str):
        self.nulled[field] = self.nulled.get(field, 0) + 1
        self.flagged += 1

    def report(self):
        log.info("── Policy audit ──────────────────────────────")
        log.info(f"  Total scanned: {self.total}")
        log.info(f"  Policies with issues: {self.flagged}")
        for field, count in sorted(self.nulled.items(), key=lambda x: -x[1]):
            log.info(f"  {field}: {count} values nulled")


def audit_policies(supa, dry_run: bool) -> PolicyStats:
    stats = PolicyStats()
    page  = 0
    PAGE  = 200

    while True:
        resp = (
            supa.table('policies')
            .select('id, loan_type, data_source, completeness_score, '
                    'interest_rate_min, interest_rate_max, '
                    'loan_amount_min, loan_amount_max, '
                    'tenure_min, tenure_max, '
                    'credit_score_min, credit_score_max, '
                    'processing_fee, min_age, max_age')
            .eq('approval_status', 'approved')
            .range(page * PAGE, (page + 1) * PAGE - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break

        for row in rows:
            stats.total += 1
            pid       = row['id']
            loan_type = row.get('loan_type') or ''
            source    = row.get('data_source') or ''
            comp      = row.get('completeness_score') or 0.0
            nulls: Dict[str, Any] = {}

            def flag(field: str, reason: str):
                log.warning(f"  policy {pid} [{loan_type}]: NULL {field} — {reason}")
                nulls[field] = None
                stats.record(field)

            # ── Interest rates ─────────────────────────────────
            ir_min = row.get('interest_rate_min')
            ir_max = row.get('interest_rate_max')

            if _out_of_range(ir_min, RATE_MIN, RATE_MAX):
                flag('interest_rate_min', f'{ir_min}% outside {RATE_MIN}–{RATE_MAX}%')
                ir_min = None
            if _out_of_range(ir_max, RATE_MIN, RATE_MAX):
                flag('interest_rate_max', f'{ir_max}% outside {RATE_MIN}–{RATE_MAX}%')
                ir_max = None
            if _inverted(ir_min, ir_max):
                flag('interest_rate_min', f'min {ir_min} > max {ir_max} — inversion')
                ir_min = None

            # Per-loan-type rate ceiling (15% tolerance, matching guardrails)
            lt_range = LOAN_TYPE_RATE.get(loan_type)
            if lt_range:
                lt_lo, lt_hi = lt_range
                if ir_min is not None and float(ir_min) < lt_lo * 0.8:
                    flag('interest_rate_min',
                         f'{ir_min}% far below {loan_type} floor {lt_lo}% — likely hallucinated')
                if ir_max is not None and float(ir_max) > lt_hi * 1.20:
                    flag('interest_rate_max',
                         f'{ir_max}% significantly above {loan_type} ceiling {lt_hi}% — likely hallucinated')

            # ── Loan amounts ───────────────────────────────────
            la_min = row.get('loan_amount_min')
            la_max = row.get('loan_amount_max')

            if la_min is not None and float(la_min) <= 0:
                flag('loan_amount_min', f'{la_min} Lakhs ≤ 0')
                la_min = None
            if la_max is not None and float(la_max) <= 0:
                flag('loan_amount_max', f'{la_max} Lakhs ≤ 0')
                la_max = None
            if la_min is not None and float(la_min) > AMOUNT_MAX:
                flag('loan_amount_min', f'{la_min} Lakhs > max {AMOUNT_MAX}')
                la_min = None
            if la_max is not None and float(la_max) > AMOUNT_MAX:
                flag('loan_amount_max', f'{la_max} Lakhs > max {AMOUNT_MAX}')
                la_max = None
            if _inverted(la_min, la_max):
                flag('loan_amount_min', f'min {la_min} > max {la_max} — inversion')

            # ── Tenure ────────────────────────────────────────
            if _out_of_range(row.get('tenure_min'), TENURE_MIN, TENURE_MAX):
                flag('tenure_min', f'{row["tenure_min"]} months outside {TENURE_MIN}–{TENURE_MAX}')
            if _out_of_range(row.get('tenure_max'), TENURE_MIN, TENURE_MAX):
                flag('tenure_max', f'{row["tenure_max"]} months outside {TENURE_MIN}–{TENURE_MAX}')
            if _inverted(row.get('tenure_min'), row.get('tenure_max')):
                flag('tenure_min', f'min {row["tenure_min"]} > max {row["tenure_max"]} — inversion')

            # ── Credit score ───────────────────────────────────
            if _out_of_range(row.get('credit_score_min'), CIBIL_MIN, CIBIL_MAX):
                flag('credit_score_min', f'{row["credit_score_min"]} outside CIBIL {CIBIL_MIN}–{CIBIL_MAX}')
            if _out_of_range(row.get('credit_score_max'), CIBIL_MIN, CIBIL_MAX):
                flag('credit_score_max', f'{row["credit_score_max"]} outside CIBIL {CIBIL_MIN}–{CIBIL_MAX}')

            # ── Processing fee ─────────────────────────────────
            if _out_of_range(row.get('processing_fee'), 0, FEE_MAX):
                flag('processing_fee', f'{row["processing_fee"]}% > {FEE_MAX}% cap')

            # ── Age ────────────────────────────────────────────
            if _out_of_range(row.get('min_age'), AGE_MIN, AGE_MAX):
                flag('min_age', f'{row["min_age"]} outside {AGE_MIN}–{AGE_MAX}')
            if _out_of_range(row.get('max_age'), AGE_MIN, AGE_MAX):
                flag('max_age', f'{row["max_age"]} outside {AGE_MIN}–{AGE_MAX}')

            # ── Gemini-only low-confidence numeric fields ──────
            # If source is gemini only and completeness is very low, the specific
            # numeric values are unreliable — blank them rather than mislead users
            if 'gemini' in source and 'bankbazaar' not in source and 'paisabazaar' not in source:
                if comp < 0.2:
                    for f in ('interest_rate_min', 'interest_rate_max',
                              'loan_amount_min', 'loan_amount_max'):
                        if row.get(f) is not None and f not in nulls:
                            flag(f, f'Gemini-only source with completeness {comp:.0%} — unreliable number')

            if nulls and not dry_run:
                try:
                    supa.table('policies').update(nulls).eq('id', pid).execute()
                except Exception as exc:
                    log.error(f"  Failed to update policy {pid}: {exc}")

        page += 1

    return stats


# ── Lender audit ──────────────────────────────────────────────────────────────

class LenderStats:
    total = 0
    flagged = 0
    nulled: Dict[str, int] = {}

    def record(self, field: str):
        self.nulled[field] = self.nulled.get(field, 0) + 1
        self.flagged += 1

    def report(self):
        log.info("── Lender audit ──────────────────────────────")
        log.info(f"  Total scanned: {self.total}")
        log.info(f"  Lenders with issues: {self.flagged}")
        for field, count in sorted(self.nulled.items(), key=lambda x: -x[1]):
            log.info(f"  {field}: {count} values nulled")


def audit_lenders(supa, dry_run: bool) -> LenderStats:
    stats = LenderStats()
    page  = 0
    PAGE  = 200

    while True:
        resp = (
            supa.table('lenders')
            .select('id, company_name, company_type, aum_crores, '
                    'established_year, employee_count, branch_count, quality_score')
            .eq('approval_status', 'approved')
            .range(page * PAGE, (page + 1) * PAGE - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break

        for row in rows:
            stats.total += 1
            lid  = row['id']
            name = row.get('company_name', f'id={lid}')
            nulls: Dict[str, Any] = {}

            def flag(field: str, reason: str):
                log.warning(f"  lender {lid} [{name}]: NULL {field} — {reason}")
                nulls[field] = None
                stats.record(field)

            # AUM
            aum = row.get('aum_crores')
            if aum is not None:
                if float(aum) <= 0:
                    flag('aum_crores', f'{aum} Cr ≤ 0')
                elif float(aum) > 30_000_000:
                    # ₹30 lakh Cr is larger than entire RBI system assets — impossible
                    flag('aum_crores', f'{aum} Cr > ₹30 lakh Cr system limit')

            # Established year
            yr = row.get('established_year')
            if yr is not None and not (1850 <= int(yr) <= CURRENT_YEAR):
                flag('established_year', f'{yr} outside 1850–{CURRENT_YEAR}')

            # Employee count
            emp = row.get('employee_count')
            if emp is not None and not (1 <= int(emp) <= 500_000):
                flag('employee_count', f'{emp} outside 1–500,000')

            # Branch count
            br = row.get('branch_count')
            if br is not None and not (1 <= int(br) <= 100_000):
                flag('branch_count', f'{br} outside 1–100,000')

            # Quality score
            qs = row.get('quality_score')
            if qs is not None and not (0.0 <= float(qs) <= 1.0):
                flag('quality_score', f'{qs} outside 0–1')

            if nulls and not dry_run:
                try:
                    supa.table('lenders').update(nulls).eq('id', lid).execute()
                except Exception as exc:
                    log.error(f"  Failed to update lender {lid}: {exc}")

        page += 1

    return stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Audit and null hallucinated DB values')
    parser.add_argument('--dry-run',  action='store_true', help='Report only, no writes')
    parser.add_argument('--policies', action='store_true', help='Audit policies only')
    parser.add_argument('--lenders',  action='store_true', help='Audit lenders only')
    args = parser.parse_args()

    # Default: both
    do_policies = args.policies or not args.lenders
    do_lenders  = args.lenders  or not args.policies

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    supa = _get_supabase()

    if args.dry_run:
        log.info("DRY RUN — no changes will be written")

    if do_lenders:
        ls = audit_lenders(supa, args.dry_run)
        ls.report()

    if do_policies:
        ps = audit_policies(supa, args.dry_run)
        ps.report()

    log.info("=" * 50)
    if args.dry_run:
        log.info("DRY RUN complete — rerun without --dry-run to apply")
    else:
        log.info("Audit complete")


if __name__ == '__main__':
    main()
