"""
airflow/dags/daily_refresh_dag.py
====================================
Daily maintenance DAG.

Tasks (in order):
  1. run_migrations        — apply any pending DB schema migrations
  2. ensure_partitions     — call DB function to create missing monthly partitions
  3. run_scheduler         — re-scrape stale lenders (batch 50, respects priority queue)
  4. refresh_mat_views     — REFRESH MATERIALIZED VIEW CONCURRENTLY for dashboard stats
  5. record_metrics        — write pipeline_runs row summarising today's run

Fixes vs previous version:
  - create_partition used raw SQL with hard-coded date arithmetic.
    Now calls ensure_matching_request_partitions() from migration 011.
  - refresh_views tried to REFRESH regular VIEWs (raises WrongObjectType).
    Now only refreshes the actual materialized views from migrations 011/013.
  - No metrics were recorded. Now writes to pipeline_runs via PipelineRun.
  - scheduler task had no structured output — result is now pushed to XCom
    so the metrics task can read success/failure counts.

Schedule: Daily at 01:00 UTC
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.models import Variable

PROJECT_ROOT = Path(Variable.get("PROJECT_ROOT", default_var="/opt/lender-platform"))

default_args = {
    "owner":             "lender-platform",
    "retries":           1,
    "retry_delay":       timedelta(minutes=5),
    "execution_timeout": timedelta(hours=3),
    "email_on_failure":  True,
}


# ── 1. Migrations ─────────────────────────────────────────────────────────────

def run_migrations(**context) -> None:
    """Apply any pending DB migrations. Runs FIRST — pipeline depends on schema."""
    result = subprocess.run(
        ["python", str(PROJECT_ROOT / "database" / "migrate.py")],
        capture_output=True, text=True, env={**os.environ},
    )
    print(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(f"migrate.py failed:\n{result.stderr}")


# ── 2. Partition maintenance ───────────────────────────────────────────────────

def ensure_partitions(**context) -> None:
    """
    Call ensure_matching_request_partitions(6) — creates the next 6 monthly
    partitions if they don't already exist.
    Uses the DB function from migration 011 instead of duplicating date logic here.
    """
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute("SELECT ensure_matching_request_partitions(6)")
        created = cur.fetchone()[0]
    conn.commit()
    conn.close()
    print(f"Partitions ensured — {created} new partition(s) created")


# ── 3. Scheduler (stale lender refresh) ───────────────────────────────────────

def run_scheduler(**context) -> dict:
    """
    Run scheduler.py --once to refresh stale lenders.
    Captures stdout to extract run summary, pushes stats to XCom.
    """
    t_start = time.time()
    result  = subprocess.run(
        [
            "python", str(PROJECT_ROOT / "backend" / "scheduler.py"),
            "--once", "--batch", "50",
        ],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "backend")},
    )

    output = result.stdout[-4000:] if len(result.stdout) > 4000 else result.stdout
    print(output)

    if result.returncode != 0:
        raise RuntimeError(f"scheduler.py failed:\n{result.stderr[-1000:]}")

    # Parse summary lines from scheduler output (format: "  Checked   : N")
    stats = {"duration_seconds": round(time.time() - t_start, 1)}
    for line in output.splitlines():
        line = line.strip()
        for key, xkey in [
            ("Checked", "checked"), ("Changed", "changed"),
            ("Unchanged", "unchanged"), ("Failed", "failed"),
        ]:
            if line.startswith(key):
                try:
                    stats[xkey] = int(line.split(":")[-1].strip().split()[0])
                except (ValueError, IndexError):
                    pass

    context["ti"].xcom_push(key="scheduler_stats", value=stats)
    return stats


# ── 4. Refresh materialized views ─────────────────────────────────────────────

def refresh_mat_views(**context) -> None:
    """
    REFRESH MATERIALIZED VIEW CONCURRENTLY for all dashboard materialized views.
    Only touches actual MATERIALIZED VIEWs — regular views are skipped silently.

    Views refreshed:
      - mv_dashboard_stats  (migration 011) — lender KPI summary
      - mv_policy_stats     (migration 011) — policy rates by type

    CONCURRENTLY = non-blocking (allows reads during refresh).
    Requires a UNIQUE index on each view (created in migration 011).
    """
    import psycopg2

    MAT_VIEWS = [
        "mv_dashboard_stats",
        "mv_policy_stats",
    ]

    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)
    refreshed = []
    skipped   = []

    with conn.cursor() as cur:
        for view in MAT_VIEWS:
            # Verify it's a materialized view before trying to refresh
            cur.execute(
                "SELECT COUNT(*) FROM pg_matviews WHERE matviewname = %s", (view,)
            )
            is_matview = cur.fetchone()[0] > 0

            if not is_matview:
                skipped.append(view)
                continue

            try:
                cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}")
                refreshed.append(view)
            except Exception as exc:
                # Non-fatal: Grafana shows stale data, not an error page
                print(f"  WARNING: Failed to refresh {view}: {exc}")
                skipped.append(view)

    conn.commit()
    conn.close()
    print(f"Refreshed: {refreshed}")
    if skipped:
        print(f"Skipped (not materialized or error): {skipped}")


# ── 5. Record pipeline metrics ────────────────────────────────────────────────

def record_metrics(**context) -> None:
    """
    Write one row to pipeline_runs summarising today's daily_refresh run.
    Reads scheduler stats from XCom to populate counts.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from pipeline.metrics import PipelineRun

    scheduler_stats: dict = context["ti"].xcom_pull(
        task_ids="run_scheduler", key="scheduler_stats"
    ) or {}

    run = PipelineRun(
        pipeline   = "daily_refresh",
        run_date   = context["ds"],
        dag_run_id = context.get("run_id", ""),
    )
    # Translate scheduler stats into pipeline metrics
    checked   = scheduler_stats.get("checked",   0)
    changed   = scheduler_stats.get("changed",   0)
    failed    = scheduler_stats.get("failed",    0)
    unchanged = scheduler_stats.get("unchanged", 0)

    run.total_processed = checked
    run.success_count   = changed + unchanged  # both are "processed without error"
    run.failed_count    = failed
    run.set_meta(
        changed=changed,
        unchanged=unchanged,
        scheduler_duration_s=scheduler_stats.get("duration_seconds", 0),
    )
    run.finish(db_url=os.environ.get("DATABASE_URL"))


# ── DAG definition ─────────────────────────────────────────────────────────────

with DAG(
    dag_id          = "daily_refresh",
    default_args    = default_args,
    description     = "Daily: migrations → partition maintenance → stale refresh → mat view refresh → metrics",
    schedule_interval = "0 1 * * *",   # 01:00 UTC every day
    start_date      = datetime(2026, 1, 1),
    catchup         = False,
    max_active_runs = 1,
    tags            = ["maintenance", "daily"],
) as dag:

    t_migrate = PythonOperator(
        task_id         = "run_migrations",
        python_callable = run_migrations,
    )

    t_partitions = PythonOperator(
        task_id         = "ensure_partitions",
        python_callable = ensure_partitions,
    )

    t_scheduler = PythonOperator(
        task_id         = "run_scheduler",
        python_callable = run_scheduler,
    )

    t_views = PythonOperator(
        task_id         = "refresh_mat_views",
        python_callable = refresh_mat_views,
    )

    t_metrics = PythonOperator(
        task_id         = "record_metrics",
        python_callable = record_metrics,
        trigger_rule    = "all_done",   # record metrics even if scheduler partially failed
    )

    # migrations → partitions (schema must exist before partition DDL)
    # migrations → scheduler  (scheduler needs latest schema)
    # scheduler  → mat views  (refresh after new data lands)
    # views      → metrics    (metrics are last: summarise everything)
    t_migrate >> t_partitions >> t_scheduler >> t_views >> t_metrics
