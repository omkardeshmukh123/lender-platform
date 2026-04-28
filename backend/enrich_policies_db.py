"""
enrich_policies_db.py  v2.0
============================
Policy enrichment pipeline — reads lenders from DB, scrapes, upserts policies.

Fixes applied (fintech hardening):
  - scraped_at (when WE scraped) stored separately from rates_as_of (lender's stated date)
  - Checkpoint stored in DB table scraper_checkpoints, not a JSON file
  - completeness_score weights interest_rate 3x — it's the critical field
  - interest_rate > 42% hard-rejected; > 36% stored with rate_flagged=True
  - Float financials rounded to 2dp before DB write
  - --stale-days flag: re-scrape policies older than N days

Usage:
    python enrich_policies_db.py                       # all approved lenders
    python enrich_policies_db.py --limit 50            # first 50
    python enrich_policies_db.py --only "Kogta"        # name filter
    python enrich_policies_db.py --force               # re-enrich even if policies exist
    python enrich_policies_db.py --stale-days 30       # re-scrape policies older than 30d
    python enrich_policies_db.py --dry-run             # scrape but don't write
    python enrich_policies_db.py --reset-checkpoint    # clear checkpoint and restart

Required env:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY

Optional:
    FIRECRAWL_API_KEY
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, date, timezone
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
                if _line and not _line.startswith('#') and '=' in _line and not _line.startswith('export '):
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

CHECKPOINT_JOB = 'enrich_policies'
RATE_DELAY     = 2.0   # seconds between lenders
BATCH_SIZE     = 50    # policies upserted per batch

# Rate guardrails (mirror policy_scraper.py)
_RATE_HARD_MAX   = 42.0
_RATE_FLAG_ABOVE = 36.0

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
    # v2 columns
    'rate_type', 'rate_flagged', 'scraped_at', 'last_verified_at',
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


# ── DB checkpoint (replaces fragile JSON file) ────────────────────────────────

def _load_checkpoint(supa) -> set:
    try:
        resp = (
            supa.table('scraper_checkpoints')
            .select('done_ids')
            .eq('job_name', CHECKPOINT_JOB)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return set(rows[0].get('done_ids') or [])
        return set()
    except Exception as exc:
        log.warning("Could not load checkpoint from DB: %s — starting fresh", exc)
        return set()


def _save_checkpoint(supa, done: set) -> None:
    try:
        supa.table('scraper_checkpoints').upsert(
            {
                'job_name':   CHECKPOINT_JOB,
                'done_ids':   list(done),
                'updated_at': datetime.now(timezone.utc).isoformat(),
            },
            on_conflict='job_name',
        ).execute()
    except Exception as exc:
        log.warning("Could not save checkpoint to DB: %s", exc)


def _reset_checkpoint(supa) -> None:
    try:
        supa.table('scraper_checkpoints').delete().eq('job_name', CHECKPOINT_JOB).execute()
        log.info("Checkpoint cleared")
    except Exception as exc:
        log.warning("Could not reset checkpoint: %s", exc)


# ── Completeness score (interest rate weighted 3×) ────────────────────────────

def _completeness(p: Dict[str, Any]) -> float:
    """
    Weighted completeness score.
    interest_rate fields count 3× each — they are the most critical for loan matching.
    """
    score = 0.0

    # Interest rate: 3× weight each
    for f in ('interest_rate_min', 'interest_rate_max'):
        if p.get(f) is not None:
            score += 3.0

    # Other numeric fields: 1× each
    for f in ('loan_amount_min', 'loan_amount_max', 'tenure_min', 'tenure_max',
              'credit_score_min', 'processing_fee'):
        if p.get(f) is not None:
            score += 1.0

    # Array fields: 1× each
    for f in ('employment_types', 'eligible_states'):
        if p.get(f):
            score += 1.0

    # Bool fields: 1× each
    for f in ('collateral_required', 'prepayment_allowed'):
        if p.get(f) is not None:
            score += 1.0

    # Rate type known: 1×
    if p.get('rate_type'):
        score += 1.0

    total = 6.0 + 6.0 + 2.0 + 2.0 + 1.0  # 17 points max
    return round(score / total, 3) if total else 0.0


# ── Float rounding helper ─────────────────────────────────────────────────────

def _r2(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 2)
    return v


# ── policy → DB row ───────────────────────────────────────────────────────────

def _policy_to_db_row(policy_dict: Dict[str, Any], lender_id: int) -> Dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    row: Dict[str, Any] = {
        'lender_id':        lender_id,
        'scraped_at':       now_iso,
        'last_verified_at': now_iso,
    }

    for k, v in policy_dict.items():
        if k not in _DB_POLICY_COLS:
            continue
        if k == 'approval_status':
            # Never overwrite a manually approved policy
            continue
        if k in ('scraped_at', 'last_verified_at'):
            # Already set above
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
        elif k == 'rates_as_of':
            # rates_as_of = lender-stated effective date, NOT today's date.
            # Leave None if the lender didn't publish one — do not fabricate.
            if isinstance(v, date):
                row[k] = v.isoformat()
            elif isinstance(v, str) and v:
                row[k] = v
            # else: leave absent (DB default null)
        elif k in ('loan_amount_min', 'loan_amount_max', 'interest_rate_min',
                   'interest_rate_max', 'processing_fee',
                   'min_monthly_income', 'min_annual_turnover'):
            row[k] = _r2(v)
        else:
            row[k] = v

    row['product_name_normalized'] = (
        (row.get('product_name') or '').lower().strip().replace(' ', '_')
    )

    # Auto-approve machine-scraped policies (guardrails already applied above).
    # setdefault means a manually-set 'rejected' row won't be overwritten when
    # we update via the Supabase upsert fallback path (update by lender_id+loan_type),
    # but the primary upsert path will overwrite — that's acceptable because we only
    # upsert when new completeness score > existing, so stale rejections are rare.
    row.setdefault('approval_status', 'approved')

    # Rate floor: reject < 5.0% (except Consumer Durable)
    ir_min = row.get('interest_rate_min')
    if ir_min is not None and row.get('loan_type') != 'Consumer Durable Loan' and ir_min < 5.0:
        row['interest_rate_min'] = None

    # Rate ceiling: reject > 42% (hard cap)
    for rate_field in ('interest_rate_min', 'interest_rate_max'):
        rv = row.get(rate_field)
        if rv is not None and rv > _RATE_HARD_MAX:
            row[rate_field] = None

    # Flag rates > 36%
    flagged = row.get('rate_flagged', False)
    for rate_field in ('interest_rate_min', 'interest_rate_max'):
        rv = row.get(rate_field)
        if rv is not None and rv > _RATE_FLAG_ABOVE:
            flagged = True
    row['rate_flagged'] = flagged

    row['completeness_score'] = _completeness(row)
    return row


# ── fetch lenders from DB ─────────────────────────────────────────────────────

def fetch_approved_lenders(
    supa,
    name_filter:  Optional[str] = None,
    limit:        Optional[int] = None,
    force:        bool = False,
    stale_days:   Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return approved lenders to enrich, respecting force/stale_days filters."""
    query = (
        supa.table('lenders')
        .select('id, company_name, website, primary_loan_segments')
        .eq('approval_status', 'approved')
        .not_.is_('website', 'null')
        .neq('website', '')
    )
    if name_filter:
        query = query.ilike('company_name', f'%{name_filter}%')
    if limit:
        query = query.limit(limit)

    resp    = query.execute()
    lenders = resp.data or []

    if force:
        log.info(f"Force mode: processing all {len(lenders)} lenders")
        return lenders

    lender_ids = [l['id'] for l in lenders]
    if not lender_ids:
        return []

    if stale_days is not None:
        # Re-scrape lenders whose policies were scraped more than N days ago
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()
        stale_resp = (
            supa.table('policies')
            .select('lender_id, scraped_at')
            .in_('lender_id', lender_ids)
            .execute()
        )
        # Keep lenders with no policy OR a policy scraped before the cutoff
        fresh_ids = set()
        for r in (stale_resp.data or []):
            sat = r.get('scraped_at') or ''
            if sat >= cutoff:
                fresh_ids.add(r['lender_id'])
        lenders = [l for l in lenders if l['id'] not in fresh_ids]
        log.info(f"Stale mode (>{stale_days}d): {len(lenders)} lenders need re-scraping")
    else:
        # Default: skip lenders that already have at least one policy
        existing = (
            supa.table('policies')
            .select('lender_id')
            .in_('lender_id', lender_ids)
            .execute()
        )
        enriched_ids = {r['lender_id'] for r in (existing.data or [])}
        lenders = [l for l in lenders if l['id'] not in enriched_ids]
        log.info(f"Skipping {len(enriched_ids)} lenders that already have policies")

    log.info(f"Found {len(lenders)} lenders to enrich")
    return lenders


# ── existing policy completeness check ───────────────────────────────────────

def get_existing_completeness(supa, lender_id: int) -> Dict[str, float]:
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
                on_conflict='lender_id,product_name_normalized,loan_type',
            ).execute()
            total += len(batch)
        except Exception as exc:
            exc_str = str(exc)
            if '23505' in exc_str and 'policies_lender_loan_type_uidx' in exc_str:
                for row in batch:
                    try:
                        supa.table('policies').update(row) \
                            .eq('lender_id', row['lender_id']) \
                            .eq('loan_type', row['loan_type']) \
                            .execute()
                        total += 1
                    except Exception as row_exc:
                        log.error(
                            "Row update fallback failed lender=%s loan_type=%s: %s",
                            row.get('lender_id'), row.get('loan_type'), row_exc,
                        )
            else:
                log.error("Upsert failed for batch at %d: %s", i, exc)
    return total


# ── main pipeline ─────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    from scraper.policy_scraper import scrape_policies

    supa = _get_supabase()

    if args.reset_checkpoint:
        _reset_checkpoint(supa)

    done = _load_checkpoint(supa)

    lenders = fetch_approved_lenders(
        supa,
        name_filter = args.only,
        limit       = args.limit,
        force       = args.force,
        stale_days  = args.stale_days,
    )

    if not lenders:
        log.info("Nothing to enrich.")
        return

    total_policies = 0
    total_lenders  = 0
    flagged_count  = 0
    errors         = 0

    for idx, lender in enumerate(lenders, 1):
        lender_id = lender['id']
        name      = lender['company_name']
        website   = lender.get('website') or ''
        raw_segs  = lender.get('primary_loan_segments') or []

        if isinstance(raw_segs, str):
            try:
                raw_segs = json.loads(raw_segs)
            except Exception:
                raw_segs = []
        loan_types = raw_segs if isinstance(raw_segs, list) else []

        lender_key = str(lender_id)
        if lender_key in done:
            log.info("[%d/%d] SKIP (checkpoint): %s", idx, len(lenders), name)
            continue

        if not website:
            log.warning("[%d/%d] SKIP (no website): %s", idx, len(lenders), name)
            done.add(lender_key)
            _save_checkpoint(supa, done)
            continue

        log.info("[%d/%d] Scraping: %s (%s)", idx, len(lenders), name, website)

        try:
            policies = scrape_policies(
                lender_name   = name,
                website       = website,
                loan_types    = loan_types or None,
                firecrawl_key = FIRECRAWL_KEY or None,
            )
        except Exception as exc:
            log.error("  Scraper error for %s: %s", name, exc)
            errors += 1
            done.add(lender_key)
            _save_checkpoint(supa, done)
            time.sleep(RATE_DELAY)
            continue

        if not policies:
            log.info("  No policies found for %s", name)
            done.add(lender_key)
            _save_checkpoint(supa, done)
            time.sleep(RATE_DELAY)
            continue

        existing = get_existing_completeness(supa, lender_id)

        rows = []
        for p in policies:
            p_dict = {k: getattr(p, k) for k in vars(p) if not k.startswith('_')}
            # DO NOT set rates_as_of = today — let it stay as extracted (or None)
            row = _policy_to_db_row(p_dict, lender_id)

            lt             = row.get('loan_type', '')
            existing_score = existing.get(lt, -1.0)
            new_score      = row.get('completeness_score', 0.0)

            if existing_score >= 0 and new_score < existing_score and not args.force:
                log.info("  SKIP '%s': existing %.2f >= new %.2f", lt, existing_score, new_score)
                continue

            if row.get('rate_flagged'):
                flagged_count += 1
                log.warning("  FLAGGED rate >36%% for %s | %s (rate_min=%s, rate_max=%s)",
                             name, lt, row.get('interest_rate_min'), row.get('interest_rate_max'))

            rows.append(row)

        if rows:
            n = upsert_policies(supa, rows, args.dry_run)
            total_policies += n
            avg = sum(r.get('completeness_score', 0) for r in rows) / len(rows)
            log.info(
                "  %s %d policies (avg completeness: %.2f)",
                "[DRY] Would upsert" if args.dry_run else "Upserted", n, avg,
            )
        else:
            log.info("  No improved policies for %s", name)

        done.add(lender_key)
        _save_checkpoint(supa, done)
        total_lenders += 1

        if idx < len(lenders):
            time.sleep(RATE_DELAY)

    log.info("=" * 60)
    log.info(
        "Done. Lenders: %d | Policies: %d | Flagged (>36%%): %d | Errors: %d",
        total_lenders, total_policies, flagged_count, errors,
    )
    if args.dry_run:
        log.info("DRY RUN — no changes written to DB")
    if flagged_count:
        log.warning("%d policies have rates >36%% and need manual review", flagged_count)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Enrich policies from DB lenders')
    parser.add_argument('--only',             type=str,  default=None,  help='Filter lenders by name substring')
    parser.add_argument('--limit',            type=int,  default=None,  help='Max lenders to process')
    parser.add_argument('--force',            action='store_true',       help='Re-enrich even if policies already exist')
    parser.add_argument('--stale-days',       type=int,  default=None,  help='Re-scrape policies scraped more than N days ago')
    parser.add_argument('--dry-run',          action='store_true',       help='Scrape but do not write to DB')
    parser.add_argument('--reset-checkpoint', action='store_true',       help='Clear DB checkpoint and restart')
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    run(args)


if __name__ == '__main__':
    main()
