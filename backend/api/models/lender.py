from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class LenderSummary(BaseModel):
    id: int
    company_name: str
    company_type: str
    rbi_category: Optional[str] = None
    aum_crores: Optional[float] = None
    aum_category: Optional[str] = None
    hq_state: Optional[str] = None
    hq_location: Optional[str] = None
    operating_intensity: Optional[str] = None
    pan_india: bool = False
    primary_loan_segments: List[str] = Field(default_factory=list)
    operating_states: List[str] = Field(default_factory=list)
    website: Optional[str] = None
    quality_score: Optional[float] = None
    employee_count: Optional[int] = None
    established_year: Optional[int] = None
    is_listed: bool = False
    phone: Optional[str] = None
    email: Optional[str] = None


class LenderDetail(LenderSummary):
    branch_count: Optional[int] = None
    stock_symbol: Optional[str] = None
    last_scraped_at: Optional[str] = None
    data_source: Optional[str] = None
    schema_version: Optional[int] = None


class LenderSearchParams(BaseModel):
    """Query parameters for lender search (used for OpenAPI docs)."""
    q: Optional[str] = Field(None, description="Name search (partial match)")
    loan_type: Optional[List[str]] = Field(None, description="e.g. MSME Loan, Gold Loan")
    state: Optional[str] = Field(None, description="Operating state")
    company_type: Optional[List[str]] = Field(None, description="NBFC, PSU Bank, …")
    aum_min: Optional[float] = Field(None, description="Min AUM in Crores")
    aum_max: Optional[float] = Field(None, description="Max AUM in Crores")
    pan_india: Optional[bool] = None
    is_listed: Optional[bool] = None
    sort_by: str = Field("aum_crores", description="Column to sort by")
    sort_dir: str = Field("desc", description="asc or desc")
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)


class LenderSearchResponse(BaseModel):
    total: int
    page: int
    limit: int
    results: List[LenderSummary]
