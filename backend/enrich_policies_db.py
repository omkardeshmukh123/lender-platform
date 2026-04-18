"""
enrich_policies_db.py
=====================
Policy enrichment pipeline that reads directly from the DB.

Unlike run_policy_extraction.py (which needs a CSV input and uses Gemini),
this script:
  - Pulls approved lenders from Supabase
  - Runs the policy scraper (BankBazaar → PaisaBazaar → own website)
  - Upserts policies back to the DB (approval_status = 'pending')
  - Is fully resumable via checkpoint

Usage:
    python enrich_policies_db.py                     # all approved lenders
    python enrich_policies_db.py --limit 50          # first 50 lenders
    python enrich_policies_db.py --only "Kogta"      # name substring match
    python enrich_policies_db.py --min-score 0.0     # completeness threshold to process
    python enrich_policies_db.py --force             # re-enrich even if policies exist
    python enrich_policies_db.py --dry-run           # scrape but don't write to DB
    python enrich_policies_db.py --reset-checkpoint  # clear checkpoint and restart

Env vars required:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY

Optional:
    FIRECRAWL_API_KEY   (enables Firecrawl fallback for missing fields)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

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

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

# ── config ────────────────────────────────────────────────────────────────────
SUPABASE_URL  = os.getenv('SUPABASE_URL', '').strip()
SUPABASE_KEY  = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()
FIRECRAWL_KEY = os.getenv('FIRECRAWL_API_KEY', '').strip()

CHECKPOINT_FILE = Path(__file__).parent / '.enrich_policies_checkpoint.json'
RATE_DELAY      = 2.0   # seconds between lenders
BATCH_SIZE      = 50    # policies upserted per batch

_DB_POLICY_COLS = {
    'lender_id', 'product_name', 'loan_type',
    'loan_amount_min', 'loan_amount_max',
    'credit_score_min', 'credit_score_max',
    'min_age', 'max_age', 'employment_types',
    'min_monthly_income', 'min_annual_turnover', 'min_business_vintage',
    'interest_rate_min', 'interest_rate_max',
    'tenure_min', 'tenure_max', 'processing_fee', 'prepayment_allowed',
    'collateral_required', 'collateral_types',
    'eligible_states', 'eligibility_notes',
    'completeness_score', 'data_source', 'source_url',
    'approval_status', 'rates_as_of',
}
_ARRAY_COLS = {'employment_types', 'collateral_types', 'eligible_states'}


# ── Supabase client ───────────────────────────────────────────────────────────

def _get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set in .env")
        sys.exit(1)
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        log.error("supabase-py not installed. Run: pip install supabase")
        sys.exit(1)


# ── checkpoint helpers ────────────────────────────────────────────────────────

def _load_checkpoint() -> set:
    if CHECKPOINT_FILE.exists():
        try:
            data = json.loads(CHECKPOINT_FILE.read_text())
            return set(data.get('done', []))
        except Exception:
            return set()
    return set()


def _save_checkpoint(done: set) -> None:
    CHECKPOINT_FILE.write_text(json.dumps({'done': list(done)}, indent=2))


# ── policy → DB row ───────────────────────────────────────────────────────────

def _completeness(p: Dict[str, Any]) -> float:
    numeric_fields = [
        'loan_amount_min', 'loan_amount_max',
        'interest_rate_min', 'interest_rate_max',
        'tenure_min', 'tenure_max',
        'credit_score_min', 'processing_fee',
    ]
    array_fields = ['employment_types', 'eligible_states']
    bool_fields  = ['collateral_required', 'prepayment_allowed']

    score = 0.0
    total = len(numeric_fields) + len(array_fields) + len(bool_fields)

    for f in numeric_fields:
        if p.get(f) is not None:
            score += 1.0
    for f in array_fields:
        if p.get(f):
            score += 1.0
    for f in bool_fields:
        if p.get(f) is not None:
            score += 1.0

    return round(score / total, 3) if total else 0.0


def _policy_to_db_row(policy_dict: Dict[str, Any], lender_id: int) -> Dict[str, Any]:
    row: Dict[str, Any] = {'lender_id': lender_id, 'approval_status': 'pending'}
    for k, v in policy_dict.items():
        if k not in _DB_POLICY_COLS:
            continue
        if k in _ARRAY_COLS:
            if isinstance(v, list):
                row[k] = v
            elif v and v not in ('[]', 'null', 'None', ''):
                try:
                    parsed = json.loads(v)
                    row[k] = parsed if isinstance(parsed, list) else []
                except Exception:
                    row[k] = []
            else:
                row[k] = []
        elif k == 'rates_as_of' and isinstance(v, date):
            row[k] = v.isoformat()
        else:
            row[k] = v

    row['completeness_score'] = _completeness(row)
    return row


# ── fetch lenders from DB ─────────────────────────────────────────────────────

def fetch_approved_lenders(
    supa,
    name_filter: Optional[str] = None,
    limit: Optional[int] = None,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """Return approved lenders with websites, optionally skipping those with policies."""
    query = (
        supa.table('lenders')
        .select('id, company_name, website, primary_loan_segments')
        .eq('approval_status', 'approved')
        .not_.is_('website', 'null')
    )
    if name_filter:
        query = query.ilike('company_name', f'%{name_filter}%')
    if limit:
        query = query.limit(limit)

    resp = query.execute()
    lenders = resp.data or []

    if not force:
        # Exclude lenders that already have at least one approved/pending policy
        lender_ids = [l['id'] for l in lenders]
        if lender_ids:
            existing = (
                supa.table('policies')
                .select('lender_id')
                .in_('lender_id', lender_ids)
                .execute()
            )
            enriched_ids = {r['lender_id'] for r in (existing.data or [])}
            lenders = [l for l in lenders if l['id'] not in enriched_ids]
            log.info(f"Skipping {len(enriched_ids)} lenders that already have policies (use --force to re-enrich)")

    log.info(f"Found {len(lenders)} lenders to enrich")
    return lenders


# ── existing policy completeness check ───────────────────────────────────────

def get_existing_completeness(supa, lender_id: int) -> Dict[str, float]:
    """Map loan_type → existing completeness_score for a lender."""
    resp = (
        supa.table('policies')
        .select('loan_type, completeness_score')
        .eq('lender_id', lender_id)
        .execute()
    )
    return {
        r['loan_type']: (r['completeness_score'] or 0.0)
        for r in (resp.data or [])
    }


# ── upsert policies ───────────────────────────────────────────────────────────

def upsert_policies(supa, rows: List[Dict[str, Any]], dry_run: bool) -> int:
    if dry_run or not rows:
        return len(rows)

    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        try:
            supa.table('policies').upsert(
                batch,
                on_conflict='lender_id,product_name,loan_type',
            ).execute()
            total += len(batch)
        except Exception as exc:
            log.error(f"Upsert failed for batch starting at {i}: {exc}")
    return total


# ── main pipeline ─────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    from scraper.policy_scraper import scrape_policies

    supa      = _get_supabase()
    done      = _load_checkpoint()
    today     = date.today().isoformat()

    lenders = fetch_approved_lenders(
        supa,
        name_filter=args.only,
        limit=args.limit,
        force=args.force,
    )

    if not lenders:
        log.info("Nothing to enrich. Use --force to re-enrich lenders that already have policies.")
        return

    total_policies = 0
    total_lenders  = 0
    errors         = 0

    for idx, lender in enumerate(lenders, 1):
        lender_id   = lender['id']
        name        = lender['company_name']
        website     = lender.get('website') or ''
        raw_segs    = lender.get('primary_loan_segments') or []

        # Parse loan segments (may be JSON string or list from Supabase)
        if isinstance(raw_segs, str):
            try:
                raw_segs = json.loads(raw_segs)
            except Exception:
                raw_segs = []
        loan_types = raw_segs if isinstance(raw_segs, list) else []

        lender_key = str(lender_id)
        if lender_key in done:
            log.info(f"[{idx}/{len(lenders)}] SKIP (checkpoint): {name}")
            continue

        if not website:
            log.warning(f"[{idx}/{len(lenders)}] SKIP (no website): {name}")
            done.add(lender_key)
            _save_checkpoint(done)
            continue

        log.info(f"[{idx}/{len(lenders)}] Scraping: {name} ({website})")

        try:
            policies = scrape_policies(
                lender_name=name,
                website=website,
                loan_types=loan_types or None,
                firecrawl_key=FIRECRAWL_KEY or None,
            )
        except Exception as exc:
            log.error(f"  Scraper error for {name}: {exc}")
            errors += 1
            done.add(lender_key)
            _save_checkpoint(done)
            time.sleep(RATE_DELAY)
            continue

        if not policies:
            log.info(f"  No policies found for {name}")
            done.add(lender_key)
            _save_checkpoint(done)
            time.sleep(RATE_DELAY)
            continue

        # Get existing completeness so we only overwrite if we do better
        existing = get_existing_completeness(supa, lender_id)

        rows = []
        for p in policies:
            p_dict = {
                k: getattr(p, k)
                for k in vars(p)
                if not k.startswith('_')
            }
            p_dict['rates_as_of'] = today
            row = _policy_to_db_row(p_dict, lender_id)

            # Only upsert if new data is more complete
            lt = row.get('loan_type', '')
            existing_score = existing.get(lt, -1.0)
            new_score      = row.get('completeness_score', 0.0)

            if existing_score >= 0 and new_score < existing_score and not args.force:
                log.info(f"  SKIP policy '{lt}': existing {existing_score:.2f} >= new {new_score:.2f}")
                continue

            rows.append(row)

        if rows:
            n = upsert_policies(supa, rows, args.dry_run)
            total_policies += n
            log.info(f"  {'[DRY] Would upsert' if args.dry_run else 'Upserted'} {n} policies "
                     f"(avg completeness: {sum(r.get('completeness_score',0) for r in rows)/len(rows):.2f})")
        else:
            log.info(f"  No improved policies to upsert for {name}")

        done.add(lender_key)
        _save_checkpoint(done)
        total_lenders += 1

        if idx < len(lenders):
            time.sleep(RATE_DELAY)

    log.info("=" * 60)
    log.info(f"Done. Lenders processed: {total_lenders} | Policies upserted: {total_policies} | Errors: {errors}")
    if args.dry_run:
        log.info("DRY RUN — no changes written to DB")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Enrich policies from DB lenders')
    parser.add_argument('--only',   type=str,  default=None, help='Filter lenders by name substring')
    parser.add_argument('--limit',  type=int,  default=None, help='Max number of lenders to process')
    parser.add_argument('--force',  action='store_true',     help='Re-enrich even if policies already exist')
    parser.add_argument('--dry-run', action='store_true',    help='Scrape but do not write to DB')
    parser.add_argument('--reset-checkpoint', action='store_true', help='Clear checkpoint and restart from beginning')
    args = parser.parse_args()

    if args.reset_checkpoint and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        log.info("Checkpoint cleared")

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    run(args)


if __name__ == '__main__':
    main()
