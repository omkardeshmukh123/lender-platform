"""
GET /policies/filter — Filter loan policies by borrower eligibility criteria
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
from core.constants import VALID_LOAN_TYPES, VALID_EMPLOYMENT_TYPES
from models.policy import Policy, PolicyFilterResponse

router = APIRouter()
logger = logging.getLogger(__name__)

_VALID_LOAN_TYPES = VALID_LOAN_TYPES
_VALID_EMPLOYMENT = VALID_EMPLOYMENT_TYPES


def _row_to_policy(row) -> Policy:
    d = dict(row)

    def _list(v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        try:
            return json.loads(v)
        except Exception:
            return []

    return Policy(
        id=d["id"],
        lender_id=d["lender_id"],
        lender_name=d.get("company_name"),
        product_name=d.get("product_name"),
        loan_type=d.get("loan_type"),
        loan_amount_min=d.get("loan_amount_min"),
        loan_amount_max=d.get("loan_amount_max"),
        credit_score_min=d.get("credit_score_min"),
        credit_score_max=d.get("credit_score_max"),
        interest_rate_min=d.get("interest_rate_min"),
        interest_rate_max=d.get("interest_rate_max"),
        tenure_min=d.get("tenure_min"),
        tenure_max=d.get("tenure_max"),
        processing_fee=d.get("processing_fee"),
        employment_types=_list(d.get("employment_types")),
        collateral_required=bool(d.get("collateral_required", False)),
        collateral_types=_list(d.get("collateral_types")),
        eligible_states=_list(d.get("eligible_states")),
        min_age=d.get("min_age"),
        max_age=d.get("max_age"),
        min_monthly_income=d.get("min_monthly_income"),
        prepayment_allowed=bool(d.get("prepayment_allowed", True)),
        eligibility_notes=d.get("eligibility_notes"),
        completeness_score=d.get("completeness_score"),
        data_source=d.get("data_source"),
        interest_rate_source=d.get("interest_rate_source"),
        loan_amount_source=d.get("loan_amount_source"),
    )


@router.get(
    "/filter",
    response_model=PolicyFilterResponse,
    summary="Filter policies by borrower profile",
)
@limiter.limit("60/minute")
async def filter_policies(
    request: Request,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
    loan_type: Optional[str]   = Query(None),
    lender_id: Optional[int]   = Query(None, description="Filter by specific lender ID"),
    credit_score: Optional[int] = Query(None, ge=300, le=900),
    loan_amount: Optional[float] = Query(None, ge=0, description="Amount in Lakhs"),
    employment_type: Optional[str] = Query(None),
    state: Optional[str]       = Query(None, max_length=100),
    age: Optional[int]         = Query(None, ge=18, le=80),
    monthly_income: Optional[float] = Query(None, ge=0),
    collateral: Optional[bool] = Query(None),
    page: int                  = Query(1, ge=1, le=500),
    limit: int                 = Query(20, ge=1, le=100),
):
    if employment_type is not None:
        employment_type = employment_type.lower().strip()
        if employment_type not in _VALID_EMPLOYMENT:
            raise HTTPException(
                status_code=422,
                detail=f"employment_type must be one of: {sorted(_VALID_EMPLOYMENT)}",
            )

    if loan_type is not None and loan_type not in _VALID_LOAN_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"loan_type must be one of: {sorted(_VALID_LOAN_TYPES)}",
        )

    cache_key = make_key("policies_filter", {
        "lender_id": lender_id, "loan_type": loan_type,
        "credit_score": credit_score, "loan_amount": loan_amount,
        "employment_type": employment_type, "state": state,
        "age": age, "monthly_income": monthly_income,
        "collateral": collateral, "page": page, "limit": limit,
    })

    cached = await cache.get(cache_key)
    if cached is not None:
        metrics.inc("cache.hit", tags={"endpoint": "policies_filter"})
        return JSONResponse(cached, headers={"X-Cache": "HIT"})
    metrics.inc("cache.miss", tags={"endpoint": "policies_filter"})

    conditions = [
        "p.is_active = true",
        "p.approval_status = 'approved'",
        "l.approval_status = 'approved'",
    ]
    params: list = []
    idx = 1

    if lender_id is not None:
        conditions.append(f"p.lender_id = ${idx}")
        params.append(lender_id)
        idx += 1

    if loan_type:
        conditions.append(f"p.loan_type = ${idx}")
        params.append(loan_type)
        idx += 1

    if credit_score is not None:
        conditions.append(f"(p.credit_score_min IS NULL OR p.credit_score_min <= ${idx})")
        params.append(credit_score)
        idx += 1
        conditions.append(f"(p.credit_score_max IS NULL OR p.credit_score_max >= ${idx})")
        params.append(credit_score)
        idx += 1

    if loan_amount is not None:
        conditions.append(f"(p.loan_amount_min IS NULL OR p.loan_amount_min <= ${idx})")
        params.append(loan_amount)
        idx += 1
        conditions.append(f"(p.loan_amount_max IS NULL OR p.loan_amount_max >= ${idx})")
        params.append(loan_amount)
        idx += 1

    if employment_type:
        conditions.append(
            f"(p.employment_types IS NULL OR p.employment_types = '{{}}' "
            f"OR ${idx} = ANY(p.employment_types))"
        )
        params.append(employment_type)
        idx += 1

    if state:
        conditions.append(
            f"(p.eligible_states IS NULL OR p.eligible_states = '{{}}' "
            f"OR ${idx} = ANY(p.eligible_states) OR l.pan_india = true)"
        )
        params.append(state)
        idx += 1

    if age is not None:
        conditions.append(f"(p.min_age IS NULL OR p.min_age <= ${idx})")
        params.append(age)
        idx += 1
        conditions.append(f"(p.max_age IS NULL OR p.max_age >= ${idx})")
        params.append(age)
        idx += 1

    if monthly_income is not None:
        conditions.append(f"(p.min_monthly_income IS NULL OR p.min_monthly_income <= ${idx})")
        params.append(monthly_income)
        idx += 1

    if collateral is False:
        conditions.append("p.collateral_required = false")

    where  = " AND ".join(conditions)
    offset = (page - 1) * limit

    sql = f"""
        SELECT
            p.id, p.lender_id, p.product_name, p.loan_type,
            p.loan_amount_min, p.loan_amount_max,
            p.credit_score_min, p.credit_score_max,
            p.interest_rate_min, p.interest_rate_max,
            p.tenure_min, p.tenure_max,
            p.processing_fee, p.employment_types,
            p.collateral_required, p.collateral_types,
            p.eligible_states, p.min_age, p.max_age,
            p.min_monthly_income, p.prepayment_allowed,
            p.eligibility_notes, p.completeness_score,
            p.data_source, p.interest_rate_source, p.loan_amount_source,
            l.company_name
        FROM policies_enriched p
        JOIN lenders l ON l.id = p.lender_id
        WHERE {where}
        ORDER BY p.completeness_score DESC NULLS LAST, p.interest_rate_min ASC NULLS LAST
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    count_sql = f"SELECT COUNT(*) FROM policies_enriched p JOIN lenders l ON l.id = p.lender_id WHERE {where}"

    try:
        async with db.acquire() as conn:
            total = await conn.fetchval(count_sql, *params)
            rows  = await conn.fetch(sql, *params, limit, offset)
    except Exception as exc:
        logger.error("filter_policies DB error: %s", exc)
        metrics.inc("db.error_count")
        raise HTTPException(status_code=503, detail="Policy service temporarily unavailable")

    result = PolicyFilterResponse(total=total, page=page, limit=limit, results=[_row_to_policy(r) for r in rows])
    await cache.set(cache_key, result.model_dump(), ttl=CacheTTL.SEARCH)
    return result
