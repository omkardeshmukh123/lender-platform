"""
backend/api/routers/admin.py
==============================
Protected admin API endpoints.

All routes require: Authorization: Bearer <admin-jwt>
Admin JWT = Supabase JWT with app_metadata.role = "admin"

Endpoints:
  GET  /admin/pipeline              — all lenders + pipeline stage + policy counts
  GET  /admin/lenders               — lenders by approval_status (pending/needs_update/etc.)
  POST /admin/lenders/{id}/approve  — approve lender + all pending policies
  POST /admin/lenders/{id}/reject   — reject lender with reason
  POST /admin/lenders/{id}/flag     — flag for re-scrape (needs_update)
  GET  /admin/audit                 — approval action audit log
  GET  /admin/pipeline-runs         — pipeline metrics (cost, success rates)
  GET  /admin/stats                 — DB summary stats from mv_dashboard_stats

These routes bypass RLS by using the service-role connection pool,
but the admin check happens in Python before any DB call.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dependencies import get_db
from core.auth    import AdminUser
from core.cache   import get_cache, make_key

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Response models ────────────────────────────────────────────────────────────

class PipelineRow(BaseModel):
    id: int
    company_name: str
    company_type: str
    lender_status: str
    quality_score: Optional[float] = None
    data_source: Optional[str] = None
    last_scraped_at: Optional[str] = None
    next_scrape_at: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    admin_notes: Optional[str] = None
    total_policies: int = 0
    approved_policies: int = 0
    pending_policies: int = 0
    rejected_policies: int = 0
    avg_policy_completeness: Optional[float] = None


class PendingLenderRow(BaseModel):
    """Simplified lender row for the admin pending-review list."""
    id: int
    company_name: str
    company_type: str
    hq_state: Optional[str] = None
    website: Optional[str] = None
    quality_score: Optional[float] = None
    data_source: Optional[str] = None
    created_at: Optional[str] = None
    admin_notes: Optional[str] = None


class PendingLenderResponse(BaseModel):
    total: int
    page: int
    limit: int
    results: List[PendingLenderRow]


class PendingPolicy(BaseModel):
    id: int
    lender_id: int
    lender_name: Optional[str] = None
    product_name: Optional[str] = None
    loan_type: Optional[str] = None
    loan_amount_min: Optional[float] = None
    loan_amount_max: Optional[float] = None
    interest_rate_min: Optional[float] = None
    interest_rate_max: Optional[float] = None
    completeness_score: Optional[float] = None
    data_source: Optional[str] = None
    created_at: Optional[str] = None


class PendingPolicyResponse(BaseModel):
    total: int
    page: int
    limit: int
    results: List[PendingPolicy]


class ApproveRequest(BaseModel):
    notes: Optional[str] = Field(None, description="Optional notes for the approval")


class RejectRequest(BaseModel):
    reason: str = Field("", description="Rejection reason")


class AuditRow(BaseModel):
    id: int
    logged_at: str
    entity_type: str
    entity_id: int
    company_name: Optional[str] = None
    action: str
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    actor: Optional[str] = None
    notes: Optional[str] = None


class PipelineRunRow(BaseModel):
    id: int
    pipeline: str
    run_date: str
    started_at: str
    duration_seconds: Optional[float] = None
    total_processed: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    gemini_calls: int = 0
    estimated_cost_usd: Optional[float] = None


class LenderRequestRow(BaseModel):
    id: int
    company_name: str
    cin: Optional[str] = None
    requested_by: Optional[str] = None
    notes: Optional[str] = None
    created_at: str
    status: str


class LenderRequestsResponse(BaseModel):
    total: int
    page: int
    limit: int
    results: List[LenderRequestRow]


class UpdateRequestStatus(BaseModel):
    status: str = Field(..., pattern="^(pending|in_progress|done|rejected)$")


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get(
    "/lenders/pending",
    response_model=PendingLenderResponse,
    summary="List lenders pending admin review (paginated)",
)
async def get_pending_lenders(
    request: Request,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Frontend-facing alias: GET /admin/lenders/pending?page=N&limit=N."""
    offset = (page - 1) * limit
    try:
        async with db.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM lenders WHERE approval_status = 'pending'"
            )
            rows = await conn.fetch(
                """
                SELECT id, company_name, company_type, hq_state, website,
                       quality_score, data_source,
                       created_at, admin_notes
                FROM lenders
                WHERE approval_status = 'pending'
                ORDER BY quality_score DESC NULLS LAST, id DESC
                LIMIT $1 OFFSET $2
                """,
                limit, offset,
            )
    except Exception as exc:
        logger.error("get_pending_lenders DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    return PendingLenderResponse(
        total=total or 0,
        page=page,
        limit=limit,
        results=[
            PendingLenderRow(
                id=r["id"],
                company_name=r["company_name"],
                company_type=r["company_type"],
                hq_state=r["hq_state"],
                website=r["website"],
                quality_score=r["quality_score"],
                data_source=r["data_source"],
                created_at=r["created_at"].isoformat() if r["created_at"] else None,
                admin_notes=r["admin_notes"],
            )
            for r in rows
        ],
    )


@router.get(
    "/policies/pending",
    response_model=PendingPolicyResponse,
    summary="List policies pending admin approval (paginated)",
)
async def get_pending_policies(
    request: Request,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    lender_id: Optional[int] = Query(None, description="Filter by lender ID"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    offset = (page - 1) * limit
    conditions = ["p.approval_status = 'pending'"]
    params: list = []
    idx = 1

    if lender_id is not None:
        conditions.append(f"p.lender_id = ${idx}")
        params.append(lender_id)
        idx += 1

    where = " AND ".join(conditions)
    try:
        async with db.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM policies p WHERE {where}", *params
            )
            rows = await conn.fetch(
                f"""
                SELECT p.id, p.lender_id, l.company_name,
                       p.product_name, p.loan_type,
                       p.loan_amount_min, p.loan_amount_max,
                       p.interest_rate_min, p.interest_rate_max,
                       p.completeness_score, p.data_source,
                       p.created_at
                FROM policies p
                JOIN lenders l ON l.id = p.lender_id
                WHERE {where}
                ORDER BY p.completeness_score DESC NULLS LAST, p.id DESC
                LIMIT ${idx} OFFSET ${idx + 1}
                """,
                *params, limit, offset,
            )
    except Exception as exc:
        logger.error("get_pending_policies DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Policy service temporarily unavailable")

    return PendingPolicyResponse(
        total=total or 0,
        page=page,
        limit=limit,
        results=[
            PendingPolicy(
                id=r["id"],
                lender_id=r["lender_id"],
                lender_name=r["company_name"],
                product_name=r["product_name"],
                loan_type=r["loan_type"],
                loan_amount_min=r["loan_amount_min"],
                loan_amount_max=r["loan_amount_max"],
                interest_rate_min=r["interest_rate_min"],
                interest_rate_max=r["interest_rate_max"],
                completeness_score=r["completeness_score"],
                data_source=r["data_source"],
                created_at=r["created_at"].isoformat() if r["created_at"] else None,
            )
            for r in rows
        ],
    )


@router.post(
    "/policies/{policy_id}/approve",
    summary="Approve a single pending policy",
)
async def approve_policy(
    request: Request,
    policy_id: int,
    body: ApproveRequest,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
):
    actor_id, actor_email = _actor_info(admin)
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE policies
                SET approval_status = 'approved',
                    is_active = true,
                    approved_at = NOW(),
                    approved_by = $2,
                    admin_notes = COALESCE($3, admin_notes)
                WHERE id = $1
                RETURNING id, lender_id
                """,
                policy_id,
                actor_email,
                body.notes,
            )
    except Exception as exc:
        logger.error("approve_policy DB error: policy=%d | %s", policy_id, exc)
        raise HTTPException(status_code=503, detail="Policy approval service temporarily unavailable")

    if not row:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")

    lender_id = row["lender_id"]
    await cache.delete_pattern("lp:policies_filter:*")
    await cache.delete_pattern(f"lp:lender_detail:{lender_id}:*")
    await cache.delete_pattern("lp:loans_match:*")

    logger.info("ADMIN_APPROVE_POLICY policy=%d actor=%s", policy_id, actor_email)
    return {"policy_id": policy_id, "action": "approved"}


@router.post(
    "/policies/{policy_id}/reject",
    summary="Reject a single pending policy",
)
async def reject_policy(
    request: Request,
    policy_id: int,
    body: ApproveRequest,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
):
    actor_id, actor_email = _actor_info(admin)
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE policies
                SET approval_status = 'rejected',
                    is_active = false,
                    admin_notes = COALESCE($2, admin_notes)
                WHERE id = $1
                RETURNING id, lender_id
                """,
                policy_id,
                body.notes,
            )
    except Exception as exc:
        logger.error("reject_policy DB error: policy=%d | %s", policy_id, exc)
        raise HTTPException(status_code=503, detail="Policy rejection service temporarily unavailable")

    if not row:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")

    lender_id = row["lender_id"]
    await cache.delete_pattern("lp:policies_filter:*")
    await cache.delete_pattern(f"lp:lender_detail:{lender_id}:*")

    logger.info("ADMIN_REJECT_POLICY policy=%d actor=%s", policy_id, actor_email)
    return {"policy_id": policy_id, "action": "rejected"}

@router.get(
    "/pipeline",
    response_model=List[PipelineRow],
    summary="Admin pipeline view — all lenders with approval status + policy counts",
)
async def get_pipeline(
    request: Request,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    status: Optional[str] = Query(
        None,
        description="Filter by lender_status: pending, approved, rejected, needs_update",
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conditions = ["1=1"]
    params: list = []
    idx = 1

    if status:
        valid_statuses = {"pending", "approved", "rejected", "needs_update"}
        if status not in valid_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"status must be one of: {sorted(valid_statuses)}",
            )
        conditions.append(f"l.approval_status = ${idx}")
        params.append(status)
        idx += 1

    where = " AND ".join(conditions)
    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    l.id, l.company_name, l.company_type,
                    l.approval_status AS lender_status,
                    l.quality_score, l.data_source,
                    l.last_scraped_at, l.next_scrape_at,
                    l.approved_by, l.approved_at, l.admin_notes,
                    COUNT(p.id)                                        AS total_policies,
                    COUNT(p.id) FILTER (WHERE p.approval_status = 'approved')   AS approved_policies,
                    COUNT(p.id) FILTER (WHERE p.approval_status = 'pending')    AS pending_policies,
                    COUNT(p.id) FILTER (WHERE p.approval_status = 'rejected')   AS rejected_policies,
                    ROUND(AVG(p.completeness_score)::NUMERIC, 2)       AS avg_policy_completeness
                FROM lenders l
                LEFT JOIN policies p ON p.lender_id = l.id
                WHERE {where}
                GROUP BY l.id
                ORDER BY
                    CASE l.approval_status
                        WHEN 'pending' THEN 1 WHEN 'needs_update' THEN 2
                        WHEN 'approved' THEN 3 WHEN 'rejected' THEN 4 ELSE 5
                    END,
                    l.quality_score DESC NULLS LAST
                LIMIT ${idx} OFFSET ${idx+1}
                """,
                *params, limit, offset,
            )
    except Exception as exc:
        logger.error("get_pipeline DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Pipeline service temporarily unavailable")

    return [
        PipelineRow(
            id=r["id"], company_name=r["company_name"], company_type=r["company_type"],
            lender_status=r["lender_status"], quality_score=r["quality_score"],
            data_source=r["data_source"],
            last_scraped_at=r["last_scraped_at"].isoformat() if r["last_scraped_at"] else None,
            next_scrape_at=r["next_scrape_at"].isoformat() if r["next_scrape_at"] else None,
            approved_by=r["approved_by"], approved_at=r["approved_at"].isoformat() if r["approved_at"] else None,
            admin_notes=r["admin_notes"],
            total_policies=r["total_policies"] or 0,
            approved_policies=r["approved_policies"] or 0,
            pending_policies=r["pending_policies"] or 0,
            rejected_policies=r["rejected_policies"] or 0,
            avg_policy_completeness=r["avg_policy_completeness"],
        )
        for r in rows
    ]


def _actor_info(admin: dict) -> tuple[str, str]:
    """Extract (actor_id_uuid, actor_email) from decoded JWT payload."""
    actor_id    = admin.get("sub", "")        # UUID string
    actor_email = admin.get("email") or admin.get("sub", "admin")
    return actor_id, actor_email


@router.post(
    "/lenders/{lender_id}/approve",
    summary="Approve a lender and all its pending policies",
)
async def approve_lender(
    request: Request,
    lender_id: int,
    body: ApproveRequest,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
):
    actor_id, actor_email = _actor_info(admin)
    request_id = getattr(request.state, "request_id", None)
    client_ip  = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )

    try:
        async with db.acquire() as conn:
            # Uses approve_lender_audited() from migration 015 — writes audit log atomically
            row = await conn.fetchrow(
                """
                SELECT approve_lender_audited(
                    $1, 'approved'::TEXT, $2, $3::UUID, $4, $5, $6
                )
                """,
                lender_id,
                body.notes,
                actor_id or None,
                actor_email,
                request_id,
                client_ip,
            )
    except Exception as exc:
        logger.error("approve_lender DB error: lender=%d | %s", lender_id, exc)
        raise HTTPException(status_code=503, detail="Approval service temporarily unavailable")

    if not row:
        raise HTTPException(status_code=404, detail=f"Lender {lender_id} not found")

    # Invalidate all search/detail caches that might include this lender
    await cache.delete_pattern("lp:lenders_search:*")
    await cache.delete_pattern(f"lp:lender_detail:{lender_id}:*")
    await cache.delete_pattern("lp:loans_match:*")

    result = dict(row[0]) if row[0] else {}
    logger.info(
        "ADMIN_APPROVE lender=%d actor=%s policies_activated=%s request_id=%s",
        lender_id, actor_email, result.get("policies_activated"), request_id,
    )
    return {"lender_id": lender_id, "action": "approved", **result}


@router.post(
    "/lenders/{lender_id}/reject",
    summary="Reject a lender",
)
async def reject_lender(
    request: Request,
    lender_id: int,
    body: RejectRequest,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
):
    actor_id, actor_email = _actor_info(admin)
    request_id = getattr(request.state, "request_id", None)
    client_ip  = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )

    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT approve_lender_audited(
                    $1, 'rejected'::TEXT, $2, $3::UUID, $4, $5, $6
                )
                """,
                lender_id,
                body.reason,
                actor_id or None,
                actor_email,
                request_id,
                client_ip,
            )
    except Exception as exc:
        logger.error("reject_lender DB error: lender=%d | %s", lender_id, exc)
        raise HTTPException(status_code=503, detail="Rejection service temporarily unavailable")

    if not row:
        raise HTTPException(status_code=404, detail=f"Lender {lender_id} not found")

    await cache.delete_pattern("lp:lenders_search:*")
    await cache.delete_pattern(f"lp:lender_detail:{lender_id}:*")

    logger.info(
        "ADMIN_REJECT lender=%d actor=%s reason=%s request_id=%s",
        lender_id, actor_email, (body.reason or "")[:80], request_id,
    )
    return {"lender_id": lender_id, "action": "rejected"}


@router.post(
    "/lenders/{lender_id}/flag",
    summary="Flag a lender for re-scraping (needs_update)",
)
async def flag_lender(
    request: Request,
    lender_id: int,
    body: ApproveRequest,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    cache=Depends(get_cache),
):
    actor_id, actor_email = _actor_info(admin)
    request_id = getattr(request.state, "request_id", None)
    client_ip  = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )

    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT approve_lender_audited(
                    $1, 'needs_update'::TEXT, $2, $3::UUID, $4, $5, $6
                )
                """,
                lender_id,
                body.notes,
                actor_id or None,
                actor_email,
                request_id,
                client_ip,
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Lender {lender_id} not found")
            # Force re-scrape on next scheduler run — same connection, one round trip
            await conn.execute(
                "UPDATE lenders SET next_scrape_at = NOW() WHERE id = $1", lender_id
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("flag_lender DB error: lender=%d | %s", lender_id, exc)
        raise HTTPException(status_code=503, detail="Flag service temporarily unavailable")

    await cache.delete_pattern(f"lp:lender_detail:{lender_id}:*")
    logger.info(
        "ADMIN_FLAG lender=%d actor=%s request_id=%s",
        lender_id, actor_email, request_id,
    )
    return {"lender_id": lender_id, "action": "needs_update"}


@router.get(
    "/audit",
    response_model=List[AuditRow],
    summary="Approval audit log — who approved/rejected what and when",
)
async def get_audit_log(
    request: Request,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    entity_type: Optional[str] = Query(None, description="lender or policy"),
    action: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    conditions = ["1=1"]
    params: list = []
    idx = 1

    if entity_type:
        conditions.append(f"entity_type = ${idx}")
        params.append(entity_type)
        idx += 1
    if action:
        conditions.append(f"action = ${idx}")
        params.append(action)
        idx += 1

    where = " AND ".join(conditions)
    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, logged_at, entity_type, entity_id,
                       company_name, action, old_status, new_status, actor, notes
                FROM lender_audit_log
                WHERE {where}
                ORDER BY logged_at DESC
                LIMIT ${idx} OFFSET ${idx+1}
                """,
                *params, limit, offset,
            )
    except Exception as exc:
        logger.error("get_audit_log DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Audit log service temporarily unavailable")

    return [
        AuditRow(
            id=r["id"],
            logged_at=r["logged_at"].isoformat(),
            entity_type=r["entity_type"],
            entity_id=r["entity_id"],
            company_name=r["company_name"],
            action=r["action"],
            old_status=r["old_status"],
            new_status=r["new_status"],
            actor=r["actor"],
            notes=r["notes"],
        )
        for r in rows
    ]


@router.get(
    "/pipeline-runs",
    response_model=List[PipelineRunRow],
    summary="Pipeline execution history and Gemini cost tracking",
)
async def get_pipeline_runs(
    request: Request,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    pipeline: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=90, description="Lookback window in days"),
    limit: int = Query(50, ge=1, le=200),
):
    conditions = ["started_at > NOW() - ($1 || ' days')::INTERVAL"]
    params: list = [days]
    idx = 2

    if pipeline:
        conditions.append(f"pipeline = ${idx}")
        params.append(pipeline)
        idx += 1

    where = " AND ".join(conditions)
    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, pipeline, run_date::TEXT, started_at,
                       duration_seconds, total_processed,
                       success_count, failed_count, skipped_count,
                       gemini_calls, estimated_cost_usd
                FROM pipeline_runs
                WHERE {where}
                ORDER BY started_at DESC
                LIMIT ${idx}
                """,
                *params, limit,
            )
    except Exception as exc:
        logger.error("get_pipeline_runs DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Pipeline runs service temporarily unavailable")

    return [
        PipelineRunRow(
            id=r["id"], pipeline=r["pipeline"], run_date=r["run_date"],
            started_at=r["started_at"].isoformat(),
            duration_seconds=float(r["duration_seconds"]) if r["duration_seconds"] else None,
            total_processed=r["total_processed"] or 0,
            success_count=r["success_count"] or 0,
            failed_count=r["failed_count"] or 0,
            skipped_count=r["skipped_count"] or 0,
            gemini_calls=r["gemini_calls"] or 0,
            estimated_cost_usd=float(r["estimated_cost_usd"]) if r["estimated_cost_usd"] else None,
        )
        for r in rows
    ]


@router.get(
    "/stats",
    summary="Live dashboard stats from mv_dashboard_stats materialized view",
)
async def get_stats(
    request: Request,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
):
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM mv_dashboard_stats")
    except Exception as exc:
        logger.error("get_stats DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Stats service temporarily unavailable")
    if not row:
        raise HTTPException(status_code=503, detail="Stats view not available")

    d = dict(row)
    if d.get("computed_at"):
        d["computed_at"] = d["computed_at"].isoformat()
    return d


@router.get(
    "/lender-requests",
    response_model=LenderRequestsResponse,
    summary="List lender enrichment requests (paginated)",
)
async def get_lender_requests(
    request: Request,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    offset = (page - 1) * limit
    conditions: list = []
    params: list = []
    idx = 1

    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    try:
        async with db.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM lender_requests {where}", *params
            )
            rows = await conn.fetch(
                f"""
                SELECT id, company_name, cin, requested_by, notes, created_at, status
                FROM lender_requests
                {where}
                ORDER BY created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}
                """,
                *params, limit, offset,
            )
    except Exception as exc:
        logger.error("get_lender_requests DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    return LenderRequestsResponse(
        total=total or 0,
        page=page,
        limit=limit,
        results=[
            LenderRequestRow(
                id=r["id"],
                company_name=r["company_name"],
                cin=r["cin"],
                requested_by=r["requested_by"],
                notes=r["notes"],
                created_at=r["created_at"].isoformat(),
                status=r["status"],
            )
            for r in rows
        ],
    )


@router.post(
    "/lender-requests/{request_id}/status",
    summary="Update status of a lender enrichment request",
)
async def update_lender_request_status(
    request: Request,
    request_id: int,
    body: UpdateRequestStatus,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
):
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE lender_requests SET status = $1 WHERE id = $2 RETURNING id",
                body.status, request_id,
            )
    except Exception as exc:
        logger.error("update_lender_request_status DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not row:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")

    return {"id": request_id, "status": body.status}
