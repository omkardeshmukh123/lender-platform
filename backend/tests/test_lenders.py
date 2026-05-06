"""
Tests for lender-search constant validation.
Ensures VALID_LOAN_TYPES, VALID_COMPANY_TYPES, etc. never drift from the frontend.
Run these in CI — a failure means the frontend and backend are out of sync.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.core.constants import (
    VALID_AUM_CATEGORIES,
    VALID_COMPANY_TYPES,
    VALID_EMPLOYMENT_TYPES,
    VALID_LOAN_TYPES,
)

# ── Canonical values hardcoded in frontend/app/dashboard/page.tsx ─────────────
# Keep these in sync manually — the test will catch any divergence.

_FRONTEND_LOAN_TYPES = {
    "MSME Loan", "Personal Loan", "Home Loan", "Business Loan",
    "Vehicle Loan", "Gold Loan", "Education Loan", "Micro Loan",
    "Loan Against Property", "Working Capital", "Agriculture Loan",
    "EV Loan", "Two Wheeler Loan", "Rural Loan", "Microfinance",
    "Supply Chain Finance", "Consumer Durable Loan", "Credit Card",
}

_FRONTEND_COMPANY_TYPES = {
    "NBFC", "Private Bank", "PSU Bank", "Foreign Bank",
    "Cooperative Bank", "NBFC-MFI", "Small Finance Bank",
}

_FRONTEND_AUM_CATEGORIES = {"Micro", "Small", "Mid", "Large"}


# ── Parity tests ──────────────────────────────────────────────────────────────


def test_loan_types_match_frontend():
    missing_in_backend = _FRONTEND_LOAN_TYPES - VALID_LOAN_TYPES
    missing_in_frontend = VALID_LOAN_TYPES - _FRONTEND_LOAN_TYPES
    assert not missing_in_backend, f"Frontend has these but backend doesn't: {missing_in_backend}"
    assert not missing_in_frontend, f"Backend has these but frontend doesn't: {missing_in_frontend}"


def test_company_types_match_frontend():
    missing_in_backend = _FRONTEND_COMPANY_TYPES - VALID_COMPANY_TYPES
    missing_in_frontend = VALID_COMPANY_TYPES - _FRONTEND_COMPANY_TYPES
    assert not missing_in_backend, f"Frontend has these but backend doesn't: {missing_in_backend}"
    assert not missing_in_frontend, f"Backend has these but frontend doesn't: {missing_in_frontend}"


def test_aum_categories_match_frontend():
    missing_in_backend = _FRONTEND_AUM_CATEGORIES - VALID_AUM_CATEGORIES
    missing_in_frontend = VALID_AUM_CATEGORIES - _FRONTEND_AUM_CATEGORIES
    assert not missing_in_backend, f"Frontend has these but backend doesn't: {missing_in_backend}"
    assert not missing_in_frontend, f"Backend has these but frontend doesn't: {missing_in_frontend}"


# ── Sanity checks ─────────────────────────────────────────────────────────────


def test_employment_types_include_core_values():
    required = {"salaried", "self-employed", "business", "agriculture"}
    missing = required - VALID_EMPLOYMENT_TYPES
    assert not missing, f"Core employment types missing: {missing}"


def test_no_empty_strings_in_any_constant():
    all_vals = (
        VALID_LOAN_TYPES
        | VALID_COMPANY_TYPES
        | VALID_AUM_CATEGORIES
        | VALID_EMPLOYMENT_TYPES
    )
    for val in all_vals:
        assert val.strip(), f"Empty or whitespace-only constant: {val!r}"


def test_no_case_duplicates():
    for name, const_set in [
        ("VALID_LOAN_TYPES", VALID_LOAN_TYPES),
        ("VALID_COMPANY_TYPES", VALID_COMPANY_TYPES),
        ("VALID_AUM_CATEGORIES", VALID_AUM_CATEGORIES),
    ]:
        lowered = [v.lower() for v in const_set]
        dupes = [v for v in lowered if lowered.count(v) > 1]
        assert not dupes, f"{name} has case duplicates: {dupes}"


def test_loan_type_count_is_reasonable():
    assert 15 <= len(VALID_LOAN_TYPES) <= 30, \
        f"Unexpected loan type count: {len(VALID_LOAN_TYPES)}"
