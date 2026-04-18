"""
GET /lenders/search  — Search and filter lenders
GET /lenders/{id}    — Single lender detail
"""

import json
import logging
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dependencies import get_db
from limiter import limiter
from core.cache import get_cache, make_key, CacheTTL
from core.metrics import metrics
from models.lender import LenderDetail, LenderSearchResponse, LenderSummary
from core.constants import VALID_LOAN_TYPES, VALID_COMPANY_TYPES, VALID_AUM_CATEGORIES

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_SORT_COLS = {
    "aum_crores", "established_year", "employee_count",
    "branch_count", "quality_score", "company_name",
}

_VALID_LOAN_TYPES    = VALID_LOAN_TYPES
_VALID_COMPANY_TYPES = VALID_COMPANY_TYPES
_VALID_AUM_CATEGORIES = VALID_AUM_CATEGORIES


def _parse_jsonb(val) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    try:
        return json.loads(val)
    except Exception:
        return []


def _row_to_summary(row) -> LenderSummary:
    d = dict(row)
    hq_location = d.get("hq_location") or ""
    hq_city = hq_location.split(",")[0].strip() or None
    return LenderSummary(
        id=d["id"],
        company_name=d["company_name"],
        company_type=d["company_type"],
        rbi_category=d.get("rbi_category"),
        aum_crores=d.get("aum_crores"),
        aum_category=d.get("aum_category"),
        hq_state=d.get("hq_state"),
        hq_location=hq_city,
        operating_intensity=d.get("operating_intensity"),
        business_sector=d.get("business_sector"),
        pan_india=bool(d.get("pan_india", False)),
        primary_loan_segments=_parse_jsonb(d.get("primary_loan_segments")),
        operating_states=_parse_jsonb(d.get("operating_states")),
        website=d.get("website"),
        quality_score=d.get("quality_score"),
        employee_count=d.get("employee_count"),
        established_year=d.get("established_year"),
        is_listed=bool(d.get("is_listed", False)),
        phone=d.get("phone"),
        email=d.get("email"),
    )


def _row_to_detail(row) -> LenderDetail:
    d = dict(row)
    hq_location = d.get("hq_location") or ""
    hq_city = hq_location.split(",")[0].strip() or None
    return LenderDetail(
        id=d["id"],
        company_name=d["company_name"],
        company_type=d["company_type"],
        rbi_category=d.get("rbi_category"),
        aum_crores=d.get("aum_crores"),
        aum_category=d.get("aum_category"),
        hq_location=hq_city,
        hq_state=d.get("hq_state"),
        operating_intensity=d.get("operating_intensity"),
        business_sector=d.get("business_sector"),
        pan_india=bool(d.get("pan_india", False)),
        primary_loan_segments=_parse_jsonb(d.get("primary_loan_segments")),
        operating_states=_parse_jsonb(d.get("operating_states")),
        website=d.get("website"),
        phone=d.get("phone"),
        email=d.get("email"),
        employee_count=d.get("employee_count"),
        branch_count=d.get("branch_count"),
        established_year=d.get("established_year"),
        is_listed=bool(d.get("is_listed", False)),
        stock_symbol=d.get("stock_symbol"),
        quality_score=d.get("quality_score"),
        last_scraped_at=d["last_scraped_at"].isoformat() if d.get("last_scraped_at") else None,
        data_source=d.get("data_source"),
        schema_version=d.get("schema_version"),
        cin=d.get("cin"),
        company_status=d.get("company_status"),
        authorized_capital_lakhs=d.get("authorized_capital_lakhs"),
        paid_up_capital_lakhs=d.get("paid_up_capital_lakhs"),
        mca21_status=d.get("mca21_status"),
    )


@router.get("/search", response_model=LenderSearchResponse, summary="Search lenders")
@limiter.limit("100/minute")
async def search_lenders(
    request: Request,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
    q: Optional[str] = Query(None, max_length=200),
    loan_type: Optional[List[str]] = Query(None),
    state: Optional[str] = Query(None, max_length=100),
    company_type: Optional[List[str]] = Query(None),
    aum_category: Optional[List[str]] = Query(None),
    aum_min: Optional[float] = Query(None, ge=0),
    aum_max: Optional[float] = Query(None, ge=0),
    established_year_min: Optional[int] = Query(None, ge=1900, le=2100),
    established_year_max: Optional[int] = Query(None, ge=1900, le=2100),
    pan_india: Optional[bool] = Query(None),
    is_listed: Optional[bool] = Query(None),
    operating_intensity: Optional[List[str]] = Query(None),
    business_sector: Optional[List[str]] = Query(None),
    sort_by: str = Query("aum_crores"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1, le=500),
    limit: int = Query(20, ge=1, le=100),
):
    if sort_by not in ALLOWED_SORT_COLS:
        sort_by = "aum_crores"

    if loan_type:
        invalid = [t for t in loan_type if t not in _VALID_LOAN_TYPES]
        if invalid:
            raise HTTPException(status_code=422, detail=f"Invalid loan_type: {invalid}")
        loan_type = loan_type[:18]

    if company_type:
        invalid = [t for t in company_type if t not in _VALID_COMPANY_TYPES]
        if invalid:
            raise HTTPException(status_code=422, detail=f"Invalid company_type: {invalid}")
        company_type = company_type[:len(_VALID_COMPANY_TYPES)]

    if aum_category:
        invalid = [t for t in aum_category if t not in _VALID_AUM_CATEGORIES]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid aum_category: {invalid}. Must be one of: {sorted(_VALID_AUM_CATEGORIES)}",
            )
        aum_category = aum_category[:len(_VALID_AUM_CATEGORIES)]

    # Cache key from all search params
    cache_params = {
        "q": q, "loan_type": sorted(loan_type or []),
        "state": state, "company_type": sorted(company_type or []),
        "aum_category": sorted(aum_category or []),
        "aum_min": aum_min, "aum_max": aum_max,
        "established_year_min": established_year_min,
        "established_year_max": established_year_max,
        "pan_india": pan_india, "is_listed": is_listed,
        "operating_intensity": sorted(operating_intensity or []),
        "business_sector": sorted(business_sector or []),
        "sort_by": sort_by, "sort_dir": sort_dir,
        "page": page, "limit": limit,
    }
    cache_key = make_key("lenders_search", cache_params)

    cached = await cache.get(cache_key)
    if cached is not None:
        metrics.inc("cache.hit", tags={"endpoint": "lenders_search"})
        return JSONResponse(cached, headers={"X-Cache": "HIT"})
    metrics.inc("cache.miss", tags={"endpoint": "lenders_search"})

    conditions = ["approval_status = 'approved'"]
    params: list = []
    idx = 1

    q_clean = q.strip() if q else None
    if q_clean:
        q_esc = q_clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(f"company_name ILIKE ${idx}")
        params.append(f"%{q_esc}%")
        idx += 1

    if company_type:
        conditions.append(f"company_type = ANY(${idx}::text[])")
        params.append(company_type)
        idx += 1

    if state:
        # operating_states is TEXT[] — use ANY(), not JSONB @> operator
        conditions.append(
            f"(pan_india = true OR ${idx} = ANY(operating_states))"
        )
        params.append(state)
        idx += 1

    if loan_type:
        # primary_loan_segments is TEXT[] — use ANY(), not JSONB @> operator
        lt_conds = []
        for lt in loan_type:
            lt_conds.append(f"${idx} = ANY(primary_loan_segments)")
            params.append(lt)
            idx += 1
        conditions.append(f"({' OR '.join(lt_conds)})")

    if aum_category:
        conditions.append(f"aum_category = ANY(${idx}::text[])")
        params.append(aum_category)
        idx += 1

    if aum_min is not None:
        conditions.append(f"aum_crores >= ${idx}")
        params.append(aum_min)
        idx += 1

    if aum_max is not None:
        conditions.append(f"aum_crores <= ${idx}")
        params.append(aum_max)
        idx += 1

    if established_year_min is not None:
        conditions.append(f"established_year >= ${idx}")
        params.append(established_year_min)
        idx += 1

    if established_year_max is not None:
        conditions.append(f"established_year <= ${idx}")
        params.append(established_year_max)
        idx += 1

    if pan_india is not None:
        conditions.append(f"pan_india = ${idx}")
        params.append(pan_india)
        idx += 1

    if is_listed is not None:
        conditions.append(f"is_listed = ${idx}")
        params.append(is_listed)
        idx += 1

    if operating_intensity:
        conditions.append(f"operating_intensity = ANY(${idx}::text[])")
        params.append(operating_intensity)
        idx += 1

    if business_sector:
        conditions.append(f"business_sector = ANY(${idx}::text[])")
        params.append(business_sector)
        idx += 1

    where    = " AND ".join(conditions)
    sort_sql = f"ORDER BY {sort_by} {sort_dir.upper()} NULLS LAST"
    offset   = (page - 1) * limit

    try:
        async with db.acquire() as conn:
            total = await conn.fetchval(f"SELECT COUNT(*) FROM lenders WHERE {where}", *params)
            rows  = await conn.fetch(
                f"""
                SELECT id, company_name, company_type, rbi_category,
                       aum_crores, aum_category, hq_state, hq_location,
                       operating_intensity, business_sector, pan_india, primary_loan_segments,
                       operating_states, website, quality_score,
                       employee_count, established_year, is_listed, phone, email
                FROM lenders
                WHERE {where}
                {sort_sql}
                LIMIT ${idx} OFFSET ${idx + 1}
                """,
                *params, limit, offset,
            )
    except Exception as exc:
        logger.error("search_lenders DB error: %s | request_id=%s",
                     exc, getattr(request.state, "request_id", ""))
        metrics.inc("db.error_count")
        raise HTTPException(status_code=503, detail="Search service temporarily unavailable")

    result = LenderSearchResponse(
        total=total,
        page=page,
        limit=limit,
        results=[_row_to_summary(r) for r in rows],
    )

    await cache.set(cache_key, result.model_dump(), ttl=CacheTTL.SEARCH)
    return result


@router.get("/stats", summary="Public platform stats (lender count, policies, states)")
@limiter.limit("60/minute")
async def get_public_stats(
    request: Request,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
):
    cache_key = make_key("lenders_stats", {})
    cached    = await cache.get(cache_key)
    if cached is not None:
        metrics.inc("cache.hit", tags={"endpoint": "lenders_stats"})
        return JSONResponse(cached, headers={"X-Cache": "HIT"})
    metrics.inc("cache.miss", tags={"endpoint": "lenders_stats"})

    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)                                                      AS total_lenders,
                    COUNT(*) FILTER (WHERE approval_status = 'approved')         AS approved_lenders,
                    COUNT(DISTINCT hq_state) FILTER (WHERE hq_state IS NOT NULL
                        AND approval_status = 'approved')                        AS states_count,
                    COUNT(DISTINCT company_type) FILTER (
                        WHERE approval_status = 'approved')                      AS company_types_count
                FROM lenders
                """
            )
            policy_row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS total_policies
                FROM policies
                WHERE is_active = true AND approval_status = 'approved'
                """
            )
    except Exception as exc:
        logger.error("get_public_stats DB error: %s", exc)
        metrics.inc("db.error_count")
        raise HTTPException(status_code=503, detail="Stats service temporarily unavailable")

    result = {
        "total_lenders":     int(row["approved_lenders"] or 0),
        "total_policies":    int(policy_row["total_policies"] or 0),
        "states_covered":    int(row["states_count"] or 0),
        "company_types":     int(row["company_types_count"] or 0),
    }
    await cache.set(cache_key, result, ttl=CacheTTL.STATS)
    return result


@router.get("/{lender_id}", response_model=LenderDetail, summary="Lender detail")
@limiter.limit("200/minute")
async def get_lender(
    request: Request,
    lender_id: int,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
):
    if lender_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid lender_id")

    cache_key = make_key("lender_detail", {"id": lender_id})
    cached    = await cache.get(cache_key)
    if cached is not None:
        metrics.inc("cache.hit", tags={"endpoint": "lender_detail"})
        return JSONResponse(cached, headers={"X-Cache": "HIT"})
    metrics.inc("cache.miss", tags={"endpoint": "lender_detail"})

    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, company_name, company_type, rbi_category,
                       aum_crores, aum_category, hq_state, hq_location,
                       operating_intensity, business_sector, pan_india, primary_loan_segments,
                       operating_states, website, quality_score,
                       employee_count, branch_count, established_year, is_listed,
                       stock_symbol, phone, email,
                       last_scraped_at, data_source, schema_version,
                       cin, company_status, authorized_capital_lakhs,
                       paid_up_capital_lakhs, mca21_status
                FROM lenders
                WHERE id = $1 AND approval_status = 'approved'
                """,
                lender_id,
            )
    except Exception as exc:
        logger.error("get_lender DB error: id=%d | %s", lender_id, exc)
        metrics.inc("db.error_count")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not row:
        raise HTTPException(status_code=404, detail="Lender not found")

    result = _row_to_detail(row)
    await cache.set(cache_key, result.model_dump(), ttl=CacheTTL.DETAIL)
    return result
