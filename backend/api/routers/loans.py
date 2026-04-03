"""
POST /loans/match   — Ranked loan matches for a borrower profile
GET  /loans/compare — Side-by-side comparison of specific policies
"""

import json
import logging
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from dependencies import get_db
from limiter import limiter
from core.cache import get_cache, make_key, CacheTTL
from core.metrics import metrics
from models.loan import (
    BorrowerProfile, CompareResponse, LoanCompareItem,
    MatchResponse, MatchedLoan,
)
from pipeline.borrower_evaluator import rank_policies, EvaluationResult

router = APIRouter()
logger = logging.getLogger(__name__)


async def _log_match_request(
    db: asyncpg.Pool,
    profile: BorrowerProfile,
    policy_ids: List[int],
    match_count: int,
) -> None:
    try:
        async with db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO matching_requests (
                    loan_type, loan_amount, credit_score, employment_type,
                    monthly_income, age, state, business_vintage,
                    matched_policy_ids, match_count
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                profile.loan_type,
                profile.loan_amount,
                profile.credit_score,
                profile.employment_type,
                profile.monthly_income,
                profile.age,
                profile.state,
                profile.business_vintage,
                policy_ids,
                match_count,
            )
    except Exception as exc:
        logger.warning("Failed to log matching_request: %s", exc)


@router.post(
    "/match",
    response_model=MatchResponse,
    summary="Find best loan matches for a borrower profile",
)
@limiter.limit("30/minute")
async def match_loans(
    request: Request,
    profile: BorrowerProfile,
    background_tasks: BackgroundTasks,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
    limit: int = Query(20, ge=1, le=50),
):
    cache_key = make_key("loans_match", {**profile.model_dump(), "limit": limit})

    cached = await cache.get(cache_key)
    if cached is not None:
        metrics.inc("cache.hit", tags={"endpoint": "loans_match"})
        return JSONResponse(cached, headers={"X-Cache": "HIT"})
    metrics.inc("cache.miss", tags={"endpoint": "loans_match"})

    try:
        async with db.acquire() as conn:
            sql_rows = await conn.fetch(
                """
                SELECT * FROM match_lenders(
                    p_loan_type        => $1,
                    p_loan_amount      => $2,
                    p_credit_score     => $3,
                    p_employment_type  => $4,
                    p_state            => $5,
                    p_age              => $6,
                    p_monthly_income   => $7,
                    p_business_vintage => $8,
                    p_limit            => $9
                )
                """,
                profile.loan_type,
                profile.loan_amount,
                profile.credit_score,
                profile.employment_type,
                profile.state,
                profile.age,
                profile.monthly_income,
                profile.business_vintage,
                limit,
            )

            # Fetch extended policy fields not returned by match_lenders()
            # (one batched query — not per-row)
            policy_ids = [r["policy_id"] for r in sql_rows]
            extended: dict = {}
            if policy_ids:
                ext_rows = await conn.fetch(
                    """
                    SELECT id, min_monthly_income, min_age, max_age,
                           eligible_states, source_confidence,
                           kyc_pan_required, kyc_aadhaar_required,
                           kyc_gstin_required, kyc_itr_years,
                           anomaly_flags, completeness_score,
                           review_priority,
                           rates_as_of, data_source_confidence
                    FROM policies
                    WHERE id = ANY($1::bigint[])
                    """,
                    policy_ids,
                )
                extended = {r["id"]: dict(r) for r in ext_rows}

    except Exception as exc:
        logger.error(
            "match_lenders() DB error: %s | profile=%s | request_id=%s",
            exc, profile.loan_type, getattr(request.state, "request_id", ""),
        )
        metrics.inc("db.error_count")
        raise HTTPException(status_code=503, detail="Matching service temporarily unavailable")

    # Merge SQL results with extended policy data
    merged_policies = []
    for row in sql_rows:
        d = dict(row)
        ext = extended.get(d["policy_id"], {})
        merged_policies.append({**d, **ext})

    # Run borrower-side evaluation (FOIR, granular employment, scoring, explanation)
    profile_dict = {
        "loan_amount":          profile.loan_amount,
        "credit_score":         profile.credit_score,
        "employment_type":      profile.employment_type,
        "monthly_income":       profile.monthly_income,
        "existing_emi_monthly": profile.existing_emi_monthly,
        "age":                  profile.age,
        "state":                profile.state,
        "business_vintage":     profile.business_vintage,
        "loan_type":            profile.loan_type,
        "tenure_months":        profile.tenure_months,
    }
    evaluated: List[EvaluationResult] = rank_policies(profile_dict, merged_policies)

    def _to_matched_loan(ev: EvaluationResult, row: dict) -> MatchedLoan:
        return MatchedLoan(
            lender_id=ev.lender_id,
            lender_name=ev.lender_name,
            policy_id=ev.policy_id,
            product_name=ev.product_name,
            loan_type=row.get("loan_type") or profile.loan_type,
            match_score=ev.score,
            eligible=ev.eligible,
            match_reasons=ev.match_reasons,
            disqualifiers=ev.disqualifiers,
            warnings=ev.warnings,
            explanation=ev.explanation,
            foir_estimate=ev.foir_estimate,
            interest_rate_min=ev.interest_rate_min,
            interest_rate_max=ev.interest_rate_max,
            loan_amount_min=ev.loan_amount_min,
            loan_amount_max=ev.loan_amount_max,
            processing_fee=ev.processing_fee,
            tenure_min=ev.tenure_min,
            tenure_max=ev.tenure_max,
            credit_score_min=ev.credit_score_min,
            collateral_required=ev.collateral_required,
            eligibility_notes=ev.eligibility_notes,
            website=ev.website,
            kyc_pan_required=ev.kyc_pan_required,
            kyc_aadhaar_required=ev.kyc_aadhaar_required,
            kyc_gstin_required=ev.kyc_gstin_required,
            kyc_itr_years=ev.kyc_itr_years,
            rates_as_of=str(row.get("rates_as_of")) if row.get("rates_as_of") else None,
            data_source_confidence=row.get("data_source_confidence"),
        )

    # Build a quick lookup for loan_type from original SQL rows
    row_by_policy = {dict(r)["policy_id"]: dict(r) for r in sql_rows}

    results = [_to_matched_loan(ev, row_by_policy.get(ev.policy_id, {}))
               for ev in evaluated]

    eligible_count = sum(1 for r in results if r.eligible)

    response_data = MatchResponse(
        profile=profile,
        total_matches=len(results),
        eligible_count=eligible_count,
        results=results,
    )

    await cache.set(cache_key, response_data.model_dump(), ttl=CacheTTL.MATCH)

    if results:
        background_tasks.add_task(
            _log_match_request, db, profile,
            [r.policy_id for r in results], len(results),
        )

    metrics.inc("loans.match_served")
    return response_data


@router.get(
    "/compare",
    response_model=CompareResponse,
    summary="Side-by-side loan policy comparison",
)
@limiter.limit("30/minute")
async def compare_loans(
    request: Request,
    db: asyncpg.Pool = Depends(get_db),
    policy_ids: List[int] = Query(..., description="Policy IDs to compare (max 5)"),
):
    seen: set = set()
    unique_ids = [x for x in policy_ids if not (x in seen or seen.add(x))]
    if len(unique_ids) > 5:
        raise HTTPException(status_code=400, detail="Compare at most 5 policies at a time")
    if not unique_ids:
        raise HTTPException(status_code=400, detail="At least one policy ID is required")
    if any(i <= 0 for i in unique_ids):
        raise HTTPException(status_code=400, detail="Policy IDs must be positive integers")

    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    p.id, p.lender_id, p.product_name, p.loan_type,
                    p.loan_amount_min, p.loan_amount_max,
                    p.interest_rate_min, p.interest_rate_max,
                    p.tenure_min, p.tenure_max,
                    p.processing_fee, p.credit_score_min,
                    p.collateral_required, p.employment_types,
                    p.prepayment_allowed, p.eligibility_notes,
                    l.company_name, l.website
                FROM policies p
                JOIN lenders l ON l.id = p.lender_id
                WHERE p.id = ANY($1::int[])
                  AND p.is_active = true
                  AND p.approval_status = 'approved'
                  AND l.approval_status = 'approved'
                  AND l.onboarding_tier IN ('verified_lender', 'provisional_lender')
                ORDER BY p.interest_rate_min ASC NULLS LAST
                """,
                unique_ids,
            )
    except Exception as exc:
        logger.error("compare_loans DB error: %s", exc)
        metrics.inc("db.error_count")
        raise HTTPException(status_code=503, detail="Comparison service temporarily unavailable")

    if not rows:
        raise HTTPException(status_code=404, detail="No matching policies found")

    def _list(v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        try:
            return json.loads(v)
        except Exception:
            return []

    items = [
        LoanCompareItem(
            lender_id=dict(row)["lender_id"],
            lender_name=dict(row)["company_name"],
            policy_id=dict(row)["id"],
            product_name=dict(row).get("product_name"),
            loan_type=dict(row).get("loan_type"),
            interest_rate_min=dict(row).get("interest_rate_min"),
            interest_rate_max=dict(row).get("interest_rate_max"),
            loan_amount_min=dict(row).get("loan_amount_min"),
            loan_amount_max=dict(row).get("loan_amount_max"),
            tenure_min=dict(row).get("tenure_min"),
            tenure_max=dict(row).get("tenure_max"),
            processing_fee=dict(row).get("processing_fee"),
            credit_score_min=dict(row).get("credit_score_min"),
            collateral_required=bool(dict(row).get("collateral_required", False)),
            employment_types=_list(dict(row).get("employment_types")),
            prepayment_allowed=bool(dict(row).get("prepayment_allowed", True)),
            eligibility_notes=dict(row).get("eligibility_notes"),
        )
        for row in rows
    ]

    return CompareResponse(count=len(items), results=items)
