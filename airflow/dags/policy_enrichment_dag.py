"""
Policy enrichment DAG — runs every Saturday at 3am.
BSE XBRL first, then FPC PDF + BankBazaar in parallel, then refresh view.
No Gemini.
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import asyncpg
from airflow import DAG
from airflow.operators.python import PythonOperator

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

log = logging.getLogger(__name__)
DB_URL = os.environ.get("DATABASE_URL", "")
DEFAULT_ARGS = {"owner": "mitram360", "retries": 1, "retry_delay": timedelta(minutes=5)}


async def _pool():
    return await asyncpg.create_pool(DB_URL, min_size=2, max_size=5)


async def _run_bse_xbrl():
    from enrichers.bse_xbrl import BSEXBRLEnricher
    from bank_manager import BankManager
    pool = await _pool()
    bm = BankManager(db=pool)
    enricher = BSEXBRLEnricher()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT l.id, l.cin, p.id AS pid, p.loan_type FROM lenders l "
            "JOIN policies p ON p.lender_id = l.id "
            "WHERE l.cin IS NOT NULL AND l.approval_status='approved' AND p.approval_status='approved'"
        )
    lmap = defaultdict(lambda: {"cin": None, "pm": {}})
    for r in rows:
        lmap[r["id"]]["cin"] = r["cin"]
        lmap[r["id"]]["pm"][r["loan_type"]] = str(r["pid"])
    stored = rejected = 0
    for lid, d in lmap.items():
        for p in enricher.enrich_lender(str(lid), d["cin"], d["pm"]):
            if await bm.validate_and_store(p): stored += 1
            else: rejected += 1
    log.info("BSE XBRL: stored=%d rejected=%d", stored, rejected)
    await pool.close()


async def _run_fpc_pdf():
    from enrichers.fpc_pdf import FPCPDFEnricher
    from bank_manager import BankManager
    pool = await _pool()
    bm = BankManager(db=pool)
    enricher = FPCPDFEnricher()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT l.id, l.website, p.id AS pid, p.loan_type FROM lenders l "
            "JOIN policies p ON p.lender_id = l.id "
            "WHERE l.website IS NOT NULL AND l.cin IS NULL "
            "AND l.approval_status='approved' AND p.approval_status='approved'"
        )
    lmap = defaultdict(lambda: {"website": None, "pm": {}})
    for r in rows:
        lmap[r["id"]]["website"] = r["website"]
        lmap[r["id"]]["pm"][r["loan_type"]] = str(r["pid"])
    stored = rejected = 0
    for lid, d in lmap.items():
        for p in enricher.enrich_lender(str(lid), d["website"], d["pm"]):
            if await bm.validate_and_store(p): stored += 1
            else: rejected += 1
    log.info("FPC PDF: stored=%d rejected=%d", stored, rejected)
    await pool.close()


async def _run_bankbazaar():
    from enrichers.bankbazaar import BankBazaarEnricher
    from bank_manager import BankManager
    pool = await _pool()
    bm = BankManager(db=pool)
    enricher = BankBazaarEnricher()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT l.id, l.company_name, p.id AS pid, p.loan_type FROM lenders l "
            "JOIN policies p ON p.lender_id = l.id "
            "WHERE l.approval_status='approved' AND p.approval_status='approved'"
        )
    lmap = defaultdict(lambda: {"name": None, "pm": {}})
    for r in rows:
        lmap[r["id"]]["name"] = r["company_name"]
        lmap[r["id"]]["pm"][r["loan_type"]] = str(r["pid"])
    stored = rejected = 0
    for lid, d in lmap.items():
        for p in enricher.enrich_lender(str(lid), d["name"], d["pm"]):
            if await bm.validate_and_store(p): stored += 1
            else: rejected += 1
    log.info("BankBazaar: stored=%d rejected=%d", stored, rejected)
    await pool.close()


async def _refresh():
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY policies_enriched")
    log.info("policies_enriched refreshed")
    await pool.close()


def run_bse():    asyncio.run(_run_bse_xbrl())
def run_fpc():    asyncio.run(_run_fpc_pdf())
def run_bb():     asyncio.run(_run_bankbazaar())
def refresh():    asyncio.run(_refresh())


with DAG("policy_enrichment", default_args=DEFAULT_ARGS,
         schedule_interval="0 3 * * 6", start_date=datetime(2026, 5, 1),
         catchup=False, tags=["enrichment", "policies"]) as dag:
    t1 = PythonOperator(task_id="bse_xbrl_enricher",   python_callable=run_bse)
    t2 = PythonOperator(task_id="fpc_pdf_enricher",    python_callable=run_fpc)
    t3 = PythonOperator(task_id="bankbazaar_enricher", python_callable=run_bb)
    t4 = PythonOperator(task_id="refresh_view",        python_callable=refresh)
    t1 >> [t2, t3] >> t4
