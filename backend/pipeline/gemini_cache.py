"""
backend/pipeline/gemini_cache.py
==================================
Hash-based Gemini call deduplication.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# SHA-256 truncated to 32 hex chars = 128-bit collision resistance.
# Previous value was 24 chars (96-bit) — upgraded to match cache.py convention.
_HASH_LENGTH = 32


def compute_scrape_hash(scraped: dict) -> str:
    relevant = {
        "loan_tags":        sorted(scraped.get("loan_tags", []) or []),
        "operating_states": sorted(scraped.get("operating_states", []) or []),
        "aum_text":         (scraped.get("aum_text") or "").strip().lower(),
        "employee_text":    (scraped.get("employee_text") or "").strip().lower(),
        "contact_email":    (scraped.get("email") or "").strip().lower(),
        "contact_phone":    (scraped.get("phone") or "").strip().lower(),
    }
    payload = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:_HASH_LENGTH]


def compute_lender_hash(lender_row: dict) -> str:
    CHANGE_FIELDS = [
        "aum_crores", "primary_loan_segments", "hq_state",
        "operating_states", "employee_count", "phone", "email",
        "ticket_size_min", "ticket_size_max", "website",
    ]

    def _normalise(v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                pass
        if isinstance(v, list):
            return sorted(str(x) for x in v)
        return v

    parts = [
        f"{f}={json.dumps(_normalise(lender_row.get(f)), sort_keys=True)}"
        for f in CHANGE_FIELDS
    ]
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()[:_HASH_LENGTH]


class GeminiCache:
    """
    Checks whether a lender's data has changed since the last successful extraction.
    Returns False (conservative) on any error — never skips Gemini when uncertain.
    """

    def __init__(self, db_url: Optional[str] = None):
        self._db_url = db_url
        self._cache: dict[int, str] = {}
        self._loaded = False
        self._load_errors = 0
        self._hits  = 0
        self._misses = 0

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._db_url:
            import os
            self._db_url = os.environ.get("DATABASE_URL", "")
        if not self._db_url:
            logger.warning("GeminiCache: DATABASE_URL not set — cache disabled (all lenders re-extracted)")
            self._loaded = True
            return
        try:
            import psycopg2
            conn = psycopg2.connect(self._db_url, connect_timeout=10)
            with conn.cursor() as cur:
                cur.execute("SELECT id, data_hash FROM lenders WHERE data_hash IS NOT NULL")
                for row in cur.fetchall():
                    self._cache[row[0]] = row[1]
            conn.close()
            logger.info("GeminiCache: loaded %d hashes from DB", len(self._cache))
        except Exception as exc:
            self._load_errors += 1
            logger.warning("GeminiCache: failed to load from DB: %s — will re-extract all lenders", exc)
        self._loaded = True

    def is_unchanged(self, lender_id: int, new_hash: str) -> bool:
        self._ensure_loaded()
        stored = self._cache.get(lender_id)
        if not stored:
            self._misses += 1
            return False
        unchanged = stored == new_hash
        if unchanged:
            self._hits += 1
            logger.debug("GeminiCache: lender %d unchanged — skip Gemini", lender_id)
        else:
            self._misses += 1
        return unchanged

    def update_hash(self, lender_id: int, new_hash: str) -> None:
        self._cache[lender_id] = new_hash

    def invalidate(self, lender_id: int) -> None:
        self._cache.pop(lender_id, None)

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "cached_lenders": len(self._cache),
            "hits":           self._hits,
            "misses":         self._misses,
            "hit_rate":       round(self._hits / total, 3) if total > 0 else 0.0,
            "load_errors":    self._load_errors,
        }
