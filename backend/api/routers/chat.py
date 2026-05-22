# backend/api/routers/chat.py
"""
POST /v1/chat      — Send a message, get AI response + lender results
GET  /v1/chat/history — Load recent messages for a session
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.auth import get_current_user
from core.cache import get_cache, make_key, CacheTTL
from core.config import cfg
from core.ai_client import get_ai_client
from core.constants import VALID_LOAN_TYPES, VALID_COMPANY_TYPES, VALID_AUM_CATEGORIES
from core.embeddings import embed_query, EmbeddingUnavailableError
from dependencies import get_db
from limiter import limiter

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_SORT_COLS = {
    "aum_crores", "established_year", "employee_count",
    "branch_count", "quality_score", "company_name",
}

# Words to ignore when doing QA keyword search
_QA_STOP = {
    "what", "which", "where", "when", "who", "why", "how", "does", "do", "did",
    "is", "are", "was", "were", "has", "have", "had", "will", "would", "should",
    "could", "can", "tell", "show", "find", "list", "give", "about", "with",
    "from", "that", "this", "their", "there", "they", "some", "more", "any",
    "the", "and", "for", "its", "also",
    # 3-char noise words now reachable after len >= 3 change
    "get", "got", "set", "not", "but", "yet", "all", "top", "big", "new",
    "old", "low", "two", "one", "per", "let", "use", "put", "say", "see",
}

# Filters dropped when broadening a zero-result query (most restrictive first)
_BROADENING_DROP_ORDER = [
    "state", "states", "aum_category",
    "aum_min", "aum_max",
    "established_year_min", "established_year_max",
    "operating_intensity", "pan_india", "business_sector",
    "loan_type", "company_type",
]

_SIMILARITY_TRIGGERS = frozenset({"similar to", "like ", "more like", "similar lenders", "lenders like", "lenders similar"})

_GREETING_TOKENS = frozenset({
    "hi", "hello", "hey", "hii", "helo", "yo", "sup",
    "thanks", "thank you", "ty", "thx", "thankyou",
    "namaste", "namaskar", "jai hind",
    "what can you do", "what can you help", "help me", "how can you help",
})

# Common loan-type phrasings not in VALID_LOAN_TYPES — map to canonical value.
# Prevents LLM from misclassifying obvious finance queries as out_of_scope.
_LOAN_TYPE_SYNONYMS: dict[str, str] = {
    # Vehicle / car
    "car loan": "Vehicle Loan",
    "car loans": "Vehicle Loan",
    "auto loan": "Vehicle Loan",
    "automobile loan": "Vehicle Loan",
    "used car loan": "Vehicle Loan",
    "pre owned car loan": "Vehicle Loan",
    "pre-owned car loan": "Vehicle Loan",
    "preowned car loan": "Vehicle Loan",
    "second hand car loan": "Vehicle Loan",
    "old car loan": "Vehicle Loan",
    "four wheeler loan": "Vehicle Loan",
    "4 wheeler loan": "Vehicle Loan",
    "four-wheeler loan": "Vehicle Loan",
    "commercial vehicle loan": "Vehicle Loan",
    "truck loan": "Vehicle Loan",
    # Two-wheeler
    "bike loan": "Two Wheeler Loan",
    "scooter loan": "Two Wheeler Loan",
    "motorbike loan": "Two Wheeler Loan",
    "two-wheeler loan": "Two Wheeler Loan",
    # EV
    "ev loan": "EV Loan",
    "electric vehicle loan": "EV Loan",
    "electric car loan": "EV Loan",
    "ev loans": "EV Loan",
    # Home
    "housing loan": "Home Loan",
    "house loan": "Home Loan",
    "home purchase loan": "Home Loan",
    "home finance": "Home Loan",
    "residential loan": "Home Loan",
    # Gold
    "jewel loan": "Gold Loan",
    "jewellery loan": "Gold Loan",
    "jewelry loan": "Gold Loan",
    "ornament loan": "Gold Loan",
    # Personal
    "salary loan": "Personal Loan",
    "cash loan": "Personal Loan",
    "instant loan": "Personal Loan",
    "quick loan": "Personal Loan",
    "emergency loan": "Personal Loan",
    # Education
    "study loan": "Education Loan",
    "student loan": "Education Loan",
    "education finance": "Education Loan",
    # Agriculture
    "tractor loan": "Agriculture Loan",
    "kisan loan": "Agriculture Loan",
    "farm loan": "Agriculture Loan",
    "agri loan": "Agriculture Loan",
    "agriculture finance": "Agriculture Loan",
    # LAP
    "lap": "Loan Against Property",
    "mortgage loan": "Loan Against Property",
    "property loan": "Loan Against Property",
    # Supply chain
    "supply chain": "Supply Chain Finance",
    "invoice discounting": "Supply Chain Finance",
    "dealer finance": "Supply Chain Finance",
    # Working capital
    "overdraft": "Working Capital",
    "cash credit": "Working Capital",
    "od limit": "Working Capital",
    # Microfinance
    "jlg loan": "Microfinance",
    "shg loan": "Microfinance",
    "mfi loan": "Microfinance",
    "micro finance loan": "Microfinance",
}

# Words that prove a message is finance-related — used as a safety net after LLM
_FINANCE_SIGNALS = frozenset({
    "loan", "loans", "lender", "lenders", "nbfc", "bank", "banks", "credit",
    "finance", "interest", "emi", "rate", "borrow", "lending", "financial",
    "mortgage", "aum", "nbfcs", "sfb", "mfi", "rbi", "npa", "mudra",
})


def _is_similarity_query(message: str) -> bool:
    msg = message.lower()
    return any(t in msg for t in _SIMILARITY_TRIGGERS)


def _quick_classify(message: str) -> Optional[dict]:
    """Rule-based pre-classifier for deterministic patterns — skips AI call entirely.
    Returns parsed intent dict or None if AI classification is needed.
    """
    msg = message.strip().lower()
    empty = {"intent": "", "filters": {}, "compare_names": [], "detail_names": []}

    # Greetings / small talk
    if msg in _GREETING_TOKENS or any(msg.startswith(g) for g in ("hi ", "hello ", "hey ")):
        return {**empty, "intent": "greeting"}

    # Synonym-matched loan phrases (before exact-match loop so "car loan" beats nothing)
    lt_syn = _LOAN_TYPE_SYNONYMS.get(msg)
    if lt_syn:
        return {**empty, "intent": "filter", "filters": {"loan_type": [lt_syn]}}
    # Also check "X lenders" suffix for synonyms
    if msg.endswith(" lenders"):
        lt_syn = _LOAN_TYPE_SYNONYMS.get(msg[: -len(" lenders")].rstrip())
        if lt_syn:
            return {**empty, "intent": "filter", "filters": {"loan_type": [lt_syn]}}

    # Single loan type (e.g. "gold loan", "home loan", "vehicle loan")
    for lt in VALID_LOAN_TYPES:
        if msg == lt.lower() or msg == lt.lower() + "s" or msg == lt.lower() + " lenders":
            return {**empty, "intent": "filter", "filters": {"loan_type": [lt]}}

    # Single company type (e.g. "nbfc", "small finance bank")
    for ct in VALID_COMPANY_TYPES:
        if msg == ct.lower() or msg == ct.lower() + "s" or msg == ct.lower() + " lenders":
            return {**empty, "intent": "filter", "filters": {"company_type": [ct]}}

    return None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class HistoryMessage(BaseModel):
    role:    str  # "user" | "assistant"
    content: str = Field(..., max_length=2000)


class ChatRequest(BaseModel):
    message:           str       = Field(..., min_length=1, max_length=1000)
    session_id:        str       = Field(..., description="UUID generated by frontend")
    history:           list[HistoryMessage] = Field(default_factory=list, max_length=40)  # 20 turns max
    last_filters:      Optional[dict] = None       # last applied filter state for multi-turn refinement
    last_lender_names: Optional[list[str]] = Field(default=None, max_length=10)  # top lender names from last response for pronoun resolution


class LenderResult(BaseModel):
    id:                    int
    company_name:          str
    company_type:          str
    rbi_category:          Optional[str] = None
    aum_crores:            Optional[float] = None
    aum_category:          Optional[str] = None
    hq_state:              Optional[str] = None
    hq_location:           Optional[str] = None
    pan_india:             bool = False
    primary_loan_segments: list[str] = Field(default_factory=list)
    operating_states:      list[str] = Field(default_factory=list)
    website:               Optional[str] = None
    quality_score:         Optional[float] = None
    employee_count:        Optional[int] = None
    established_year:      Optional[int] = None
    is_listed:             bool = False
    phone:                 Optional[str] = None
    email:                 Optional[str] = None
    operating_intensity:   Optional[str] = None
    business_sector:       Optional[str] = None


class ChatResponse(BaseModel):
    answer:            str
    intent:            str
    lenders:           list[LenderResult] = Field(default_factory=list)
    total_count:       int = 0
    applied_filters:   Optional[dict] = None
    unmatched_names:   list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    session_id:        str
    message_id:        Optional[int] = None


class FeedbackRequest(BaseModel):
    session_id: str = Field(..., description="UUID of the chat session")
    message_id: int = Field(..., description="ID of the assistant message being rated")
    rating:     str = Field(..., description="'up' or 'down'")


class HistoryResponse(BaseModel):
    session_id: str
    messages:   list[dict]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_arr(val: Any) -> list:
    import json as _json
    if val is None:
        return []
    if isinstance(val, list):
        return val
    try:
        return _json.loads(val)
    except Exception:
        return []


def _row_to_lender(row: Any) -> LenderResult:
    d = dict(row)
    return LenderResult(
        id=d["id"],
        company_name=d["company_name"],
        company_type=d["company_type"],
        rbi_category=d.get("rbi_category"),
        aum_crores=d.get("aum_crores"),
        aum_category=d.get("aum_category"),
        hq_state=d.get("hq_state"),
        hq_location=d.get("hq_location"),
        pan_india=bool(d.get("pan_india", False)),
        primary_loan_segments=_parse_arr(d.get("primary_loan_segments")),
        operating_states=_parse_arr(d.get("operating_states")),
        website=d.get("website"),
        quality_score=d.get("quality_score"),
        employee_count=d.get("employee_count"),
        established_year=d.get("established_year"),
        is_listed=bool(d.get("is_listed", False)),
        phone=d.get("phone"),
        email=d.get("email"),
        operating_intensity=d.get("operating_intensity"),
        business_sector=d.get("business_sector"),
    )


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _merge_filters(base: Optional[dict], override: dict) -> dict:
    """Merge last_filters (base) with newly parsed filters (override takes precedence)."""
    merged = dict(base or {})
    for k, v in override.items():
        if v is None:
            continue
        if isinstance(v, list) and not v:
            continue
        merged[k] = v
    return merged


def _normalize_filters(filters: dict) -> dict:
    """Sort list values so the cache key is stable regardless of order."""
    return {k: sorted(v) if isinstance(v, list) else v for k, v in filters.items()}


_PRONOUN_TRIGGERS = {"it", "that", "those", "them", "one", "ones", "first", "second", "third", "1st", "2nd", "3rd"}


def _inject_lender_context(message: str, last_lender_names: Optional[list[str]]) -> str:
    """Prepend recently mentioned lender names if message has vague references."""
    if not last_lender_names:
        return message
    words = set(message.lower().split())
    if words & _PRONOUN_TRIGGERS:
        safe_names = [
            n[:80].replace("[", "").replace("]", "").replace("\n", " ").strip()
            for n in last_lender_names[:3]
            if n and isinstance(n, str)
        ]
        if not safe_names:
            return message
        context = ", ".join(safe_names)
        return f"[Context — recently mentioned lenders: {context}] {message}"
    return message


def _generate_suggestions(intent: str, lenders: list[LenderResult], filters: dict) -> list[str]:
    names = [l.company_name for l in lenders[:3]]
    s: list[str] = []
    if intent == "filter":
        if names:
            s.append(f"Tell me about {names[0]}")
        if len(names) >= 2:
            s.append(f"Compare {names[0]} vs {names[1]}")
        if not filters.get("loan_type"):
            s.append("Show MSME loan lenders")
        elif not filters.get("aum_category"):
            s.append("Show only large AUM")
    elif intent == "compare":
        if names:
            s.append(f"Tell me about {names[0]}")
            s.append(f"Find more lenders like {names[0]}")
    elif intent == "lender_detail":
        if names:
            s.append(f"Find similar lenders to {names[0]}")
            s.append(f"Compare {names[0]} with another lender")
    elif intent == "qa" and names:
        s.append(f"Tell me about {names[0]}")
    return s[:3]


_NAME_STOP = {
    "the", "and", "of", "a", "an", "ltd", "limited", "pvt", "private", "india", "indian",
    "finance", "financial", "capital", "credit", "services", "bank", "banking",
    "investment", "investments", "holdings", "group", "enterprises", "solutions",
    "microfinance", "leasing", "asset", "assets", "management", "fund", "funds",
}

import re as _re
_STRIP_SUFFIX_CHAT = _re.compile(
    r'\s*(private\s+limited|private\s+ltd\.?|limited|ltd\.?|pvt\.?)$',
    _re.IGNORECASE,
)

def _dedup_lenders(lenders: list[LenderResult]) -> list[LenderResult]:
    """Remove near-duplicates (e.g. 'HDFC Bank' vs 'HDFC Bank Limited')."""
    seen: dict[str, LenderResult] = {}
    for l in lenders:
        key = _STRIP_SUFFIX_CHAT.sub('', l.company_name.strip()).strip().lower()
        if key not in seen:
            seen[key] = l
        else:
            prev = seen[key]
            if (l.quality_score or 0, l.aum_crores or 0) > (prev.quality_score or 0, prev.aum_crores or 0):
                seen[key] = l
    return list(seen.values())


def _compute_unmatched_names(requested: list[str], found: list[LenderResult]) -> list[str]:
    """Return requested names with no distinctive-word match in found results."""
    found_names = [l.company_name.lower() for l in found]
    unmatched = []
    for name in requested:
        # Use only distinctive words (exclude generic finance terms)
        words = [w for w in name.lower().split() if len(w) > 2 and w not in _NAME_STOP]
        if not words:
            # Fall back to all words if everything was generic
            words = [w for w in name.lower().split() if len(w) > 2]
        if not words or not any(any(w in fn for w in words) for fn in found_names):
            unmatched.append(name)
    return unmatched


async def _search_lenders(db: asyncpg.Pool, filters: dict) -> tuple[list[LenderResult], int]:
    conditions = ["approval_status = 'approved'"]
    params: list = []
    idx = 1

    q = (filters.get("q") or "").strip()
    if q:
        q_esc = _esc(q)
        conditions.append(f"company_name ILIKE ${idx}")
        params.append(f"%{q_esc}%")
        idx += 1

    company_type = [t for t in (filters.get("company_type") or []) if t in VALID_COMPANY_TYPES]
    if company_type:
        conditions.append(f"company_type = ANY(${idx}::text[])")
        params.append(company_type)
        idx += 1

    state  = filters.get("state")
    states = filters.get("states") or []
    if states:
        # OR across all states: lender must operate in at least one
        state_conds = " OR ".join(
            f"${idx + i} = ANY(operating_states)" for i in range(len(states))
        )
        conditions.append(f"(pan_india = true OR {state_conds})")
        params.extend(states)
        idx += len(states)
    elif state:
        conditions.append(f"(pan_india = true OR ${idx} = ANY(operating_states))")
        params.append(state)
        idx += 1

    loan_type = [t for t in (filters.get("loan_type") or []) if t in VALID_LOAN_TYPES]
    if loan_type:
        lt_conds = []
        for lt in loan_type:
            lt_conds.append(f"${idx} = ANY(primary_loan_segments)")
            params.append(lt)
            idx += 1
        conditions.append(f"({' OR '.join(lt_conds)})")

    aum_category = [t for t in (filters.get("aum_category") or []) if t in VALID_AUM_CATEGORIES]
    if aum_category:
        conditions.append(f"aum_category = ANY(${idx}::text[])")
        params.append(aum_category)
        idx += 1

    if filters.get("aum_min") is not None:
        conditions.append(f"aum_crores >= ${idx}")
        params.append(float(filters["aum_min"]))
        idx += 1

    if filters.get("aum_max") is not None:
        conditions.append(f"aum_crores <= ${idx}")
        params.append(float(filters["aum_max"]))
        idx += 1

    if filters.get("established_year_min") is not None:
        conditions.append(f"established_year >= ${idx}")
        params.append(int(filters["established_year_min"]))
        idx += 1

    if filters.get("established_year_max") is not None:
        conditions.append(f"established_year <= ${idx}")
        params.append(int(filters["established_year_max"]))
        idx += 1

    if filters.get("pan_india") is not None:
        conditions.append(f"pan_india = ${idx}")
        params.append(bool(filters["pan_india"]))
        idx += 1

    if filters.get("is_listed") is not None:
        conditions.append(f"is_listed = ${idx}")
        params.append(bool(filters["is_listed"]))
        idx += 1

    operating_intensity = filters.get("operating_intensity") or []
    if operating_intensity:
        conditions.append(f"operating_intensity = ANY(${idx}::text[])")
        params.append(operating_intensity)
        idx += 1

    business_sector = filters.get("business_sector") or []
    if business_sector:
        conditions.append(f"business_sector = ANY(${idx}::text[])")
        params.append(business_sector)
        idx += 1

    sort_by = filters.get("sort_by", "aum_crores")
    if sort_by not in ALLOWED_SORT_COLS:
        sort_by = "aum_crores"
    sort_dir = "DESC" if filters.get("sort_dir", "desc") == "desc" else "ASC"

    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, company_name, company_type, rbi_category,
                   aum_crores, aum_category, hq_state, hq_location,
                   pan_india, primary_loan_segments, operating_states,
                   website, quality_score, employee_count,
                   established_year, is_listed, phone, email,
                   operating_intensity, business_sector
            FROM lenders
            WHERE {where}
            ORDER BY {sort_by} {sort_dir} NULLS LAST
            LIMIT 20
            """,
            *params,
        )
        true_count = await conn.fetchval(
            f"SELECT COUNT(*) FROM lenders WHERE {where}",
            *params,
        )
    return [_row_to_lender(r) for r in rows], int(true_count)


async def _fetch_lenders_by_name(db: asyncpg.Pool, names: list[str]) -> list[LenderResult]:
    """Fetch lenders by name — single query covering all three match tiers via CTE:
    Tier 1: full-phrase ILIKE · Tier 2: word-level ILIKE · Tier 3: trigram similarity.
    Results ordered by tier then quality_score so best matches come first.
    """
    if not names:
        return []

    capped = names[:3]

    # Compute patterns in Python so they can be passed as params
    phrase_patterns = [f"%{_esc(n)}%" for n in capped]

    word_patterns: list[str] = []
    for name in capped:
        words = [w for w in name.lower().split() if len(w) > 2 and w not in _NAME_STOP]
        word_patterns.extend([f"%{_esc(w)}%" for w in words[:3]])
    if not word_patterns:
        for name in capped:
            words = [w for w in name.lower().split() if len(w) > 2]
            word_patterns.extend([f"%{_esc(w)}%" for w in words[:2]])

    # Build trigram OR conditions for up to 3 names
    trgm_conditions = " OR ".join(
        f"similarity(company_name, ${i + 3}) > 0.25"
        for i in range(len(capped))
    )
    # similarity() ORDER BY needs to pick one representative score
    trgm_order = " + ".join(
        f"similarity(company_name, ${i + 3})"
        for i in range(len(capped))
    )

    _cols = """id, company_name, company_type, rbi_category,
               aum_crores, aum_category, hq_state, hq_location,
               pan_india, primary_loan_segments, operating_states,
               website, quality_score, employee_count,
               established_year, is_listed, phone, email,
               operating_intensity, business_sector"""

    query = f"""
        WITH
        tier1 AS (
            SELECT {_cols}, 1 AS _tier
            FROM lenders
            WHERE approval_status = 'approved'
              AND company_name ILIKE ANY($1::text[])
        ),
        tier2 AS (
            SELECT {_cols}, 2 AS _tier
            FROM lenders
            WHERE approval_status = 'approved'
              AND company_name ILIKE ANY($2::text[])
              AND id NOT IN (SELECT id FROM tier1)
        ),
        tier3 AS (
            SELECT {_cols}, 3 AS _tier
            FROM lenders
            WHERE approval_status = 'approved'
              AND ({trgm_conditions})
              AND id NOT IN (SELECT id FROM tier1)
              AND id NOT IN (SELECT id FROM tier2)
            ORDER BY {trgm_order} DESC
            LIMIT 3
        )
        SELECT * FROM tier1
        UNION ALL
        SELECT * FROM tier2
        UNION ALL
        SELECT * FROM tier3
        ORDER BY _tier, quality_score DESC NULLS LAST
        LIMIT 9
    """

    params: list = [phrase_patterns, word_patterns or phrase_patterns, *capped]

    async with db.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return _dedup_lenders([_row_to_lender(r) for r in rows])


async def _search_lenders_for_qa(db: asyncpg.Pool, message: str) -> list[LenderResult]:
    """Extract keywords from a QA message and find matching lenders for context.

    Searches company_name, primary_loan_segments, operating_states, and hq_state
    so questions like "who lends to farmers in Rajasthan?" get grounded DB context.
    """
    words = [w for w in message.lower().split() if len(w) >= 3 and w not in _QA_STOP]
    if not words:
        return []
    patterns = [f"%{_esc(w)}%" for w in words[:4]]
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, company_name, company_type, rbi_category,
                   aum_crores, aum_category, hq_state, hq_location,
                   pan_india, primary_loan_segments, operating_states,
                   website, quality_score, employee_count,
                   established_year, is_listed, phone, email,
                   operating_intensity, business_sector
            FROM lenders
            WHERE approval_status = 'approved'
              AND (
                company_name ILIKE ANY($1::text[])
                OR primary_loan_segments::text ILIKE ANY($1::text[])
                OR operating_states::text ILIKE ANY($1::text[])
                OR hq_state ILIKE ANY($1::text[])
              )
            ORDER BY quality_score DESC NULLS LAST, aum_crores DESC NULLS LAST
            LIMIT 8
            """,
            patterns,
        )
    return [_row_to_lender(r) for r in rows]


async def _search_lenders_semantic(
    db: asyncpg.Pool,
    query: str,
    limit: int = 20,
) -> list[LenderResult]:
    """Find lenders by vector similarity to query. Falls back to keyword search on error."""
    try:
        vector = await asyncio.get_running_loop().run_in_executor(None, embed_query, query)
    except EmbeddingUnavailableError:
        logger.warning("semantic search: embedding unavailable, falling back to keyword search")
        return await _search_lenders_for_qa(db, query)

    vec_literal = "[" + ",".join(f"{v:.8f}" for v in vector) + "]"
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, company_name, company_type, rbi_category,
                   aum_crores, aum_category, hq_state, hq_location,
                   pan_india, primary_loan_segments, operating_states,
                   website, quality_score, employee_count,
                   established_year, is_listed, phone, email,
                   operating_intensity, business_sector
            FROM lenders
            WHERE approval_status = 'approved'
              AND embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vec_literal, limit,
        )
    return [_row_to_lender(r) for r in rows]


async def _search_with_broadening(
    db: asyncpg.Pool, filters: dict
) -> tuple[list[LenderResult], dict, str, int]:
    """Search lenders with automatic filter broadening on zero results.
    Returns (lenders, applied_filters, broadening_note, db_total).
    On zero results, all broadening levels run in parallel via asyncio.gather()
    instead of sequentially — time = slowest query, not sum of all queries.
    """
    lenders, total = await _search_lenders(db, filters)
    lenders = _dedup_lenders(lenders)
    if lenders:
        return lenders, filters, "", total

    # Build all broadening levels upfront
    levels: list[tuple[list[str], dict]] = []
    broad_filters = dict(filters)
    dropped: list[str] = []
    for key in _BROADENING_DROP_ORDER:
        if key in broad_filters:
            del broad_filters[key]
            dropped.append(key)
            levels.append((list(dropped), dict(broad_filters)))

    if not levels:
        return [], filters, "", 0

    # Run all levels in parallel
    results = await asyncio.gather(
        *[_search_lenders(db, f) for _, f in levels],
        return_exceptions=True,
    )

    # Pick the least-broadened level that has results
    for (dropped_keys, level_filters), result in zip(levels, results):
        if isinstance(result, Exception):
            continue
        lenders, total = result
        lenders = _dedup_lenders(lenders)
        if lenders:
            note = (
                f"No exact match with all filters — dropped {', '.join(dropped_keys)} "
                f"to show nearby results."
            )
            return lenders, level_filters, note, total

    return [], filters, "", 0


async def _ensure_session(db: asyncpg.Pool, session_id: str, user_id: str) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chat_sessions (id, user_id)
            VALUES ($1::uuid, $2::uuid)
            ON CONFLICT (id) DO UPDATE SET last_active = now()
            """,
            session_id, user_id,
        )


async def _save_turn(
    db: asyncpg.Pool,
    session_id: str,
    user_msg: str,
    assistant_msg: str,
    intent: str,
    filters_used: Optional[dict],
    refusal: bool = False,
    lender_names: Optional[list[str]] = None,
) -> Optional[int]:
    import json as _json
    meta = dict(filters_used or {})
    if lender_names:
        meta["_lender_names"] = lender_names[:3]
    filters_meta = meta if meta else None
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO chat_messages (session_id, role, content) VALUES ($1::uuid, 'user', $2)",
                session_id, user_msg,
            )
            if refusal:
                row = await conn.fetchrow(
                    """
                    INSERT INTO chat_messages (session_id, role, content, intent, filters_used, refusal)
                    VALUES ($1::uuid, 'assistant', $2, $3, $4::jsonb, true)
                    RETURNING id
                    """,
                    session_id, assistant_msg, intent,
                    _json.dumps(filters_meta) if filters_meta else None,
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO chat_messages (session_id, role, content, intent, filters_used)
                    VALUES ($1::uuid, 'assistant', $2, $3, $4::jsonb)
                    RETURNING id
                    """,
                    session_id, assistant_msg, intent,
                    _json.dumps(filters_meta) if filters_meta else None,
                )
            return int(row["id"]) if row else None


def _format_history_for_gemini(history: list[HistoryMessage], max_turns: int) -> list[dict]:
    recent = history[-(max_turns * 2):]
    return [
        {
            "role": "user" if m.role == "user" else "model",
            "parts": [m.content],
        }
        for m in recent
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/ping")
@limiter.limit("20/minute")
async def chat_ping(request: Request, user: dict = Depends(get_current_user)):
    """Returns whether the AI service is configured and reachable."""
    import os
    configured = bool(os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("GEMINI_API_KEY", ""))
    return {"ai_available": configured}


@router.post("", response_model=ChatResponse)
@limiter.limit("20/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    db: asyncpg.Pool = Depends(get_db),
    user: dict = Depends(get_current_user),
    cache=Depends(get_cache),
):
    user_id = user.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user token")

    try:
        uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="session_id must be a valid UUID")

    # Session persistence is best-effort — if chat tables don't exist yet we still serve responses.
    session_ok = True
    try:
        await _ensure_session(db, body.session_id, user_id)
    except Exception as exc:
        logger.warning("chat: session upsert failed (migration pending?): %s", exc)
        session_ok = False

    gemini_history = _format_history_for_gemini(body.history, cfg.chat_context_turns)

    # ------------------------------------------------------------------
    # Pass 1 — classify intent + extract entities
    # ------------------------------------------------------------------
    # Inject lender context for pronoun/ordinal references ("the first one", "it", etc.)
    classified_message = _inject_lender_context(body.message, body.last_lender_names)

    # Quick rule-based classifier — skips AI entirely for obvious patterns
    parsed = _quick_classify(body.message) if not body.last_lender_names else None

    # Intent cache — skip when last_lender_names is set (pronoun resolution is context-dependent)
    _intent_cache_key = None
    if parsed is None and not body.last_lender_names:
        _intent_cache_key = make_key("chat:intent", {"msg": body.message.strip().lower()})
        parsed = await cache.get(_intent_cache_key)
        if parsed:
            logger.debug("chat: intent cache HIT")

    try:
        client = get_ai_client()
    except ValueError:
        raise HTTPException(status_code=503, detail="AI_NOT_CONFIGURED")

    if parsed is None:
        try:
            parsed = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, client.parse_intent, classified_message, gemini_history
                ),
                timeout=cfg.chat_intent_timeout_secs,
            )
        except asyncio.TimeoutError:
            logger.error("chat: intent parse timed out after %ds", cfg.chat_intent_timeout_secs)
            raise HTTPException(status_code=503, detail="AI_TIMEOUT")
        except Exception as exc:
            logger.error("chat: intent parse error: %s", exc)
            raise HTTPException(status_code=503, detail="AI_UNAVAILABLE")
        if _intent_cache_key:
            await cache.set(_intent_cache_key, parsed, ttl=CacheTTL.DETAIL)

    intent        = parsed.get("intent", "qa")
    filters       = parsed.get("filters") or {}
    compare_names = parsed.get("compare_names") or []
    detail_names  = parsed.get("detail_names") or []

    # Safety net: LLM sometimes mis-fires out_of_scope on valid finance queries.
    if intent == "out_of_scope":
        msg_words = set(body.message.lower().split())
        if msg_words & _FINANCE_SIGNALS:
            logger.warning("chat: overriding out_of_scope→qa for finance message: %r", body.message[:80])
            intent = "qa"

    # ------------------------------------------------------------------
    # Short-circuit for intents that need no DB lookup
    # ------------------------------------------------------------------
    if intent in ("greeting", "out_of_scope", "concept"):
        try:
            answer = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: client.generate_grounded_answer(
                        body.message, intent, [], gemini_history
                    ),
                ),
                timeout=cfg.chat_answer_timeout_secs,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="AI_TIMEOUT")
        message_id = None
        if session_ok:
            try:
                message_id = await _save_turn(
                    db, body.session_id, body.message, answer, intent, None,
                    refusal=(intent == "out_of_scope"),
                )
            except Exception as exc:
                logger.warning("chat: failed to save turn: %s", exc)
        return ChatResponse(
            answer=answer,
            intent=intent,
            lenders=[],
            applied_filters=None,
            unmatched_names=[],
            session_id=body.session_id,
            message_id=message_id,
        )

    # ------------------------------------------------------------------
    # DB lookup — always driven by intent
    # Cache key covers intent + query/names so identical queries skip DB.
    # ------------------------------------------------------------------
    lenders: list[LenderResult] = []
    applied_filters: Optional[dict] = None
    unmatched_names: list[str] = []

    _lender_cache_key = None
    _cached_lender_payload = None

    if intent in ("filter", "qa", "compare", "lender_detail"):
        _lender_cache_params: dict = {"intent": intent}
        if intent == "filter":
            # Key on the *extracted filter dict* so "NBFCs in Maharashtra" and
            # "Show NBFCs from Maharashtra" share a cache entry when filters match.
            _lender_cache_params["filters"] = _normalize_filters(
                _merge_filters(body.last_filters, filters)
            )
        elif intent == "qa":
            _lender_cache_params["query"] = body.message.strip().lower()
        elif intent == "compare":
            _lender_cache_params["names"] = sorted(compare_names)
        elif intent == "lender_detail":
            _lender_cache_params["names"] = detail_names[:1]
        _lender_cache_key = make_key("chat:lenders", _lender_cache_params)
        _cached_lender_payload = await cache.get(_lender_cache_key)

    total_count: int = 0

    if _cached_lender_payload is not None:
        logger.debug("chat: lender cache HIT")
        lenders = [LenderResult(**d) for d in _cached_lender_payload["lenders"]]
        applied_filters = _cached_lender_payload.get("applied_filters")
        unmatched_names = _cached_lender_payload.get("unmatched_names", [])
        total_count = _cached_lender_payload.get("total_count", len(lenders))
    else:
        try:
            if intent == "filter":
                merged = _merge_filters(body.last_filters, filters)
                lenders, applied_filters, _, total_count = await _search_with_broadening(db, merged)
                lenders = _dedup_lenders(lenders)

            elif intent == "qa":
                lenders = _dedup_lenders(
                    await _search_lenders_semantic(db, body.message, cfg.embedding_top_k)
                )

            elif intent == "compare" and compare_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, compare_names))
                unmatched_names = _compute_unmatched_names(compare_names, lenders)
                if len(lenders) == 1 and "another" in body.message.lower():
                    async with db.acquire() as conn:
                        alt = await conn.fetchrow(
                            """
                            SELECT id, company_name, company_type, rbi_category,
                                   aum_crores, aum_category, hq_state, hq_location,
                                   pan_india, primary_loan_segments, operating_states,
                                   website, quality_score, employee_count,
                                   established_year, is_listed, phone, email,
                                   operating_intensity, business_sector
                            FROM lenders
                            WHERE approval_status = 'approved'
                              AND company_type = $1 AND id != $2
                            ORDER BY quality_score DESC NULLS LAST, aum_crores DESC NULLS LAST
                            LIMIT 1
                            """,
                            lenders[0].company_type, lenders[0].id,
                        )
                    if alt:
                        lenders.append(_row_to_lender(alt))
                        unmatched_names = []

            elif intent == "lender_detail" and detail_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, detail_names[:1]))

        except Exception as exc:
            logger.error("chat: DB query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Search temporarily unavailable")

        if _lender_cache_key and lenders:
            await cache.set(_lender_cache_key, {
                "lenders":         [l.model_dump() for l in lenders],
                "applied_filters": applied_filters,
                "unmatched_names": unmatched_names,
                "total_count":     total_count,
            }, ttl=CacheTTL.MATCH)

    # ------------------------------------------------------------------
    # Pass 2 — generate answer strictly from DB records
    # ------------------------------------------------------------------
    lender_dicts = [l.model_dump() for l in lenders]

    # For state-filtered queries, inject an explicit context note so the AI
    # never confuses the HQ(headquartered) breakdown with operating coverage.
    _answer_note = ""
    if intent == "filter" and applied_filters:
        _s = applied_filters.get("state")
        _ss = applied_filters.get("states") or []
        if _s:
            _answer_note = (
                f"FILTER ACTIVE: state={_s!r} — ALL {total_count} matching lenders "
                f"OPERATE IN {_s} (most are headquartered elsewhere). "
                f"The HQ(headquartered) breakdown below is irrelevant to the count."
            )
        elif _ss:
            _states_str = " or ".join(_ss)
            _answer_note = (
                f"FILTER ACTIVE: states={_ss!r} — ALL {total_count} matching lenders "
                f"OPERATE IN {_states_str} (most are headquartered elsewhere). "
                f"The HQ(headquartered) breakdown below is irrelevant to the count."
            )

    try:
        answer = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: client.generate_grounded_answer(
                    body.message, intent, lender_dicts, gemini_history,
                    note=_answer_note,
                    total_count=total_count,
                ),
            ),
            timeout=cfg.chat_answer_timeout_secs,
        )
    except asyncio.TimeoutError:
        logger.error("chat: answer generation timed out after %ds", cfg.chat_answer_timeout_secs)
        raise HTTPException(status_code=503, detail="AI_TIMEOUT")
    except Exception as exc:
        logger.error("chat: answer generation failed: %s", exc)
        raise HTTPException(status_code=503, detail="AI_UNAVAILABLE")

    message_id = None
    if session_ok:
        try:
            lender_names = [l.company_name for l in lenders[:3]] if lenders else None
            message_id = await _save_turn(
                db, body.session_id, body.message, answer, intent, applied_filters,
                lender_names=lender_names,
            )
        except Exception as exc:
            logger.warning("chat: failed to save turn: %s", exc)

    suggested_actions = _generate_suggestions(intent, lenders, applied_filters or {})

    return ChatResponse(
        answer=answer,
        intent=intent,
        lenders=lenders,
        total_count=total_count,
        applied_filters=applied_filters,
        unmatched_names=unmatched_names,
        suggested_actions=suggested_actions,
        session_id=body.session_id,
        message_id=message_id,
    )


@router.get("/history", response_model=HistoryResponse)
@limiter.limit("30/minute")
async def get_history(
    request: Request,
    session_id: Optional[str] = Query(None),
    db: asyncpg.Pool = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user token")

    try:
        async with db.acquire() as conn:
            if session_id:
                try:
                    uuid.UUID(session_id)
                except ValueError:
                    raise HTTPException(status_code=422, detail="session_id must be a valid UUID")
                owner = await conn.fetchval(
                    "SELECT user_id FROM chat_sessions WHERE id = $1::uuid",
                    session_id,
                )
                if str(owner) != user_id:
                    raise HTTPException(status_code=404, detail="Session not found")
                sid = session_id
            else:
                sid = await conn.fetchval(
                    "SELECT id FROM chat_sessions WHERE user_id = $1::uuid ORDER BY last_active DESC LIMIT 1",
                    user_id,
                )
                if not sid:
                    return HistoryResponse(session_id="", messages=[])

            rows = await conn.fetch(
                """
                SELECT role, content, intent, filters_used, created_at
                FROM chat_messages
                WHERE session_id = $1::uuid
                ORDER BY created_at ASC
                LIMIT $2
                """,
                str(sid), cfg.chat_history_limit,
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_history: DB error: %s", exc)
        raise HTTPException(status_code=503, detail="History service temporarily unavailable")

    messages = [
        {
            "role":         r["role"],
            "content":      r["content"],
            "intent":       r["intent"],
            "filters_used": r["filters_used"],
            "created_at":   r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return HistoryResponse(session_id=str(sid), messages=messages)


# ---------------------------------------------------------------------------
# Streaming endpoint (SSE)
# ---------------------------------------------------------------------------

@router.post("/stream")
@limiter.limit("20/minute")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    db: asyncpg.Pool = Depends(get_db),
    user: dict = Depends(get_current_user),
    cache=Depends(get_cache),
):
    """SSE streaming version of /v1/chat. Yields meta → token* → done events."""
    import json as _json

    user_id = user.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user token")

    try:
        uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="session_id must be a valid UUID")

    session_ok = True
    try:
        await _ensure_session(db, body.session_id, user_id)
    except Exception as exc:
        logger.warning("chat_stream: session upsert failed: %s", exc)
        session_ok = False

    gemini_history    = _format_history_for_gemini(body.history, cfg.chat_context_turns)
    classified_msg    = _inject_lender_context(body.message, body.last_lender_names)

    # Quick rule-based classifier — skips AI entirely for obvious patterns
    parsed = _quick_classify(body.message) if not body.last_lender_names else None

    # Intent cache — skip for context-dependent pronoun resolution
    _intent_cache_key = None
    if parsed is None and not body.last_lender_names:
        _intent_cache_key = make_key("chat:intent", {"msg": body.message.strip().lower()})
        parsed = await cache.get(_intent_cache_key)
        if parsed:
            logger.debug("chat_stream: intent cache HIT")

    try:
        client = get_ai_client()
    except ValueError:
        raise HTTPException(status_code=503, detail="AI_NOT_CONFIGURED")

    if parsed is None:
        try:
            parsed = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, client.parse_intent, classified_msg, gemini_history
                ),
                timeout=cfg.chat_intent_timeout_secs,
            )
        except asyncio.TimeoutError:
            logger.error("chat_stream: intent parse timed out after %ds", cfg.chat_intent_timeout_secs)
            raise HTTPException(status_code=503, detail="AI_TIMEOUT")
        except Exception as exc:
            logger.error("chat_stream: intent parse error: %s", exc)
            raise HTTPException(status_code=503, detail="AI_UNAVAILABLE")
        if _intent_cache_key:
            await cache.set(_intent_cache_key, parsed, ttl=CacheTTL.DETAIL)

    intent        = parsed.get("intent", "qa")
    filters       = parsed.get("filters") or {}
    compare_names = parsed.get("compare_names") or []
    detail_names  = parsed.get("detail_names") or []

    # Safety net: override spurious out_of_scope on finance messages
    if intent == "out_of_scope":
        msg_words = set(body.message.lower().split())
        if msg_words & _FINANCE_SIGNALS:
            logger.warning("chat_stream: overriding out_of_scope→qa for finance message: %r", body.message[:80])
            intent = "qa"

    lenders: list[LenderResult] = []
    applied_filters: Optional[dict] = None
    unmatched_names: list[str] = []

    _lender_cache_key = None
    _cached_lender_payload = None

    if intent in ("filter", "qa", "compare", "lender_detail"):
        _lender_cache_params: dict = {"intent": intent}
        if intent == "filter":
            _lender_cache_params["filters"] = _normalize_filters(
                _merge_filters(body.last_filters, filters)
            )
        elif intent == "qa":
            _lender_cache_params["query"] = body.message.strip().lower()
        elif intent == "compare":
            _lender_cache_params["names"] = sorted(compare_names)
        elif intent == "lender_detail":
            _lender_cache_params["names"] = detail_names[:1]
        _lender_cache_key = make_key("chat:lenders", _lender_cache_params)
        _cached_lender_payload = await cache.get(_lender_cache_key)

    if _cached_lender_payload is not None:
        logger.debug("chat_stream: lender cache HIT")
        lenders = [LenderResult(**d) for d in _cached_lender_payload["lenders"]]
        applied_filters = _cached_lender_payload.get("applied_filters")
        unmatched_names = _cached_lender_payload.get("unmatched_names", [])
    elif intent not in ("greeting", "out_of_scope", "concept"):
        try:
            if intent == "filter":
                merged = _merge_filters(body.last_filters, filters)
                lenders, applied_filters, _, _ = await _search_with_broadening(db, merged)
                lenders = _dedup_lenders(lenders)

            elif intent == "qa":
                lenders = _dedup_lenders(
                    await _search_lenders_semantic(db, body.message, cfg.embedding_top_k)
                )

            elif intent == "compare" and compare_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, compare_names))
                unmatched_names = _compute_unmatched_names(compare_names, lenders)
                if len(lenders) == 1 and "another" in body.message.lower():
                    async with db.acquire() as conn:
                        alt = await conn.fetchrow(
                            """
                            SELECT id, company_name, company_type, rbi_category,
                                   aum_crores, aum_category, hq_state, hq_location,
                                   pan_india, primary_loan_segments, operating_states,
                                   website, quality_score, employee_count,
                                   established_year, is_listed, phone, email,
                                   operating_intensity, business_sector
                            FROM lenders
                            WHERE approval_status = 'approved'
                              AND company_type = $1 AND id != $2
                            ORDER BY quality_score DESC NULLS LAST, aum_crores DESC NULLS LAST
                            LIMIT 1
                            """,
                            lenders[0].company_type, lenders[0].id,
                        )
                    if alt:
                        lenders.append(_row_to_lender(alt))
                        unmatched_names = []

            elif intent == "lender_detail" and detail_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, detail_names[:1]))

        except Exception as exc:
            logger.error("chat_stream: DB query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Search temporarily unavailable")

        if _lender_cache_key and lenders:
            await cache.set(_lender_cache_key, {
                "lenders":         [l.model_dump() for l in lenders],
                "applied_filters": applied_filters,
                "unmatched_names": unmatched_names,
            }, ttl=CacheTTL.MATCH)

    lender_dicts      = [l.model_dump() for l in lenders]
    suggested_actions = _generate_suggestions(intent, lenders, applied_filters or {})

    _stream_note = ""
    if intent == "filter" and applied_filters:
        _s = applied_filters.get("state")
        _ss = applied_filters.get("states") or []
        _stream_total = total_count if "total_count" in dir() else len(lenders)
        if _s:
            _stream_note = (
                f"FILTER ACTIVE: state={_s!r} — ALL {_stream_total} matching lenders "
                f"OPERATE IN {_s} (most are headquartered elsewhere). "
                f"The HQ(headquartered) breakdown below is irrelevant to the count."
            )
        elif _ss:
            _states_str = " or ".join(_ss)
            _stream_note = (
                f"FILTER ACTIVE: states={_ss!r} — ALL {_stream_total} matching lenders "
                f"OPERATE IN {_states_str} (most are headquartered elsewhere). "
                f"The HQ(headquartered) breakdown below is irrelevant to the count."
            )

    async def event_gen():
        meta = {
            "type":              "meta",
            "intent":            intent,
            "lenders":           lender_dicts,
            "applied_filters":   applied_filters,
            "unmatched_names":   unmatched_names,
            "suggested_actions": suggested_actions,
            "session_id":        body.session_id,
        }
        yield f"data: {_json.dumps(meta)}\n\n"

        full_parts: list[str] = []
        had_error = False
        loop  = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _worker():
            try:
                for token in client.generate_grounded_answer_stream(
                    body.message, intent, lender_dicts, gemini_history,
                    note=_stream_note,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, ("t", token))
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, ("e", str(exc)))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("d", None))

        future = loop.run_in_executor(None, _worker)

        # Absolute deadline for the entire stream — no per-token reset
        deadline = time.monotonic() + cfg.chat_answer_timeout_secs
        _KEEPALIVE = 15.0  # send SSE comment every 15s to prevent browser disconnect

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                had_error = True
                logger.error("chat_stream: answer timed out after %ds", cfg.chat_answer_timeout_secs)
                break
            try:
                kind, val = await asyncio.wait_for(queue.get(), timeout=min(remaining, _KEEPALIVE))
            except asyncio.TimeoutError:
                if time.monotonic() >= deadline:
                    had_error = True
                    logger.error("chat_stream: answer timed out after %ds", cfg.chat_answer_timeout_secs)
                    break
                # Still within deadline — keepalive comment so browsers don't drop the connection
                yield ": keepalive\n\n"
                continue
            if kind == "t":
                full_parts.append(val)
                yield f"data: {_json.dumps({'type': 'token', 'text': val})}\n\n"
            elif kind == "e":
                had_error = True
                logger.error("chat_stream: answer error: %s", val)
                break
            else:
                break

        await future

        if had_error or not full_parts:
            yield f"data: {_json.dumps({'type': 'error', 'message': 'Answer generation failed'})}\n\n"

        message_id = None
        if session_ok and full_parts and not had_error:
            try:
                lender_names = [l["company_name"] for l in lender_dicts[:3]] if lender_dicts else None
                message_id = await _save_turn(
                    db, body.session_id, body.message,
                    "".join(full_parts), intent, applied_filters,
                    refusal=intent == "out_of_scope",
                    lender_names=lender_names,
                )
            except Exception as exc:
                logger.warning("chat_stream: save failed: %s", exc)

        yield f"data: {_json.dumps({'type': 'done', 'message_id': message_id})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Content-Encoding":  "identity",
        },
    )


# ---------------------------------------------------------------------------
# Feedback endpoint
# ---------------------------------------------------------------------------

@router.post("/feedback")
@limiter.limit("60/minute")
async def chat_feedback(
    request: Request,
    body: FeedbackRequest,
    db: asyncpg.Pool = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Record a thumbs-up/down rating for an assistant message."""
    user_id = user.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user token")
    if body.rating not in ("up", "down"):
        raise HTTPException(status_code=422, detail="rating must be 'up' or 'down'")
    try:
        uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="session_id must be a valid UUID")

    try:
        async with db.acquire() as conn:
            owner = await conn.fetchval(
                "SELECT user_id FROM chat_sessions WHERE id = $1::uuid",
                body.session_id,
            )
            if not owner or str(owner) != user_id:
                raise HTTPException(status_code=404, detail="Session not found")

            await conn.execute(
                """
                INSERT INTO chat_feedback (session_id, message_id, rating, user_id)
                VALUES ($1::uuid, $2, $3, $4::uuid)
                ON CONFLICT (user_id, message_id)
                DO UPDATE SET rating = EXCLUDED.rating, created_at = now()
                """,
                body.session_id, body.message_id, body.rating, user_id,
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("chat_feedback: DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Feedback service temporarily unavailable")

    return {"ok": True}
