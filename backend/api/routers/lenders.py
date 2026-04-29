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
from models.lender import LenderDetail, LenderSearchResponse, LenderSummary, RegistryStub
from pydantic import BaseModel, Field as PydanticField
from core.constants import VALID_LOAN_TYPES, VALID_COMPANY_TYPES, VALID_AUM_CATEGORIES

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_SORT_COLS = {
    "aum_crores", "established_year", "employee_count",
    "branch_count", "quality_score", "company_name", "last_year_revenue",
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
        policy_count=d.get("policy_count"),
        last_year_revenue=d.get("last_year_revenue"),
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
        last_year_revenue=d.get("last_year_revenue"),
        recent_funding=d.get("recent_funding"),
        recent_funding_amount=d.get("recent_funding_amount"),
        recent_funding_year=d.get("recent_funding_year"),
        financial_source=d.get("financial_source"),
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
    has_policies: Optional[bool] = Query(None, description="Filter to lenders with scraped policy data"),
    has_revenue: Optional[bool] = Query(None, description="Filter to lenders with revenue data"),
    revenue_min: Optional[float] = Query(None, ge=0, description="Min revenue in Crores"),
    revenue_max: Optional[float] = Query(None, ge=0, description="Max revenue in Crores"),
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
        "has_policies": has_policies, "has_revenue": has_revenue,
        "revenue_min": revenue_min, "revenue_max": revenue_max,
        "sort_by": sort_by, "sort_dir": sort_dir,
        "page": page, "limit": limit,
    }
    cache_key = make_key("lenders_search", cache_params)

    cached = await cache.get(cache_key)
    if cached is not None:
        metrics.inc("cache.hit", tags={"endpoint": "lenders_search"})
        return JSONResponse(cached, headers={"X-Cache": "HIT"})
    metrics.inc("cache.miss", tags={"endpoint": "lenders_search"})

    conditions = ["l.approval_status = 'approved'"]
    params: list = []
    idx = 1

    q_clean = q.strip() if q else None
    if q_clean:
        q_esc = q_clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(f"l.company_name ILIKE ${idx}")
        params.append(f"%{q_esc}%")
        idx += 1

    if company_type:
        conditions.append(f"l.company_type = ANY(${idx}::text[])")
        params.append(company_type)
        idx += 1

    if state:
        conditions.append(f"(l.pan_india = true OR ${idx} = ANY(l.operating_states))")
        params.append(state)
        idx += 1

    if loan_type:
        lt_conds = []
        for lt in loan_type:
            lt_conds.append(f"${idx} = ANY(l.primary_loan_segments)")
            params.append(lt)
            idx += 1
        conditions.append(f"({' OR '.join(lt_conds)})")

    if aum_category:
        conditions.append(f"l.aum_category = ANY(${idx}::text[])")
        params.append(aum_category)
        idx += 1

    if aum_min is not None:
        conditions.append(f"l.aum_crores >= ${idx}")
        params.append(aum_min)
        idx += 1

    if aum_max is not None:
        conditions.append(f"l.aum_crores <= ${idx}")
        params.append(aum_max)
        idx += 1

    if established_year_min is not None:
        conditions.append(f"l.established_year >= ${idx}")
        params.append(established_year_min)
        idx += 1

    if established_year_max is not None:
        conditions.append(f"l.established_year <= ${idx}")
        params.append(established_year_max)
        idx += 1

    if pan_india is not None:
        conditions.append(f"l.pan_india = ${idx}")
        params.append(pan_india)
        idx += 1

    if is_listed is not None:
        conditions.append(f"l.is_listed = ${idx}")
        params.append(is_listed)
        idx += 1

    if operating_intensity:
        conditions.append(f"l.operating_intensity = ANY(${idx}::text[])")
        params.append(operating_intensity)
        idx += 1

    if business_sector:
        conditions.append(f"l.business_sector = ANY(${idx}::text[])")
        params.append(business_sector)
        idx += 1

    if has_policies is True:
        conditions.append(
            "EXISTS (SELECT 1 FROM policies p WHERE p.lender_id = l.id "
            "AND p.is_active = true AND p.approval_status = 'approved')"
        )
    elif has_policies is False:
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM policies p WHERE p.lender_id = l.id "
            "AND p.is_active = true AND p.approval_status = 'approved')"
        )

    if has_revenue is True:
        conditions.append("l.last_year_revenue IS NOT NULL")
    elif has_revenue is False:
        conditions.append("l.last_year_revenue IS NULL")

    if revenue_min is not None:
        conditions.append(f"l.last_year_revenue >= ${idx}")
        params.append(revenue_min)
        idx += 1

    if revenue_max is not None:
        conditions.append(f"l.last_year_revenue <= ${idx}")
        params.append(revenue_max)
        idx += 1

    where    = " AND ".join(conditions)
    sort_sql = f"ORDER BY l.{sort_by} {sort_dir.upper()} NULLS LAST"
    offset   = (page - 1) * limit

    _SELECT_COLS = """
        l.id, l.company_name, l.company_type, l.rbi_category,
        l.aum_crores, l.aum_category, l.hq_state, l.hq_location,
        l.operating_intensity, l.business_sector, l.pan_india,
        l.primary_loan_segments, l.operating_states, l.website,
        l.quality_score, l.employee_count, l.established_year,
        l.is_listed, l.phone, l.email, l.last_year_revenue,
        (
            SELECT COUNT(*)::int FROM policies p
            WHERE p.lender_id = l.id
              AND p.is_active = true
              AND p.approval_status = 'approved'
        ) AS policy_count
    """

    stubs: List[RegistryStub] = []

    try:
        async with db.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM lenders l WHERE {where}", *params
            )
            rows = await conn.fetch(
                f"""
                SELECT {_SELECT_COLS}
                FROM lenders l
                WHERE {where}
                {sort_sql}
                LIMIT ${idx} OFFSET ${idx + 1}
                """,
                *params, limit, offset,
            )

            # Phase 2: trigram fallback when ILIKE returned < 3 results
            fuzzy_rows: list = []
            if q_clean and len(q_clean) >= 3 and (total or 0) < 3:
                existing_ids = [r["id"] for r in rows]
                fuzzy_rows = await conn.fetch(
                    f"""
                    SELECT {_SELECT_COLS}
                    FROM lenders l
                    WHERE l.approval_status = 'approved'
                      AND similarity(l.company_name, $1) > 0.25
                      AND l.id <> ALL($2::bigint[])
                    ORDER BY similarity(l.company_name, $1) DESC
                    LIMIT 10
                    """,
                    q_clean, existing_ids,
                )

            # Phase 3: registry stubs when still no results at all
            if q_clean and len(q_clean) >= 3 and (total or 0) == 0 and not fuzzy_rows:
                stub_rows = await conn.fetch(
                    """
                    SELECT id, company_name, cin, rbi_registration_number,
                           regulatory_tier, hq_state, established_year
                    FROM rbi_registry
                    WHERE similarity(company_name, $1) > 0.3
                    ORDER BY similarity(company_name, $1) DESC
                    LIMIT 5
                    """,
                    q_clean,
                )
                stubs = [
                    RegistryStub(
                        id=r["id"],
                        company_name=r["company_name"],
                        cin=r["cin"],
                        rbi_registration_number=r["rbi_registration_number"],
                        regulatory_tier=r["regulatory_tier"],
                        hq_state=r["hq_state"],
                        established_year=r["established_year"],
                    )
                    for r in stub_rows
                ]

    except Exception as exc:
        logger.error("search_lenders DB error: %s | request_id=%s",
                     exc, getattr(request.state, "request_id", ""))
        metrics.inc("db.error_count")
        raise HTTPException(status_code=503, detail="Search service temporarily unavailable")

    result = LenderSearchResponse(
        total=total,
        page=page,
        limit=limit,
        results=[_row_to_summary(r) for r in rows] + [_row_to_summary(r) for r in fuzzy_rows],
        stubs=stubs,
    )

    await cache.set(cache_key, result.model_dump(), ttl=CacheTTL.SEARCH)
    return result


# ── Lender request ──────────────────────────────────────────────────────────────

class LenderRequestBody(BaseModel):
    company_name: str = PydanticField(..., min_length=2, max_length=200)
    cin: Optional[str] = PydanticField(None, max_length=30)
    requested_by: Optional[str] = PydanticField(None, max_length=200)
    notes: Optional[str] = PydanticField(None, max_length=500)


@router.post("/request", summary="Submit a request to enrich a missing lender")
@limiter.limit("10/hour")
async def request_lender(
    request: Request,
    body: LenderRequestBody,
    db: asyncpg.Pool = Depends(get_db),
):
    try:
        async with db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO lender_requests (company_name, cin, requested_by, notes)
                VALUES ($1, $2, $3, $4)
                """,
                body.company_name,
                body.cin,
                body.requested_by or "anonymous",
                body.notes,
            )
    except Exception as exc:
        logger.error("request_lender DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    logger.info("LENDER_REQUEST company=%s by=%s", body.company_name, body.requested_by)
    return {"status": "submitted"}


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
                       paid_up_capital_lakhs, mca21_status,
                       last_year_revenue, recent_funding, recent_funding_amount,
                       recent_funding_year, financial_source
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
