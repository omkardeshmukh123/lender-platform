"""
airflow/dags/rbi_extraction_dag.py
====================================
RBI banks extraction DAG — production-grade rewrite.

Banks are split by category (PSU / Private / Foreign / Cooperative) and
run in parallel — one task per category group.

Fixes vs previous version:
  - merge_rbi_chunks sent raw CSV strings to Supabase (same bug as NBFC DAG).
    Now uses _coerce_row() for proper type conversion.
  - validate_rows() applied before upsert — prevents CHECK constraint violations.
  - PipelineRun metrics written per group and for merge task.
  - extract_bank_group now records per-lender outcomes (success/failed/skipped).

Schedule: Monthly (1st of month, 03:00 UTC)
"""

from __future__ import annotations

import csv as csv_mod
import json
import os
import time
from datetime import datetime, timedelta
from pathlib  import Path
from typing   import Any

from airflow            import DAG
from airflow.decorators import task
from airflow.models     import Variable

PROJECT_ROOT = Path(Variable.get("PROJECT_ROOT", default_var="/opt/lender-platform"))

BANK_GROUPS: dict[str, list[str]] = {
    "psu":         ["PSU Bank"],
    "private":     ["Private Bank"],
    "foreign":     ["Foreign Bank"],
    "cooperative": ["Cooperative Bank"],
}

default_args = {
    "owner":             "lender-platform",
    "retries":           2,
    "retry_delay":       timedelta(minutes=10),
    "execution_timeout": timedelta(hours=2),
    "email_on_failure":  True,
}

# Type coercion constants (shared with nbfc_extraction_dag)
_NUMERIC_COLS = {
    "aum_crores", "last_year_revenue", "recent_funding_amount",
    "ticket_size_min", "ticket_size_max", "quality_score",
}
_INT_COLS = {
    "recent_funding_year", "established_year", "employee_count",
    "branch_count", "schema_version",
}
_BOOL_COLS = {"is_listed", "pan_india", "has_subsidiaries", "rural_presence"}
_JSONB_COLS = {"primary_loan_segments", "operating_states", "product_types"}
_STRIP_COLS = {"_run_date", "_group", "lender_name", "extraction_timestamp"}


def _coerce_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k in _STRIP_COLS:
            continue
        if v is None or v == "" or v == "None":
            out[k] = None
            continue
        if k in _NUMERIC_COLS:
            try:
                out[k] = float(v)
            except (ValueError, TypeError):
                out[k] = None
        elif k in _INT_COLS:
            try:
                out[k] = int(float(v))
            except (ValueError, TypeError):
                out[k] = None
        elif k in _BOOL_COLS:
            out[k] = str(v).strip().lower() in ("true", "1", "yes")
        elif k in _JSONB_COLS:
            if isinstance(v, (list, dict)):
                out[k] = v
            else:
                try:
                    out[k] = json.loads(v)
                except Exception:
                    out[k] = []
        else:
            out[k] = v
    return out


@task(retries=2, retry_delay=timedelta(minutes=5))
def extract_bank_group(group: dict, ds: str) -> str:
    """
    Extract one category of RBI banks (PSU / Private / Foreign / Cooperative).
    Returns path to the output CSV.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

    from run_rbi_extraction import (
        load_rbi_banks, extract_with_gemini, build_lender,
        RateLimiter, merge_scraper_data,
        _SingleLenderScraper, _scraper_available, ENABLE_SCRAPER,
    )
    from dataclasses       import asdict
    from pipeline.metrics  import PipelineRun

    group_name    = group["name"]
    company_types = group["types"]

    out_dir  = PROJECT_ROOT / "data" / "output" / "rbi_chunks" / ds
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{group_name}.csv"

    run = PipelineRun(
        pipeline   = "rbi_extraction",
        run_date   = ds,
        dag_run_id = os.environ.get("AIRFLOW_CTX_DAG_RUN_ID", ""),
    )
    run.set_meta(group=group_name)

    banks    = [b for b in load_rbi_banks() if b["company_type"] in company_types]
    rate_lim = RateLimiter()
    scraper  = None

    if ENABLE_SCRAPER and _scraper_available and _SingleLenderScraper:
        try:
            scraper = _SingleLenderScraper(use_stealth=False)
        except Exception as exc:
            print(f"  WARNING: Scraper init failed: {exc}")

    results = []
    for bank in banks:
        name  = bank["company_name"]
        ctype = bank["company_type"]

        t0          = time.time()
        gemini_data = extract_with_gemini(name, ctype, rate_lim)
        elapsed     = round(time.time() - t0, 2)

        if not gemini_data:
            run.record_item(status="failed", error=f"Gemini returned nothing for {name}")
            print(f"  ✗ {name}: Gemini failed ({elapsed}s)")
            continue

        estimated_tokens = len(json.dumps(gemini_data)) // 4

        scraped: dict = {}
        website = str(gemini_data.get("website") or "").strip()
        if scraper and website:
            try:
                scraped = scraper.scrape(name, website)
            except Exception as exc:
                print(f"  Scraper error for {name}: {exc}")

        if scraped:
            gemini_data = merge_scraper_data(scraped, gemini_data)

        lender = build_lender(name, ctype, gemini_data, scraped or None, "scraper+gemini")
        row    = asdict(lender)
        row["schema_version"] = 3
        row["_run_date"]      = ds
        row["_group"]         = group_name
        results.append(row)

        run.record_item(status="success", gemini_tokens=estimated_tokens)
        print(f"  ✓ {name} ({elapsed}s)")

    if results:
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv_mod.DictWriter(f, fieldnames=results[0].keys())
            w.writeheader()
            w.writerows(results)

    run.set_meta(banks_in_group=len(banks), output_path=str(out_path))
    run.finish()

    print(f"Group '{group_name}': {len(results)}/{len(banks)} records → {out_path}")
    return str(out_path)


@task
def merge_and_upload_rbi(group_paths: list[str], ds: str) -> dict:
    """
    Merge all group CSVs, coerce types, validate, upsert to Supabase.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from upload_lenders  import validate_rows
    from pipeline.metrics import PipelineRun
    from supabase        import create_client

    all_rows: list[dict] = []
    for path_str in group_paths:
        if not path_str:
            continue
        p = Path(path_str)
        if not p.exists():
            print(f"  WARNING: group file not found: {p}")
            continue
        with open(p, encoding="utf-8-sig") as f:
            all_rows.extend(csv_mod.DictReader(f))

    if not all_rows:
        print("No RBI rows to merge")
        return {"upserted": 0}

    coerced   = [_coerce_row(r) for r in all_rows]
    validated = validate_rows(coerced)
    print(f"RBI merge: {len(validated)}/{len(coerced)} rows after validation")

    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    BATCH    = 200
    upserted = 0
    for i in range(0, len(validated), BATCH):
        batch = validated[i: i + BATCH]
        try:
            sb.table("lenders").upsert(
                batch, on_conflict="company_name"
            ).execute()
            upserted += len(batch)
        except Exception as exc:
            print(f"  ERROR: Supabase upsert batch {i//BATCH}: {exc}")

    stats = {"upserted": upserted, "validated": len(validated), "raw": len(all_rows)}

    run = PipelineRun(
        pipeline   = "rbi_extraction_merge",
        run_date   = ds,
        dag_run_id = os.environ.get("AIRFLOW_CTX_DAG_RUN_ID", ""),
    )
    run.total_processed = len(all_rows)
    run.success_count   = upserted
    run.failed_count    = len(all_rows) - len(validated)
    run.set_meta(**stats)
    run.finish()

    print(f"RBI merge complete: {upserted} upserted")
    return stats


# ── DAG definition ─────────────────────────────────────────────────────────────

with DAG(
    dag_id            = "rbi_extraction",
    default_args      = default_args,
    description       = "Monthly RBI banks extraction — parallel by bank category, type coercion, metrics",
    schedule_interval = "0 3 1 * *",   # Monthly, 1st at 03:00 UTC
    start_date        = datetime(2026, 1, 1),
    catchup           = False,
    max_active_runs   = 1,
    tags              = ["extraction", "rbi", "parallel"],
) as dag:

    # Build group dicts for dynamic mapping
    group_defs = [{"name": name, "types": types} for name, types in BANK_GROUPS.items()]

    # Dynamic mapping: one task per bank group
    group_csvs = extract_bank_group.expand_kwargs(
        [{"group": g, "ds": "{{ ds }}"} for g in group_defs]
    )

    merge_and_upload_rbi(group_paths=group_csvs, ds="{{ ds }}")
