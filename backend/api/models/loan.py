from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from core.constants import VALID_LOAN_TYPES, VALID_EMPLOYMENT_TYPES

_VALID_LOAN_TYPES = VALID_LOAN_TYPES
_VALID_EMPLOYMENT = VALID_EMPLOYMENT_TYPES


class BorrowerProfile(BaseModel):
    loan_type: str = Field(..., description="e.g. MSME Loan, Personal Loan")
    loan_amount: float = Field(..., gt=0, le=50_000, description="Loan amount in Lakhs")
    credit_score: int = Field(..., ge=300, le=900)
    employment_type: str = Field(..., description="salaried/self-employed/business/agriculture or granular sub-types")
    state: str = Field(..., min_length=2, max_length=60)
    monthly_income: Optional[float] = Field(None, gt=0, description="Monthly income in ₹ thousands")
    existing_emi_monthly: Optional[float] = Field(
        None, ge=0, description="Sum of current EMI obligations in ₹ (home loan, car, CC, etc.)"
    )
    age: Optional[int] = Field(None, ge=18, le=80)
    business_vintage: Optional[int] = Field(None, ge=0, le=100, description="Years in business")
    tenure_months: Optional[int] = Field(None, ge=1, le=600, description="Requested tenure in months")

    @field_validator("loan_type")
    @classmethod
    def validate_loan_type(cls, v: str) -> str:
        if v not in _VALID_LOAN_TYPES:
            raise ValueError(
                f"Invalid loan_type '{v}'. Must be one of: {sorted(_VALID_LOAN_TYPES)}"
            )
        return v

    @field_validator("employment_type")
    @classmethod
    def validate_employment_type(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _VALID_EMPLOYMENT:
            raise ValueError(
                f"Invalid employment_type '{v}'. Must be one of: {sorted(_VALID_EMPLOYMENT)}"
            )
        return v

    @field_validator("state")
    @classmethod
    def normalize_state(cls, v: str) -> str:
        return v.strip().title()


class MatchedLoan(BaseModel):
    lender_id: int
    lender_name: str
    policy_id: int
    product_name: Optional[str] = None
    loan_type: str
    match_score: float = Field(..., ge=0, le=100, description="0-100 composite score")
    eligible: bool = True
    match_reasons: List[str] = Field(default_factory=list)
    disqualifiers: List[str] = Field(default_factory=list, description="Why borrower is not eligible")
    warnings: List[str] = Field(default_factory=list, description="Non-blocking notes for borrower")
    explanation: str = Field('', description="One-sentence decision explanation")
    foir_estimate: Optional[float] = Field(None, description="Borrower's repayment ratio (0–1)")
    interest_rate_min: Optional[float] = None
    interest_rate_max: Optional[float] = None
    loan_amount_min: Optional[float] = None
    loan_amount_max: Optional[float] = None
    processing_fee: Optional[float] = None
    tenure_min: Optional[int] = None
    tenure_max: Optional[int] = None
    credit_score_min: Optional[int] = None
    collateral_required: Optional[bool] = None
    eligibility_notes: Optional[str] = None
    website: Optional[str] = None
    # KYC requirements surfaced to borrower
    kyc_pan_required: Optional[bool] = None
    kyc_aadhaar_required: Optional[bool] = None
    kyc_gstin_required: Optional[bool] = None
    kyc_itr_years: Optional[int] = None
    # Data freshness — shown as disclaimer in UI
    rates_as_of: Optional[str] = Field(None, description="Date rates were last verified (ISO date)")
    data_source_confidence: Optional[float] = Field(None, ge=0, le=1, description="0–1 data quality score")


class MatchResponse(BaseModel):
    profile: BorrowerProfile
    total_matches: int
    eligible_count: int = 0
    results: List[MatchedLoan]


class LoanCompareItem(BaseModel):
    lender_id: int
    lender_name: str
    policy_id: int
    product_name: Optional[str] = None
    loan_type: Optional[str] = None
    interest_rate_min: Optional[float] = None
    interest_rate_max: Optional[float] = None
    loan_amount_min: Optional[float] = None
    loan_amount_max: Optional[float] = None
    tenure_min: Optional[int] = None
    tenure_max: Optional[int] = None
    processing_fee: Optional[float] = None
    credit_score_min: Optional[int] = None
    collateral_required: bool = False
    employment_types: List[str] = Field(default_factory=list)
    prepayment_allowed: bool = True
    eligibility_notes: Optional[str] = None


class CompareResponse(BaseModel):
    count: int
    results: List[LoanCompareItem]
