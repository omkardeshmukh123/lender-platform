#!/usr/bin/env python3
"""
Batch embed all approved lenders into the vector column.

Usage:
  python scripts/embed_lenders.py              # embed lenders with missing embeddings
  python scripts/embed_lenders.py --all        # re-embed everything (force refresh)
  python scripts/embed_lenders.py --dry-run    # print text docs without calling API
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Allow imports from backend/api
sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "api"))

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from core.embeddings import build_lender_text, embed_lender, EmbeddingUnavailableError


_COLS = """
    id, company_name, company_type, rbi_category,
    aum_crores, aum_category, hq_state, hq_location,
    pan_india, primary_loan_segments, operating_states,
    is_listed, established_year, employee_count,
    operating_intensity, business_sector
"""

BATCH_SIZE = 20
BATCH_DELAY = 1.0  # seconds between batches — stay within Gemini free-tier rate limits


def _parse_row(row: asyncpg.Record) -> dict:
    d = dict(row)
    for arr_col in ("primary_loan_segments", "operating_states"):
        val = d.get(arr_col)
        if isinstance(val, str):
            try:
                d[arr_col] = json.loads(val)
            except Exception:
                d[arr_col] = []
        elif val is None:
            d[arr_col] = []
    return d


async def main(force_all: bool, dry_run: bool) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment or .env file", file=sys.stderr)
        sys.exit(1)

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key and not dry_run:
        print("ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2, statement_cache_size=0)

    try:
        where = "approval_status = 'approved'"
        if not force_all:
            where += " AND embedding IS NULL"

        async with pool.acquire() as conn:
            total = await conn.fetchval(f"SELECT COUNT(*) FROM lenders WHERE {where}")
            rows = await conn.fetch(f"SELECT {_COLS} FROM lenders WHERE {where} ORDER BY id")

        print(f"Lenders to embed: {total}")
        if total == 0:
            print("Nothing to do.")
            return

        embedded = 0
        failed = 0

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]

            for row in batch:
                lender = _parse_row(row)
                text = build_lender_text(lender)

                if dry_run:
                    print(f"\n--- ID {lender['id']}: {lender.get('company_name')} ---")
                    print(text)
                    embedded += 1
                    continue

                try:
                    vector = embed_lender(lender)
                    vec_literal = "[" + ",".join(f"{v:.8f}" for v in vector) + "]"
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE lenders SET embedding = $1::vector WHERE id = $2",
                            vec_literal, lender["id"],
                        )
                    embedded += 1
                    print(f"  [{embedded}/{total}] Embedded: {lender.get('company_name')}")
                except EmbeddingUnavailableError as exc:
                    failed += 1
                    print(f"  [{embedded+failed}/{total}] FAILED: {lender.get('company_name')} — {exc}")
                except Exception as exc:
                    failed += 1
                    print(f"  [{embedded+failed}/{total}] ERROR: {lender.get('company_name')} — {exc}")

            if not dry_run and i + BATCH_SIZE < len(rows):
                print(f"  Batch done. Waiting {BATCH_DELAY}s...")
                await asyncio.sleep(BATCH_DELAY)

        print(f"\nDone. Embedded: {embedded}, Failed: {failed}")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch embed lenders for semantic search")
    parser.add_argument("--all",     action="store_true", help="Re-embed all lenders, not just missing ones")
    parser.add_argument("--dry-run", action="store_true", help="Print text docs only, no API calls")
    args = parser.parse_args()
    asyncio.run(main(force_all=args.all, dry_run=args.dry_run))
