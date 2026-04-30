from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class Policy(BaseModel):
    id: int
    lender_id: int
    lender_name: Optional[str] = None
    product_name: Optional[str] = None
    loan_type: Optional[str] = None
    loan_amount_min: Optional[float] = None
    loan_amount_max: Optional[float] = None
    credit_score_min: Optional[int] = None
    credit_score_max: Optional[int] = None
    interest_rate_min: Optional[float] = None
    interest_rate_max: Optional[float] = None
    tenure_min: Optional[int] = None
    tenure_max: Optional[int] = None
    processing_fee: Optional[float] = None
    employment_types: List[str] = Field(default_factory=list)
    collateral_required: bool = False
    collateral_types: List[str] = Field(default_factory=list)
    eligible_states: List[str] = Field(default_factory=list)
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    min_monthly_income: Optional[float] = None
    prepayment_allowed: bool = True
    eligibility_notes: Optional[str] = None
    completeness_score: Optional[float] = None
    data_source: Optional[str] = None
    interest_rate_source: Optional[str] = None
    loan_amount_source: Optional[str] = None


class PolicyFilterParams(BaseModel):
    loan_type: Optional[str] = None
    credit_score: Optional[int] = Field(None, ge=300, le=900)
    loan_amount: Optional[float] = Field(None, description="Loan amount in Lakhs")
    employment_type: Optional[str] = Field(None, description="salaried/self-employed/business/agriculture")
    state: Optional[str] = None
    age: Optional[int] = None
    monthly_income: Optional[float] = None
    collateral: Optional[bool] = None
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)


class PolicyFilterResponse(BaseModel):
    total: int
    page: int
    limit: int
    results: List[Policy]
