"""
backend/api/core/constants.py
==============================
Single source of truth for all domain constants shared across routers and models.
Import as: from core.constants import VALID_LOAN_TYPES, ...
"""

from __future__ import annotations

VALID_LOAN_TYPES: frozenset[str] = frozenset({
    "MSME Loan", "Personal Loan", "Home Loan", "Business Loan",
    "Vehicle Loan", "Gold Loan", "Education Loan", "Micro Loan",
    "Loan Against Property", "Working Capital", "Agriculture Loan",
    "EV Loan", "Two Wheeler Loan", "Rural Loan", "Microfinance",
    "Supply Chain Finance", "Consumer Durable Loan", "Credit Card",
})

VALID_COMPANY_TYPES: frozenset[str] = frozenset({
    "NBFC", "Private Bank", "PSU Bank", "Foreign Bank",
    "Cooperative Bank", "NBFC-MFI", "Small Finance Bank",
})

VALID_AUM_CATEGORIES: frozenset[str] = frozenset({"Micro", "Small", "Mid", "Large"})

VALID_EMPLOYMENT_TYPES: frozenset[str] = frozenset({
    # Granular
    "salaried_govt", "salaried_private", "salaried_psu",
    "self_employed_professional", "self_employed_non_professional",
    # Legacy broad (still accepted)
    "salaried", "self-employed", "business", "agriculture",
    # Other
    "student", "nri",
})
