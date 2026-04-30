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
    business_sector: Optional[str] = None
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
    policy_count: Optional[int] = None
    last_year_revenue: Optional[float] = None
    min_interest_rate: Optional[float] = None


class LenderDetail(LenderSummary):
    branch_count: Optional[int] = None
    stock_symbol: Optional[str] = None
    last_scraped_at: Optional[str] = None
    data_source: Optional[str] = None
    schema_version: Optional[int] = None
    # MCA21 registry fields
    cin: Optional[str] = None
    company_status: Optional[str] = None
    authorized_capital_lakhs: Optional[float] = None
    paid_up_capital_lakhs: Optional[float] = None
    mca21_status: Optional[str] = None
    # Financial fields
    recent_funding: Optional[str] = None
    recent_funding_amount: Optional[float] = None
    recent_funding_year: Optional[int] = None
    financial_source: Optional[str] = None


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
    has_policies: Optional[bool] = Field(None, description="Only lenders with scraped policy data")
    has_revenue: Optional[bool] = Field(None, description="Only lenders with revenue data")
    revenue_min: Optional[float] = Field(None, description="Min last_year_revenue in Crores")
    revenue_max: Optional[float] = Field(None, description="Max last_year_revenue in Crores")
    sort_by: str = Field("aum_crores", description="Column to sort by")
    sort_dir: str = Field("desc", description="asc or desc")
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)


class RegistryStub(BaseModel):
    id: int
    company_name: str
    cin: Optional[str] = None
    rbi_registration_number: Optional[str] = None
    regulatory_tier: Optional[str] = None
    hq_state: Optional[str] = None
    established_year: Optional[int] = None


class LenderSearchResponse(BaseModel):
    total: int
    page: int
    limit: int
    results: List[LenderSummary]
    stubs: List[RegistryStub] = Field(default_factory=list)
