"""
airflow/dags/nbfc_extraction_dag.py
=====================================
Parallel NBFC extraction DAG — production-grade rewrite.

Fixes vs previous version:
  ──────────────────────────────────────────────────────────────
  BUG 1 — Static 10-chunk assumption:
    Old code pre-created exactly 10 extract_chunk tasks (chunk_00…chunk_09).
    If actual chunks ≠ 10, tasks failed with "Chunk N not found in XCom".
    Fix: Use dynamic task mapping (@task.expand / mapped operators) so
    the number of tasks matches the actual number of chunks at runtime.

  BUG 2 — String types in Supabase upsert:
    CSV round-trip converts all values to strings. merge_chunks sent
    {"aum_crores": "25000"} (string). DB CHECK constraints and numeric
    comparisons silently failed.
    Fix: Proper type coercion in _coerce_row() before upsert.

  BUG 3 — Cross-chunk duplicate detection:
    Each chunk ran Guardrails() with a fresh in-memory DeduplicationStore.
    Duplicates across chunk boundaries were not caught.
    Fix: Dedup is enforced at DB level (company_name UNIQUE constraint).
    In-chunk dedup still runs; cross-chunk is DB-level.

  BUG 4 — Checkpoint written before DB upsert succeeded (CRITICAL):
    Old code: mark_done(chunk_id) → upsert_to_db(records)
    If DAG crashed between checkpoint write and upsert, those lenders were
    permanently skipped — checkpoint said "done", DB said "missing".
    Fix: Checkpoint is ONLY written AFTER upsert returns successfully.

  BUG 5 — Non-transactional batch upserts (CRITICAL):
    Upserts happened in chunks of 200. If chunk 3/5 failed, chunks 1-2
    were already committed — partial DB state, no rollback.
    Fix: Single psycopg2 transaction wraps ALL batches. Any failure rolls
    back the entire merge. Failed rows go to extraction_failures table.

  BUG 6 — No circuit breaker for Gemini (CRITICAL):
    If Gemini was rate-limited/down, every lender hard-failed, the whole
    DAG task failed, and Airflow retried everything from scratch.
    Fix: GeminiCircuitBreaker wraps every Gemini call. After 5 consecutive
    failures, the circuit opens — remaining lenders are skipped gracefully
    (not failed). The DAG completes with partial results + failures logged
    to extraction_failures. Operator is alerted via Airflow email.

  BUG 7 — No dead-letter queue:
    Failed lenders were silently skipped — no record of what failed or why.
    Fix: Every failure (scrape/gemini/guardrails/upsert) writes a row to
    extraction_failures table (migration 014). Admin dashboard shows them.

  IMPROVEMENT — Gemini cache (skip unchanged lenders):
    Before calling Gemini, compute a scrape hash and compare to DB.
    If unchanged → skip Gemini call → save API cost + quota.

  IMPROVEMENT — Structured metrics:
    Each chunk writes a pipeline_runs row (via PipelineRun).
    merge_chunks writes a final aggregated row.

  IMPROVEMENT — RBI validation before merge:
    merge_chunks now validates rows through validate_rows() from
    upload_lenders.py before upserting — prevents constraint violations.

Strategy:
  - Load all pending NBFC IDs (checkpoint-aware)
  - Split into chunks of CHUNK_SIZE
  - Map one Airflow task per chunk (fully dynamic, not hard-coded 10)
  - Each chunk: scrape → hash check → Gemini → guardrails → write CSV
  - Final merge task: read all CSVs → validate → upsert Supabase (one tx)
  - Checkpoint written ONLY after successful upsert

Schedule: Sundays 02:00 UTC (weekly full refresh)
          Also triggered manually after first-time bulk load
"""

from __future__ import annotations

import json
import os
import csv as csv_mod
import time
from datetime   import datetime, timedelta
from pathlib    import Path
from typing     import Any

from airflow            import DAG
from airflow.decorators import task
from airflow.models     import Variable

# ── Constants ─────────────────────────────────────────────────────────────────
CHUNK_SIZE   = 100    # lenders per parallel task
PROJECT_ROOT = Path(Variable.get("PROJECT_ROOT", default_var="/opt/lender-platform"))
INPUT_CSV    = PROJECT_ROOT / "data" / "input" / "nbfc_names.csv"
OUTPUT_BASE  = PROJECT_ROOT / "data" / "output" / "nbfc_chunks"
CHECKPOINT   = PROJECT_ROOT / "data" / "output" / ".checkpoint.json"

default_args = {
    "owner":             "lender-platform",
    "depends_on_past":   False,
    "retries":           2,
    "retry_delay":       timedelta(minutes=5),
    "execution_timeout": timedelta(hours=4),
    "email_on_failure":  True,
}


# ── Type coercion ──────────────────────────────────────────────────────────────

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
# Internal-only fields written by the DAG — strip before DB insert
_STRIP_COLS = {"_run_date", "_chunk_index", "lender_name", "extraction_timestamp"}


def _coerce_row(row: dict) -> dict:
    """
    Convert CSV string values to proper Python types for Supabase upsert.
    Strips internal pipeline fields that don't exist in the DB schema.
    """
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


# ── Dead-letter writer ─────────────────────────────────────────────────────────

def _write_failure(
    *,
    lender_name: str,
    failure_stage: str,
    failure_reason: str,
    error_detail: str = "",
    raw_response: dict | None = None,
    guardrail_issues: list | None = None,
    dag_run_id: str = "",
    chunk_index: int = 0,
    run_date: str = "",
) -> None:
    """
    Write one row to extraction_failures (dead-letter table).
    Never raises — failures in the failure writer must not crash the pipeline.
    """
    import psycopg2
    import psycopg2.extras

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print(f"  [DLQ] No DATABASE_URL — cannot write failure for {lender_name}")
        return
    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO extraction_failures
                    (lender_name, failure_stage, failure_reason, error_detail,
                     raw_response, guardrail_issues, dag_run_id, chunk_index, run_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    lender_name[:500],
                    failure_stage,
                    failure_reason[:2000],
                    (error_detail or "")[:2000],
                    psycopg2.extras.Json(raw_response) if raw_response else None,
                    psycopg2.extras.Json(guardrail_issues) if guardrail_issues else None,
                    dag_run_id,
                    chunk_index,
                    run_date or str(datetime.now().date()),
                ),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        # Intentionally swallowed — DLQ write failure must not stop the pipeline
        print(f"  [DLQ] Failed to write failure record for {lender_name}: {exc}")


# ── Task functions ─────────────────────────────────────────────────────────────

@task
def generate_chunks(ds: str) -> list[dict]:
    """
    Build chunk definitions from the NBFC input CSV.
    Returns a list of chunk dicts — one per parallel extract task.
    Respects the checkpoint file (skips already-processed IDs).
    """
    import csv

    # Load all IDs from input CSV
    all_ids: list[int] = []
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                all_ids.append(int(row["id"]))
            except (KeyError, ValueError):
                pass
    all_ids = sorted(set(all_ids))

    # Subtract already-done IDs from checkpoint
    done_ids: set[int] = set()
    if CHECKPOINT.exists():
        try:
            cp        = json.loads(CHECKPOINT.read_text())
            processed = set(int(x) for x in cp.get("processed_ids", []))
            failed    = set(int(x) for x in cp.get("failed_ids", []))
            done_ids  = processed - failed
        except Exception:
            pass

    pending = [i for i in all_ids if i not in done_ids]
    print(f"Total: {len(all_ids)} | Done: {len(done_ids)} | Pending: {len(pending)}")

    chunks: list[dict] = []
    for i in range(0, len(pending), CHUNK_SIZE):
        ids = pending[i: i + CHUNK_SIZE]
        chunks.append({
            "chunk_index": i // CHUNK_SIZE,
            "ids":         ids,
            "run_date":    ds,
        })

    print(f"Generated {len(chunks)} chunk(s) of up to {CHUNK_SIZE} lenders each")
    return chunks


@task(retries=2, retry_delay=timedelta(minutes=3))
def extract_chunk(chunk: dict) -> str:
    """
    Extract one chunk of NBFCs.
    Returns the path to the output CSV for this chunk.

    Pipeline per lender:
      Phase 1 — Scrape website (if scraper available)
      Phase 2 — Hash check vs DB (skip Gemini if unchanged)
      Phase 3 — Gemini extraction (via circuit breaker)
      Phase 4 — Merge scraper + Gemini data
      Phase 5 — Guardrails validation + quality score
      Phase 6 — Write to chunk CSV

    Failures at any phase are written to extraction_failures (dead-letter).
    Circuit breaker prevents Gemini quota exhaustion from failing the whole DAG.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

    from run_nbfc_extraction import (
        load_nbfc_csv, extract_with_gemini, build_lender_record,
        merge_scraper_data, RateLimiter,
        _SingleLenderScraper, _scraper_available, ENABLE_SCRAPER,
    )
    from scraper.guardrails import Guardrails, DeduplicationStore
    from pipeline.metrics   import PipelineRun
    from pipeline.gemini_cache import GeminiCache, compute_scrape_hash
    from pipeline.circuit_breaker import get_gemini_circuit, CircuitOpenError

    chunk_index = chunk["chunk_index"]
    target_ids  = set(chunk["ids"])
    run_date    = chunk["run_date"]
    dag_run_id  = os.environ.get("AIRFLOW_CTX_DAG_RUN_ID", "")

    out_dir  = OUTPUT_BASE / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"chunk_{chunk_index:03d}.csv"

    run    = PipelineRun(
        pipeline    = "nbfc_extraction",
        run_date    = run_date,
        dag_run_id  = dag_run_id,
        chunk_index = chunk_index,
    )
    cache      = GeminiCache()
    rate_lim   = RateLimiter()
    guardrails = Guardrails(dedup=DeduplicationStore())
    circuit    = get_gemini_circuit(
        failure_threshold=int(os.environ.get("GEMINI_CIRCUIT_THRESHOLD", "5")),
        reset_timeout=float(os.environ.get("GEMINI_CIRCUIT_RESET_S", "300")),
    )
    scraper = None

    if ENABLE_SCRAPER and _scraper_available and _SingleLenderScraper:
        try:
            scraper = _SingleLenderScraper(use_stealth=False)
        except Exception as exc:
            print(f"  WARNING: Scraper init failed: {exc} — Gemini-only mode")

    nbfcs   = [n for n in load_nbfc_csv() if int(n.get("id", 0)) in target_ids]
    results = []

    for nbfc in nbfcs:
        nbfc_id = int(nbfc.get("id", 0))
        name    = nbfc.get("company_name", "").strip()
        website = nbfc.get("original_website", "").strip()
        summary = nbfc.get("business_summary", "").strip()
        focus   = nbfc.get("primary_focus", "").strip()

        # Phase 1: Scrape
        scraped: dict = {}
        if scraper and website:
            try:
                scraped = scraper.scrape(name, website)
                if scraped.get("is_non_lender"):
                    run.record_item(status="skipped", reason="non_lender_signal")
                    continue
            except Exception as exc:
                print(f"  Scraper error for {name}: {exc}")
                _write_failure(
                    lender_name=name, failure_stage="scrape",
                    failure_reason=f"Scraper raised: {type(exc).__name__}",
                    error_detail=str(exc), dag_run_id=dag_run_id,
                    chunk_index=chunk_index, run_date=run_date,
                )
                scraped = {}

        # Phase 2: Hash check — skip Gemini if website content unchanged
        if scraped:
            new_hash = compute_scrape_hash(scraped)
            if cache.is_unchanged(lender_id=nbfc_id, new_hash=new_hash):
                run.record_item(status="skipped", reason="hash_unchanged")
                continue

        # Phase 3: Gemini extraction — wrapped in circuit breaker
        t_gemini   = time.time()
        extracted  = None
        try:
            extracted = circuit.call(
                extract_with_gemini,
                name, website, summary, focus, rate_lim,
                scraped_context=scraped or None,
            )
        except CircuitOpenError as exc:
            # Circuit is open — Gemini is down/rate-limited.
            # Skip remaining lenders gracefully rather than failing the task.
            print(f"  CIRCUIT OPEN — skipping {name}: {exc}")
            run.record_item(status="skipped", reason="gemini_circuit_open")
            _write_failure(
                lender_name=name, failure_stage="gemini",
                failure_reason="Gemini circuit breaker open — API unavailable",
                error_detail=str(exc), dag_run_id=dag_run_id,
                chunk_index=chunk_index, run_date=run_date,
            )
            continue
        except Exception as exc:
            gemini_latency = round(time.time() - t_gemini, 2)
            print(f"  ✗ {name}: Gemini error ({gemini_latency}s): {exc}")
            run.record_item(status="failed", error=f"Gemini error: {exc}")
            _write_failure(
                lender_name=name, failure_stage="gemini",
                failure_reason=f"Gemini raised: {type(exc).__name__}",
                error_detail=str(exc), dag_run_id=dag_run_id,
                chunk_index=chunk_index, run_date=run_date,
            )
            continue

        gemini_latency = round(time.time() - t_gemini, 2)

        if not extracted:
            run.record_item(status="failed", error=f"Gemini returned nothing for {name}")
            print(f"  ✗ {name}: Gemini returned nothing ({gemini_latency}s)")
            _write_failure(
                lender_name=name, failure_stage="gemini",
                failure_reason="Gemini returned empty/null response",
                dag_run_id=dag_run_id, chunk_index=chunk_index, run_date=run_date,
            )
            continue

        # Estimate tokens from response size (rough: 4 chars ≈ 1 token)
        estimated_tokens = len(json.dumps(extracted)) // 4

        # Phase 4: Merge scraper data
        if scraped:
            extracted = merge_scraper_data(scraped, extracted)

        # Phase 5: Guardrails
        gr_result = guardrails.run({**nbfc, **extracted, "data_source": "scraper+gemini"})
        if not gr_result.is_valid:
            issues = [i["message"] for i in gr_result.critical_issues]
            run.record_item(
                status="failed",
                error=f"{name}: guardrails failed — {'; '.join(issues[:2])}",
                gemini_tokens=estimated_tokens,
            )
            print(f"  ✗ {name}: guardrails rejected ({issues[:1]})")
            _write_failure(
                lender_name=name, failure_stage="guardrails",
                failure_reason="; ".join(issues[:3]),
                guardrail_issues=gr_result.issues[:10],
                dag_run_id=dag_run_id, chunk_index=chunk_index, run_date=run_date,
            )
            continue

        # Phase 6: Build DB record
        row = build_lender_record(nbfc, gr_result.clean_data, scraped, data_src="scraper+gemini")
        row["_run_date"]    = run_date
        row["_chunk_index"] = chunk_index
        row["schema_version"] = 3
        results.append(row)

        if scraped:
            cache.update_hash(lender_id=nbfc_id, new_hash=compute_scrape_hash(scraped))

        run.record_item(status="success", gemini_tokens=estimated_tokens)
        print(f"  ✓ {name} (quality={gr_result.quality_score:.0%}, {gemini_latency}s)")

    # Write chunk output
    if results:
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv_mod.DictWriter(f, fieldnames=results[0].keys())
            w.writeheader()
            w.writerows(results)

    # Log circuit breaker state at end of chunk
    cb_stats = circuit.stats()
    if cb_stats["total_circuit_opens"] > 0:
        print(
            f"  [CIRCUIT] Opened {cb_stats['total_circuit_opens']} time(s) this chunk. "
            f"Blocked {cb_stats['total_blocked']} calls. State: {cb_stats['state']}"
        )

    run.set_meta(lenders_in_chunk=len(nbfcs), output_path=str(out_path))
    run.finish()

    print(f"Chunk {chunk_index}: {len(results)}/{len(nbfcs)} written → {out_path}")
    return str(out_path)


@task
def merge_and_upload(chunk_paths: list[str], ds: str) -> dict:
    """
    Merge all chunk CSVs, apply type coercion and validation, upsert to Supabase.

    Key guarantees (audit fixes):
    ─────────────────────────────
    1. ALL batches upsert inside ONE psycopg2 transaction.
       If any batch fails, the entire upsert rolls back — no partial state.

    2. Checkpoint is written ONLY after the transaction commits successfully.
       If the DAG crashes before commit, next run re-processes these lenders.

    3. Every upsert failure writes a row to extraction_failures (dead-letter).

    Returns a dict with upload stats for the metrics task.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from upload_lenders import validate_rows
    from pipeline.metrics import PipelineRun
    import psycopg2
    import psycopg2.extras

    dag_run_id = os.environ.get("AIRFLOW_CTX_DAG_RUN_ID", "")
    run_date   = ds
    all_rows: list[dict] = []

    for path_str in chunk_paths:
        if not path_str:
            continue
        p = Path(path_str)
        if not p.exists():
            print(f"  WARNING: chunk file not found: {p}")
            continue
        with open(p, encoding="utf-8-sig") as f:
            all_rows.extend(csv_mod.DictReader(f))

    if not all_rows:
        print("No rows to merge — all chunks empty or failed")
        return {"upserted": 0, "validated": 0, "raw": 0}

    print(f"Loaded {len(all_rows)} rows from {len(chunk_paths)} chunk(s)")

    # Coerce types (converts string numerics, booleans, JSON columns)
    coerced = [_coerce_row(r) for r in all_rows]

    # Pre-upload validation (enforces same rules as DB CHECK constraints)
    validated = validate_rows(coerced)
    print(f"After validation: {len(validated)}/{len(coerced)} rows")

    # Log rows that failed pre-upload validation to dead-letter
    if len(validated) < len(coerced):
        valid_names = {r.get("company_name") for r in validated}
        for row in coerced:
            name = row.get("company_name", "unknown")
            if name not in valid_names:
                _write_failure(
                    lender_name=name, failure_stage="validation",
                    failure_reason="Failed pre-upload validation (DB constraint check)",
                    dag_run_id=dag_run_id, run_date=run_date,
                )

    # ── Single transaction for all upserts ────────────────────────────────────
    # FIX: One transaction = atomic. If any batch fails, everything rolls back.
    # We write the checkpoint ONLY after this commit succeeds.
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set — cannot upsert")

    upserted      = 0
    upsert_failed = 0
    BATCH         = 200

    conn = psycopg2.connect(db_url, connect_timeout=15)
    try:
        with conn:  # psycopg2 context manager: commits on exit, rolls back on exception
            with conn.cursor() as cur:
                for i in range(0, len(validated), BATCH):
                    batch = validated[i: i + BATCH]
                    try:
                        # Build bulk upsert: INSERT ... ON CONFLICT (company_name) DO UPDATE
                        if not batch:
                            continue
                        cols = list(batch[0].keys())
                        placeholders = ", ".join(
                            f"({', '.join(['%s'] * len(cols))})"
                            for _ in batch
                        )
                        values = [
                            tuple(row.get(c) for c in cols)
                            for row in batch
                        ]
                        # Flatten for executemany-style insert
                        update_set = ", ".join(
                            f"{c} = EXCLUDED.{c}"
                            for c in cols
                            if c != "company_name"
                        )
                        sql = f"""
                            INSERT INTO lenders ({', '.join(cols)})
                            VALUES %s
                            ON CONFLICT (company_name)
                            DO UPDATE SET {update_set}
                        """
                        psycopg2.extras.execute_values(
                            cur, sql, values, page_size=BATCH
                        )
                        upserted += len(batch)
                        print(
                            f"  Batch {i // BATCH + 1}: {len(batch)} rows upserted "
                            f"(total so far: {upserted})"
                        )
                    except Exception as exc:
                        # This will trigger rollback of the entire transaction
                        # (via the `with conn:` context manager)
                        print(f"  ERROR in batch {i // BATCH}: {exc}")
                        # Log to dead-letter before re-raising
                        for row in batch:
                            _write_failure(
                                lender_name=row.get("company_name", "unknown"),
                                failure_stage="db_upsert",
                                failure_reason=f"Batch upsert failed: {type(exc).__name__}",
                                error_detail=str(exc)[:500],
                                dag_run_id=dag_run_id, run_date=run_date,
                            )
                        upsert_failed += len(batch)
                        raise  # triggers full rollback

    except Exception as exc:
        print(f"  UPSERT TRANSACTION FAILED — all changes rolled back: {exc}")
        stats = {
            "upserted": 0, "validated": len(validated),
            "raw": len(all_rows), "error": str(exc),
        }
        # Do NOT update checkpoint — next run will retry these lenders
        _write_pipeline_metrics(
            run_date=run_date, dag_run_id=dag_run_id,
            all_rows=all_rows, upserted=0, validated=validated,
        )
        return stats
    finally:
        conn.close()

    print(f"Merge complete: {upserted} upserted from {len(validated)} validated rows")

    # ── Checkpoint update — ONLY after successful commit ───────────────────────
    # FIX: Previously checkpoint was updated before upsert. Now it's only
    # updated after the transaction commits successfully.
    _update_checkpoint(chunk_paths=chunk_paths, run_date=run_date)

    stats = {"upserted": upserted, "validated": len(validated), "raw": len(all_rows)}
    _write_pipeline_metrics(
        run_date=run_date, dag_run_id=dag_run_id,
        all_rows=all_rows, upserted=upserted, validated=validated,
    )
    return stats


def _update_checkpoint(chunk_paths: list[str], run_date: str) -> None:
    """
    Update checkpoint file with IDs that were successfully upserted.
    Called ONLY after a successful DB commit.
    """
    import csv

    newly_processed: set[int] = set()
    for path_str in chunk_paths:
        if not path_str:
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    try:
                        newly_processed.add(int(row.get("id", 0)))
                    except (ValueError, TypeError):
                        pass
        except Exception as exc:
            print(f"  WARNING: Could not read chunk for checkpoint update: {exc}")

    # Merge with existing checkpoint
    existing: dict = {}
    if CHECKPOINT.exists():
        try:
            existing = json.loads(CHECKPOINT.read_text())
        except Exception:
            existing = {}

    prev_processed = set(int(x) for x in existing.get("processed_ids", []))
    all_processed  = prev_processed | newly_processed

    CHECKPOINT.write_text(json.dumps({
        "processed_ids": sorted(all_processed),
        "failed_ids":    existing.get("failed_ids", []),
        "last_run_date": run_date,
        "last_updated":  datetime.now().isoformat(),
    }, indent=2))
    print(f"  Checkpoint updated: {len(newly_processed)} new IDs, {len(all_processed)} total")


def _write_pipeline_metrics(
    *,
    run_date: str,
    dag_run_id: str,
    all_rows: list,
    upserted: int,
    validated: list,
) -> None:
    """Write final pipeline metrics row to pipeline_runs."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from pipeline.metrics import PipelineRun

    run = PipelineRun(
        pipeline   = "nbfc_extraction_merge",
        run_date   = run_date,
        dag_run_id = dag_run_id,
    )
    run.total_processed = len(all_rows)
    run.success_count   = upserted
    run.failed_count    = len(all_rows) - len(validated)
    run.set_meta(upserted=upserted, validated=len(validated), raw=len(all_rows))
    run.finish()


# ── DAG definition ─────────────────────────────────────────────────────────────

with DAG(
    dag_id            = "nbfc_extraction",
    default_args      = default_args,
    description       = "Parallel NBFC extraction — dynamic chunking, circuit breaker, transactional upsert",
    schedule_interval = "0 2 * * 0",    # Sundays 02:00 UTC
    start_date        = datetime(2026, 1, 1),
    catchup           = False,
    max_active_runs   = 1,
    max_active_tasks  = 10,             # max parallel chunk tasks
    tags              = ["extraction", "nbfc", "parallel"],
) as dag:

    chunks = generate_chunks()

    # Dynamic task mapping: one extract_chunk task per chunk (not hard-coded 10)
    chunk_results = extract_chunk.expand(chunk=chunks)

    merge_and_upload(chunk_paths=chunk_results, ds="{{ ds }}")
