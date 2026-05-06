"""
GET /v1/constants — Static domain constants for frontend consumption.
Prevents frontend/backend drift: frontend can fetch these instead of hardcoding.
Cache this response aggressively — values only change with code deploys.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.constants import (
    VALID_AUM_CATEGORIES,
    VALID_COMPANY_TYPES,
    VALID_EMPLOYMENT_TYPES,
    VALID_LOAN_TYPES,
)

router = APIRouter()

_PAYLOAD = {
    "loan_types": sorted(VALID_LOAN_TYPES),
    "company_types": sorted(VALID_COMPANY_TYPES),
    "aum_categories": ["Micro", "Small", "Mid", "Large"],  # ordered by size
    "employment_types": sorted(VALID_EMPLOYMENT_TYPES),
    "operating_intensities": ["Hyperlocal", "Local", "Regional", "National"],
    "business_sectors": sorted([
        "Agriculture", "MSME", "Consumer", "Real Estate", "Microfinance",
        "Vehicle", "Education", "Healthcare", "Infrastructure",
    ]),
    "sort_fields": ["aum_crores", "established_year", "employee_count", "quality_score", "company_name"],
}

# Pre-serialised — avoids re-serialising on every request
_RESPONSE = JSONResponse(
    content=_PAYLOAD,
    headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400"},
)


@router.get("", summary="Domain constants")
async def get_constants():
    """Returns valid enum values for all search and match filter fields.
    Cache aggressively — these only change on new deployments."""
    return _RESPONSE
