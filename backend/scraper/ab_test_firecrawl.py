"""
ab_test_firecrawl.py
====================
Compare current scraper vs Firecrawl API on the same lender URLs.

Usage:
    python ab_test_firecrawl.py --urls "https://site1.com" "https://site2.com"
    python ab_test_firecrawl.py --file urls.txt   # one URL per line

Requirements:
    pip install firecrawl-py requests python-dotenv
    FIRECRAWL_API_KEY=fc-... in .env or environment

Output:
    Prints a per-URL comparison table + a summary CSV: ab_test_results.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

# Allow importing from parent / scraper directories
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# FIELDS WE COMPARE
# ──────────────────────────────────────────────────────────────

COMPARE_FIELDS = [
    "phone", "email", "hq_city", "hq_state",
    "established_year", "employee_count", "branch_count",
    "loan_tags",          # product types
    "operating_states",   # state list
    "rbi_registration",
]

# ──────────────────────────────────────────────────────────────
# CURRENT SCRAPER
# ──────────────────────────────────────────────────────────────

def _name_from_url(url: str) -> str:
    """Derive a lender display name from the URL domain."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc.removeprefix("www.")
    return host.split(".")[0].title()


def run_current_scraper(url: str) -> dict[str, Any]:
    """Run the existing SingleLenderScraper and return its dict output."""
    try:
        from lender_scraper import SingleLenderScraper
        scraper = SingleLenderScraper()
        return scraper.scrape(_name_from_url(url), url)
    except Exception as exc:
        log.warning("Current scraper error for %s: %s", url, exc)
        return {"_error": str(exc)}


# ──────────────────────────────────────────────────────────────
# FIRECRAWL
# ──────────────────────────────────────────────────────────────

def run_firecrawl(url: str, api_key: str) -> dict[str, Any]:
    """
    Call Firecrawl /scrape endpoint and return the extracted markdown + metadata.
    Returns a dict with the same keys as the current scraper for easy comparison.
    """
    endpoint = "https://api.firecrawl.dev/v1/scrape"
    headers  = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload  = {
        "url":     url,
        "formats": ["markdown", "extract"],
        "extract": {
            # Use string for all numeric fields — Firecrawl returns 0 (not null)
            # for missing integers, which pollutes the comparison. We parse manually.
            "schema": {
                "type": "object",
                "properties": {
                    "phone":              {"type": "string"},
                    "email":              {"type": "string"},
                    "hq_city":            {"type": "string"},
                    "hq_state":           {"type": "string"},
                    "established_year":   {"type": "string"},
                    "employee_count":     {"type": "string"},
                    "branch_count":       {"type": "string"},
                    "loan_tags":          {"type": "array", "items": {"type": "string"}},
                    "operating_states":   {"type": "array", "items": {"type": "string"}},
                    "rbi_registration":   {"type": "string"},
                },
            },
            "prompt": (
                "Extract the following fields from this Indian lender/NBFC website. "
                "For loan_tags, use only canonical names: MSME Loan, Personal Loan, Home Loan, "
                "Business Loan, Vehicle Loan, Gold Loan, Education Loan, Micro Loan, "
                "Loan Against Property, Working Capital, Agriculture Loan, EV Loan, "
                "Two Wheeler Loan, Rural Loan, Microfinance, Supply Chain Finance, "
                "Consumer Durable Loan, Credit Card. "
                "For operating_states, list full Indian state names, or ['PAN_INDIA'] if "
                "the lender explicitly states it operates across all of India. "
                "Return null (empty string for text fields, empty array for list fields) "
                "for any field not explicitly found on the page. "
                "Do NOT guess or infer values."
            ),
        },
    }

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        # Firecrawl nests extracted data under data.extract
        extracted = (data.get("data") or data).get("extract") or {}
        markdown  = (data.get("data") or data).get("markdown") or ""
        result: dict[str, Any] = {}
        int_fields = {"established_year", "employee_count", "branch_count"}
        for k in COMPARE_FIELDS:
            val = extracted.get(k)
            # Coerce numeric strings; treat empty string / "0" / 0 as None
            if k in int_fields:
                try:
                    v = int(str(val).strip()) if val not in (None, "", "null") else None
                    result[k] = v if v else None
                except (ValueError, TypeError):
                    result[k] = None
            else:
                # Empty string → None
                result[k] = val if val not in (None, "", [], "null") else None
        result["_markdown_words"] = len(markdown.split())
        result["_status_code"]    = resp.status_code
        return result
    except requests.HTTPError as exc:
        log.warning("Firecrawl HTTP error for %s: %s", url, exc)
        return {"_error": str(exc), "_status_code": getattr(exc.response, "status_code", None)}
    except Exception as exc:
        log.warning("Firecrawl error for %s: %s", url, exc)
        return {"_error": str(exc)}


# ──────────────────────────────────────────────────────────────
# COMPARISON LOGIC
# ──────────────────────────────────────────────────────────────

def _is_empty(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, (list, dict)):
        return len(val) == 0
    return str(val).strip() == ""


def compare(url: str, current: dict, firecrawl: dict) -> dict:
    """
    Produce a per-field comparison summary.
    Returns a flat dict suitable for CSV export.
    """
    row: dict[str, Any] = {"url": url}

    agree = disagree = current_only = firecrawl_only = both_empty = 0

    for field in COMPARE_FIELDS:
        c = current.get(field)
        f = firecrawl.get(field)
        c_empty = _is_empty(c)
        f_empty = _is_empty(f)

        if c_empty and f_empty:
            status = "both_null"
            both_empty += 1
        elif c_empty and not f_empty:
            status = "firecrawl_only"
            firecrawl_only += 1
        elif not c_empty and f_empty:
            status = "current_only"
            current_only += 1
        else:
            # Both have values — do they agree?
            c_str = json.dumps(c, sort_keys=True) if isinstance(c, (list, dict)) else str(c)
            f_str = json.dumps(f, sort_keys=True) if isinstance(f, (list, dict)) else str(f)
            if c_str.lower() == f_str.lower():
                status = "agree"
                agree += 1
            else:
                status = "disagree"
                disagree += 1

        row[f"field_{field}"]        = status
        row[f"current_{field}"]      = c
        row[f"firecrawl_{field}"]    = f

    total = len(COMPARE_FIELDS)
    row["summary_agree"]         = agree
    row["summary_disagree"]      = disagree
    row["summary_current_only"]  = current_only
    row["summary_firecrawl_only"]= firecrawl_only
    row["summary_both_null"]     = both_empty
    row["current_coverage_pct"]  = round(100 * (agree + current_only + disagree) / total, 1)
    row["firecrawl_coverage_pct"]= round(100 * (agree + firecrawl_only + disagree) / total, 1)

    # Errors
    row["current_error"]   = current.get("_error", "")
    row["firecrawl_error"] = firecrawl.get("_error", "")
    row["firecrawl_markdown_words"] = firecrawl.get("_markdown_words", 0)

    return row


def print_comparison(row: dict):
    url = row["url"]
    print(f"\n{'='*70}")
    print(f"URL: {url}")
    print(f"{'='*70}")
    if row["current_error"]:
        print(f"  [current scraper ERROR] {row['current_error']}")
    if row["firecrawl_error"]:
        print(f"  [firecrawl ERROR]       {row['firecrawl_error']}")

    print(f"  {'Field':<25}  {'Status':<16}  {'Current':<25}  {'Firecrawl'}")
    print(f"  {'-'*25}  {'-'*16}  {'-'*25}  {'-'*25}")
    for field in COMPARE_FIELDS:
        status   = row.get(f"field_{field}", "")
        current  = str(row.get(f"current_{field}",   ""))[:24]
        fc_val   = str(row.get(f"firecrawl_{field}", ""))[:24]
        marker   = "=" if status == "agree" else ("!" if status == "disagree" else "-")
        print(f"  {marker} {field:<23}  {status:<16}  {current:<25}  {fc_val}")

    print(f"\n  Coverage  —  current: {row['current_coverage_pct']}%  |  "
          f"firecrawl: {row['firecrawl_coverage_pct']}%  |  "
          f"agree: {row['summary_agree']}  disagree: {row['summary_disagree']}")
    if row["firecrawl_markdown_words"]:
        print(f"  Firecrawl markdown: {row['firecrawl_markdown_words']} words")


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="A/B test: current scraper vs Firecrawl")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--urls",  nargs="+", help="One or more lender URLs")
    group.add_argument("--file",  type=str,  help="Text file with one URL per line")
    parser.add_argument("--output", default="ab_test_results.csv",
                        help="CSV output path (default: ab_test_results.csv)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Seconds between requests (default: 2.0)")
    args = parser.parse_args()

    api_key = os.getenv("FIRECRAWL_API_KEY", "")
    if not api_key:
        log.error("FIRECRAWL_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    if args.file:
        urls = [u.strip() for u in Path(args.file).read_text().splitlines() if u.strip()]
    else:
        urls = args.urls

    log.info("Testing %d URL(s)…", len(urls))
    all_rows: list[dict] = []

    for i, url in enumerate(urls, 1):
        log.info("[%d/%d] %s", i, len(urls), url)

        log.info("  → current scraper…")
        current   = run_current_scraper(url)
        time.sleep(args.delay)

        log.info("  → firecrawl…")
        firecrawl = run_firecrawl(url, api_key)
        time.sleep(args.delay)

        row = compare(url, current, firecrawl)
        all_rows.append(row)
        print_comparison(row)

    # ── Write CSV ──
    if all_rows:
        out_path = Path(args.output)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_rows[0].keys())
            writer.writeheader()
            writer.writerows(all_rows)
        log.info("\nResults saved → %s", out_path)

    # ── Aggregate summary ──
    if len(all_rows) > 1:
        total = len(all_rows)
        avg_c  = sum(r["current_coverage_pct"]   for r in all_rows) / total
        avg_fc = sum(r["firecrawl_coverage_pct"]  for r in all_rows) / total
        print(f"\n{'='*70}")
        print(f"AGGREGATE SUMMARY ({total} URLs)")
        print(f"  Avg coverage — current: {avg_c:.1f}%  |  firecrawl: {avg_fc:.1f}%")
        errors_c  = sum(1 for r in all_rows if r["current_error"])
        errors_fc = sum(1 for r in all_rows if r["firecrawl_error"])
        print(f"  Errors       — current: {errors_c}     |  firecrawl: {errors_fc}")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
