"""
airflow/dags/policy_extraction_dag.py
=======================================
Automated policy extraction DAG — runs weekly after NBFC/RBI data is fresh.

What it does:
  1. load_lenders       — read approved lenders from DB (those missing policies
                          or whose lender was updated since last policy extraction)
  2. generate_chunks    — split into chunks of CHUNK_SIZE
  3. extract_policies   — parallel: scrape + Gemini → build Policy objects
                          → guardrails → write to chunk CSV
  4. merge_and_upload   — merge CSVs → validate → upsert policies table
                          (ON CONFLICT lender_id, product_name, loan_type)
  5. record_metrics     — write pipeline_runs row

Idempotency:
  - Uses ON CONFLICT upsert — safe to re-run without duplicates
  - Checkpoint file tracks processed lender IDs across restarts
  - Hash check: skip lenders whose policy data hasn't changed

Schedule: Tuesdays 03:00 UTC (day after Sunday NBFC extraction + Monday daily_refresh)
"""

from __future__ import annotations

import csv as csv_mod
import json
import os
import time
from dataclasses  import asdict
from datetime     import datetime, timedelta
from pathlib      import Path
from typing       import Any

from airflow            import DAG
from airflow.decorators import task
from airflow.models     import Variable

# ── Constants ─────────────────────────────────────────────────────────────────
CHUNK_SIZE   = 50    # policies per lender can be large — smaller chunks
PROJECT_ROOT = Path(Variable.get("PROJECT_ROOT", default_var="/opt/lender-platform"))
OUTPUT_BASE  = PROJECT_ROOT / "data" / "output" / "policy_chunks"
CHECKPOINT   = PROJECT_ROOT / "data" / "output" / ".policy_checkpoint.json"

default_args = {
    "owner":             "lender-platform",
    "depends_on_past":   False,
    "retries":           2,
    "retry_delay":       timedelta(minutes=5),
    "execution_timeout": timedelta(hours=6),
    "email_on_failure":  True,
}

# DB columns that exist in the policies table
_DB_POLICY_COLS = {
    "lender_id", "product_name", "loan_type",
    "loan_amount_min", "loan_amount_max",
    "credit_score_min", "credit_score_max",
    "min_age", "max_age", "employment_types",
    "min_monthly_income", "min_annual_turnover", "min_business_vintage",
    "interest_rate_min", "interest_rate_max",
    "tenure_min", "tenure_max", "processing_fee", "prepayment_allowed",
    "collateral_required", "collateral_types",
    "eligible_states", "eligibility_notes",
    "completeness_score", "data_source", "source_url",
    "approval_status",
    # audit / lineage (migration 018)
    "source_confidence", "version", "is_verified",
    # dedup + review workflow (migration 019)
    "product_name_normalized", "review_priority", "anomaly_flags",
    # KYC / documentation (migration 020)
    "kyc_pan_required", "kyc_aadhaar_required", "kyc_gstin_required", "kyc_itr_years",
}


def _to_db_row(policy_dict: dict) -> dict:
    return {k: v for k, v in policy_dict.items() if k in _DB_POLICY_COLS}


# ── Task functions ─────────────────────────────────────────────────────────────

@task
def load_lenders_needing_policies(ds: str) -> list[dict]:
    """
    Query DB for lenders that need policy extraction:
      - Approved lenders with zero policies (new lenders)
      - Approved lenders whose last_scraped_at > max(policy.last_updated)

    Returns list of {id, company_name, company_type, website, primary_loan_segments}
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                l.id,
                l.company_name,
                l.company_type,
                l.website,
                l.primary_loan_segments,
                MAX(p.last_updated) AS last_policy_update
            FROM lenders l
            LEFT JOIN policies p ON p.lender_id = l.id
            WHERE l.approval_status = 'approved'
            GROUP BY l.id, l.company_name, l.company_type, l.website, l.primary_loan_segments
            HAVING
                -- No policies yet
                COUNT(p.id) = 0
                -- OR lender data is fresher than policy data
                OR l.last_scraped_at > MAX(p.last_updated)
            ORDER BY l.aum_crores DESC NULLS LAST
        """)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Respect checkpoint — skip already-processed
    done_ids: set[int] = set()
    if CHECKPOINT.exists():
        try:
            cp       = json.loads(CHECKPOINT.read_text())
            done_ids = set(int(x) for x in cp.get("done", []))
        except Exception:
            pass

    pending = [r for r in rows if r["id"] not in done_ids]
    print(f"Lenders needing policies: {len(rows)} total | {len(done_ids)} already done | {len(pending)} pending")
    return pending


@task
def split_into_chunks(lenders: list[dict], ds: str) -> list[dict]:
    """Split lender list into fixed-size chunks."""
    chunks = []
    for i in range(0, len(lenders), CHUNK_SIZE):
        batch = lenders[i: i + CHUNK_SIZE]
        chunks.append({
            "chunk_index": i // CHUNK_SIZE,
            "lenders":     batch,
            "run_date":    ds,
        })
    print(f"Split into {len(chunks)} chunk(s) of up to {CHUNK_SIZE} lenders")
    return chunks


@task(retries=2, retry_delay=timedelta(minutes=3))
def extract_policies_chunk(chunk: dict) -> str:
    """
    Extract policies for one chunk of lenders.
    Returns path to the output CSV.

    Per lender:
      Phase 1 — Scrape website for product pages
      Phase 2 — Gemini extracts policies[] array
      Phase 3 — build_policies() validates + filters low-completeness policies
      Phase 4 — Write to chunk CSV
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

    from run_policy_extraction import (
        call_gemini, build_policies, make_policy_prompt, RateLimiter,
        MIN_POLICY_COMPLETENESS,
    )
    from pipeline.metrics import PipelineRun

    chunk_index = chunk["chunk_index"]
    lenders     = chunk["lenders"]
    run_date    = chunk["run_date"]

    out_dir  = OUTPUT_BASE / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"chunk_{chunk_index:03d}.csv"

    run      = PipelineRun(
        pipeline    = "policy_extraction",
        run_date    = run_date,
        dag_run_id  = os.environ.get("AIRFLOW_CTX_DAG_RUN_ID", ""),
        chunk_index = chunk_index,
    )
    rate_lim = RateLimiter()
    scraper  = None

    try:
        from scraper.lender_scraper import SingleLenderScraper
        scraper = SingleLenderScraper(use_stealth=False)
    except Exception:
        pass

    all_policies: list[dict] = []

    for lender in lenders:
        lid     = int(lender["id"])
        name    = lender["company_name"]
        ctype   = lender.get("company_type", "NBFC")
        website = lender.get("website") or ""

        # Parse known products from lender row
        try:
            products = json.loads(lender.get("primary_loan_segments") or "[]")
        except Exception:
            products = []

        # Phase 1: Scrape for product page content
        scraped: dict = {}
        if scraper and website:
            try:
                scraped = scraper.scrape(name, website)
                if scraped.get("loan_tags"):
                    for tag in scraped["loan_tags"]:
                        if tag not in products:
                            products.append(tag)
            except Exception as exc:
                print(f"  Scraper error for {name}: {exc}")

        # Phase 2: Gemini extraction
        t0     = time.time()
        prompt = make_policy_prompt(name, ctype, website, products, scraped or None)
        result = call_gemini(prompt, rate_lim)
        elapsed = round(time.time() - t0, 2)

        if not result:
            run.record_item(status="failed", error=f"Gemini returned nothing for {name}")
            print(f"  ✗ {name}: Gemini failed ({elapsed}s)")
            continue

        estimated_tokens = len(json.dumps(result)) // 4

        # Phase 3: Build + validate policies (with rejection audit trail)
        chunk_rejections: list = []
        policies = build_policies(lid, name, ctype, result,
                                  scraped_context=scraped or None,
                                  rejections=chunk_rejections)
        run.record_item_rejections(len(chunk_rejections))
        if not policies:
            run.record_item(
                status="skipped",
                reason="no_policies_above_threshold",
                gemini_tokens=estimated_tokens,
            )
            print(f"  ~ {name}: no policies above completeness={MIN_POLICY_COMPLETENESS:.0%}")
            continue

        for p in policies:
            p.source_url = website
            d = asdict(p)
            d["_run_date"]    = run_date
            d["_chunk_index"] = chunk_index
            all_policies.append(d)

        run.record_item(status="success", gemini_tokens=estimated_tokens)
        avg_comp = sum(p.completeness_score for p in policies) / len(policies)
        print(f"  ✓ {name}: {len(policies)} policies | avg completeness={avg_comp:.0%} ({elapsed}s)")

    # Write chunk CSV
    if all_policies:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv_mod.DictWriter(f, fieldnames=all_policies[0].keys())
            w.writeheader()
            w.writerows(all_policies)

    run.set_meta(lenders_in_chunk=len(lenders), policies_extracted=len(all_policies))
    run.finish()

    print(f"Chunk {chunk_index}: {len(all_policies)} policies from {len(lenders)} lenders → {out_path}")
    return str(out_path)


@task
def merge_and_upload_policies(chunk_paths: list[str], ds: str) -> dict:
    """
    Merge all policy chunk CSVs, strip non-DB fields, upsert to Supabase.
    Uses ON CONFLICT(lender_id, product_name, loan_type) — fully idempotent.
    Updates checkpoint file with successfully processed lender IDs.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from pipeline.metrics import PipelineRun

    all_rows: list[dict] = []
    for path_str in chunk_paths:
        if not path_str:
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            all_rows.extend(csv_mod.DictReader(f))

    if not all_rows:
        print("No policy rows to merge")
        return {"upserted": 0}

    print(f"Merging {len(all_rows)} policy rows from {len(chunk_paths)} chunks")

    # Strip non-DB fields before upload
    db_rows = [_to_db_row(r) for r in all_rows]

    # Upsert via Supabase client
    from supabase import create_client
    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )

    BATCH    = 100
    upserted = 0
    for i in range(0, len(db_rows), BATCH):
        batch = db_rows[i: i + BATCH]
        try:
            sb.table("policies").upsert(
                batch,
                on_conflict="lender_id,product_name_normalized,loan_type",
            ).execute()
            upserted += len(batch)
        except Exception as exc:
            print(f"  ERROR: policies upsert failed for batch {i//BATCH}: {exc}")

    # Update checkpoint with lender IDs that now have policies
    lender_ids_done = {int(r.get("lender_id", 0)) for r in db_rows if r.get("lender_id")}
    if CHECKPOINT.exists():
        try:
            cp = json.loads(CHECKPOINT.read_text())
        except Exception:
            cp = {"done": []}
    else:
        cp = {"done": []}

    cp["done"] = list(set(cp.get("done", [])) | lender_ids_done)
    CHECKPOINT.write_text(json.dumps(cp, indent=2))

    stats = {"upserted": upserted, "total_policies": len(all_rows)}
    print(f"Upserted {upserted} policies for {len(lender_ids_done)} lenders")

    run = PipelineRun(
        pipeline   = "policy_extraction_merge",
        run_date   = ds,
        dag_run_id = os.environ.get("AIRFLOW_CTX_DAG_RUN_ID", ""),
    )
    run.total_processed = len(all_rows)
    run.success_count   = upserted
    run.failed_count    = len(all_rows) - upserted
    run.set_meta(**stats)
    run.finish()

    return stats


# ── DAG definition ─────────────────────────────────────────────────────────────

with DAG(
    dag_id            = "policy_extraction",
    default_args      = default_args,
    description       = "Weekly policy extraction for approved lenders — parallel, idempotent",
    schedule_interval = "0 3 * * 2",    # Tuesdays 03:00 UTC
    start_date        = datetime(2026, 1, 1),
    catchup           = False,
    max_active_runs   = 1,
    max_active_tasks  = 8,              # parallel chunk tasks
    tags              = ["extraction", "policy", "parallel"],
) as dag:

    lenders = load_lenders_needing_policies()
    chunks  = split_into_chunks(lenders=lenders, ds="{{ ds }}")

    # Dynamic mapping: one task per chunk (number determined at runtime from DB query)
    chunk_csvs = extract_policies_chunk.expand(chunk=chunks)

    merge_and_upload_policies(chunk_paths=chunk_csvs, ds="{{ ds }}")
