"""
POST /v1/leads — Capture borrower intent from LenderCard website-click modal.
"""
from __future__ import annotations

import logging
import re

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dependencies import get_db
from limiter import limiter

router = APIRouter()
logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"^\+?[\d\s\-]{7,15}$")


class LeadIn(BaseModel):
    lender_name: str
    loan_type:   str | None = None
    phone:       str | None = None

    @field_validator("lender_name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("lender_name must not be empty")
        return v[:300]

    @field_validator("phone")
    @classmethod
    def _phone_clean(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip()
        if not _PHONE_RE.match(v):
            return None  # silently drop malformed phones
        return v[:20]

    @field_validator("loan_type")
    @classmethod
    def _loan_type_clean(cls, v: str | None) -> str | None:
        if not v:
            return None
        return v.strip()[:100]


@router.post("", status_code=201)
@limiter.limit("30/minute")
async def capture_lead(
    request: Request,
    body:    LeadIn,
    db:      asyncpg.Pool = Depends(get_db),
):
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO leads (lender_name, loan_type, phone) VALUES ($1, $2, $3)",
            body.lender_name, body.loan_type, body.phone,
        )
    logger.info("lead captured lender=%r loan_type=%r", body.lender_name, body.loan_type)
    return JSONResponse(status_code=201, content={"status": "ok"})
