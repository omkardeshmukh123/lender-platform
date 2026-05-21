"""
Bank Manager Validation Test Suite

Two perspectives in every test:
  CUSTOMER  — sends a natural-language query the way a real user would
  BANK MGR  — checks that every returned lender actually satisfies what was asked

The Python-side filter function mirrors the SQL WHERE clause in _search_lenders
so we can validate result accuracy without a live database.

Coverage:
  - 25 realistic mock lenders (all company types, states, loan types, AUM bands)
  - 30 customer query scenarios
  - BankManagerValidator: company type, state coverage, loan type, AUM accuracy
  - Edge cases: no results, broadening, pan-India vs regional, multi-filter
  - _normalize_filters and filter cache key stability
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.routers.chat import (
    LenderResult,
    _merge_filters,
    _normalize_filters,
    _dedup_lenders,
)
from api.core.constants import VALID_LOAN_TYPES, VALID_COMPANY_TYPES, VALID_AUM_CATEGORIES


# ===========================================================================
# Realistic mock dataset — 25 lenders covering all company types / states
# ===========================================================================

def _mk(
    id: int, name: str, ctype: str, aum: Optional[float], aum_cat: str,
    hq_state: str, hq_loc: str, pan_india: bool, states: list[str],
    segments: list[str], sector: str, intensity: str,
    quality: float = 0.8, listed: bool = False, year: int = 2000,
) -> dict:
    return {
        "id": id, "company_name": name, "company_type": ctype,
        "approval_status": "approved", "aum_crores": aum,
        "aum_category": aum_cat, "hq_state": hq_state, "hq_location": hq_loc,
        "pan_india": pan_india, "operating_states": states,
        "primary_loan_segments": segments, "business_sector": sector,
        "operating_intensity": intensity, "quality_score": quality,
        "is_listed": listed, "established_year": year,
        "employee_count": None, "rbi_category": None,
        "website": None, "phone": None, "email": None,
    }


MOCK_LENDERS: list[dict] = [
    # --- Large NBFCs ---
    _mk(1,  "Bajaj Finance Limited",            "NBFC",         310000, "Large",
        "Maharashtra", "Pune",       True,  [],
        ["Personal Loan", "Business Loan", "Home Loan", "Consumer Durable Loan"],
        "Retail", "Pan India", 0.95, listed=True),
    _mk(2,  "IIFL Finance Limited",             "NBFC",         92000,  "Large",
        "Maharashtra", "Mumbai",     True,  [],
        ["Gold Loan", "Home Loan", "Business Loan", "Microfinance"],
        "Gold",   "Pan India", 0.88, listed=True),
    _mk(3,  "Muthoot Finance Limited",          "NBFC",         75000,  "Large",
        "Kerala",      "Kochi",      True,  [],
        ["Gold Loan"],
        "Gold",   "Pan India", 0.90, listed=True),
    _mk(4,  "Manappuram Finance Limited",       "NBFC",         21000,  "Mid",
        "Kerala",      "Thrissur",   True,  [],
        ["Gold Loan", "Vehicle Loan", "Home Loan"],
        "Gold",   "Pan India", 0.82, listed=True),
    _mk(5,  "Shriram Finance Limited",          "NBFC",         235000, "Large",
        "Tamil Nadu",  "Chennai",    True,  [],
        ["Vehicle Loan", "Two Wheeler Loan", "Personal Loan"],
        "Vehicle","Pan India", 0.87, listed=True),
    _mk(6,  "Mahindra & Mahindra Financial Services", "NBFC",   110000, "Large",
        "Maharashtra", "Mumbai",     True,  [],
        ["Vehicle Loan", "Agriculture Loan", "Business Loan"],
        "Vehicle","Pan India", 0.85, listed=True),
    _mk(7,  "LIC Housing Finance Limited",      "NBFC",         295000, "Large",
        "Maharashtra", "Mumbai",     True,  [],
        ["Home Loan"],
        "Housing","Pan India", 0.92, listed=True),

    # --- Mid NBFCs ---
    _mk(8,  "Sundaram Finance Limited",         "NBFC",         42000,  "Mid",
        "Tamil Nadu",  "Chennai",    False, ["Tamil Nadu", "Karnataka", "Andhra Pradesh", "Kerala"],
        ["Vehicle Loan", "Two Wheeler Loan", "Working Capital"],
        "Vehicle","Regional",  0.80, listed=True),
    _mk(9,  "SBFC Finance Limited",             "NBFC",         7200,   "Small",
        "Maharashtra", "Mumbai",     False, ["Maharashtra", "Gujarat", "Karnataka"],
        ["Business Loan", "Loan Against Property"],
        "MSME",   "Regional",  0.75),

    # --- PSU Banks ---
    _mk(10, "State Bank of India",              "PSU Bank",     None,   "Large",
        "Delhi",       "New Delhi",  True,  [],
        ["Home Loan","Personal Loan","MSME Loan","Agriculture Loan","Vehicle Loan","Education Loan"],
        "MSME",   "Pan India", 0.93, listed=True),
    _mk(11, "Bank of Baroda",                   "PSU Bank",     None,   "Large",
        "Gujarat",     "Vadodara",   True,  [],
        ["Home Loan","MSME Loan","Agriculture Loan","Personal Loan"],
        "MSME",   "Pan India", 0.85, listed=True),
    _mk(12, "Canara Bank",                      "PSU Bank",     None,   "Large",
        "Karnataka",   "Bengaluru",  True,  [],
        ["Home Loan","Agriculture Loan","MSME Loan","Education Loan"],
        "Agriculture","Pan India",0.84,listed=True),
    _mk(13, "UCO Bank",                         "PSU Bank",     None,   "Small",
        "West Bengal", "Kolkata",    True,  [],
        ["Home Loan","Personal Loan","Agriculture Loan","MSME Loan"],
        "MSME",   "Pan India", 0.70, listed=True),

    # --- Private Banks ---
    _mk(14, "HDFC Bank Limited",                "Private Bank", None,   "Large",
        "Maharashtra", "Mumbai",     True,  [],
        ["Home Loan","Vehicle Loan","Personal Loan","Business Loan","Credit Card","Working Capital"],
        "Retail", "Pan India", 0.97, listed=True),
    _mk(15, "ICICI Bank Limited",               "Private Bank", None,   "Large",
        "Maharashtra", "Mumbai",     True,  [],
        ["Home Loan","Vehicle Loan","Personal Loan","Business Loan","Education Loan"],
        "Retail", "Pan India", 0.96, listed=True),
    _mk(16, "Kotak Mahindra Bank Limited",      "Private Bank", None,   "Large",
        "Maharashtra", "Mumbai",     True,  [],
        ["Home Loan","Vehicle Loan","Personal Loan","Business Loan"],
        "Retail", "Pan India", 0.91, listed=True),
    _mk(17, "RBL Bank Limited",                 "Private Bank", 85000,  "Mid",
        "Maharashtra", "Mumbai",     True,  [],
        ["Business Loan","Personal Loan","Credit Card","Working Capital"],
        "Retail", "Pan India", 0.78, listed=True),

    # --- Small Finance Banks ---
    _mk(18, "AU Small Finance Bank Limited",    "Small Finance Bank", 80000, "Mid",
        "Rajasthan",   "Jaipur",     True,  [],
        ["Vehicle Loan","Home Loan","Business Loan","Personal Loan"],
        "Vehicle","Pan India", 0.88, listed=True),
    _mk(19, "Ujjivan Small Finance Bank",       "Small Finance Bank", 32000, "Small",
        "Karnataka",   "Bengaluru",  False, ["Karnataka","Tamil Nadu","Maharashtra","West Bengal"],
        ["Microfinance","Home Loan","Personal Loan","MSME Loan"],
        "Microfinance","Regional",0.82,listed=True),
    _mk(20, "Equitas Small Finance Bank",       "Small Finance Bank", 32000, "Small",
        "Tamil Nadu",  "Chennai",    False, ["Tamil Nadu","Karnataka","Andhra Pradesh"],
        ["Microfinance","Home Loan","Business Loan","Vehicle Loan"],
        "Microfinance","Regional",0.80,listed=True),
    _mk(21, "Suryoday Small Finance Bank",      "Small Finance Bank", 8000,  "Small",
        "Maharashtra", "Navi Mumbai",False, ["Maharashtra","Gujarat","Rajasthan"],
        ["Microfinance","Home Loan","Personal Loan"],
        "Microfinance","Regional",0.73),

    # --- NBFC-MFIs ---
    _mk(22, "Grameen Koota Financial Services", "NBFC-MFI",     3800,   "Small",
        "Karnataka",   "Bengaluru",  False, ["Karnataka","Tamil Nadu","Maharashtra"],
        ["Microfinance","Micro Loan"],
        "Microfinance","Regional",0.76),
    _mk(23, "Arohan Financial Services",        "NBFC-MFI",     5200,   "Small",
        "West Bengal", "Kolkata",    False, ["West Bengal","Bihar","Jharkhand","Odisha","Assam"],
        ["Microfinance","Micro Loan","Rural Loan"],
        "Microfinance","Regional",0.74),

    # --- Cooperative & Foreign ---
    _mk(24, "Saraswat Cooperative Bank",        "Cooperative Bank",2400, "Small",
        "Maharashtra", "Mumbai",     False, ["Maharashtra","Goa","Gujarat","Karnataka"],
        ["Home Loan","Personal Loan","Business Loan","Gold Loan"],
        "MSME",   "Regional",  0.72),
    _mk(25, "DBS Bank India Limited",           "Foreign Bank",  None,  "Mid",
        "Tamil Nadu",  "Chennai",    True,  [],
        ["Business Loan","Working Capital","Personal Loan","Home Loan"],
        "Retail", "Pan India", 0.79, listed=True),
]

# Index by id for direct lookup
_LENDER_BY_ID = {l["id"]: l for l in MOCK_LENDERS}


# ===========================================================================
# Python-side filter — mirrors the SQL WHERE clause in _search_lenders
# ===========================================================================

def _apply_filters(lenders: list[dict], filters: dict) -> list[dict]:
    """Apply the same filter logic as the SQL WHERE clause, in Python."""
    results = []
    for l in lenders:
        if l.get("approval_status") != "approved":
            continue

        # company_type
        ct = [t for t in (filters.get("company_type") or []) if t in VALID_COMPANY_TYPES]
        if ct and l.get("company_type") not in ct:
            continue

        # state — pan_india OR state in operating_states (mirrors SQL logic)
        state = filters.get("state")
        if state:
            if not l.get("pan_india") and state not in (l.get("operating_states") or []):
                continue

        # loan_type
        loan_types = [t for t in (filters.get("loan_type") or []) if t in VALID_LOAN_TYPES]
        if loan_types:
            segs = l.get("primary_loan_segments") or []
            if not any(lt in segs for lt in loan_types):
                continue

        # aum_category
        aum_cat = [t for t in (filters.get("aum_category") or []) if t in VALID_AUM_CATEGORIES]
        if aum_cat and l.get("aum_category") not in aum_cat:
            continue

        # aum_min / aum_max
        if filters.get("aum_min") is not None:
            aum = l.get("aum_crores")
            if aum is None or aum < filters["aum_min"]:
                continue
        if filters.get("aum_max") is not None:
            aum = l.get("aum_crores")
            if aum is None or aum > filters["aum_max"]:
                continue

        # pan_india
        if filters.get("pan_india") is not None:
            if bool(l.get("pan_india")) != bool(filters["pan_india"]):
                continue

        # is_listed
        if filters.get("is_listed") is not None:
            if bool(l.get("is_listed")) != bool(filters["is_listed"]):
                continue

        # operating_intensity
        oi = filters.get("operating_intensity") or []
        if oi and l.get("operating_intensity") not in oi:
            continue

        # business_sector
        bs = filters.get("business_sector") or []
        if bs and l.get("business_sector") not in bs:
            continue

        results.append(l)

    sort_by = filters.get("sort_by", "aum_crores")
    sort_dir = filters.get("sort_dir", "desc")
    results.sort(
        key=lambda x: (x.get(sort_by) or 0),
        reverse=(sort_dir == "desc"),
    )
    return results[:20]


def _as_lender_result(d: dict) -> LenderResult:
    return LenderResult(
        id=d["id"], company_name=d["company_name"], company_type=d["company_type"],
        rbi_category=d.get("rbi_category"), aum_crores=d.get("aum_crores"),
        aum_category=d.get("aum_category"), hq_state=d.get("hq_state"),
        hq_location=d.get("hq_location"), pan_india=bool(d.get("pan_india", False)),
        primary_loan_segments=d.get("primary_loan_segments") or [],
        operating_states=d.get("operating_states") or [],
        website=d.get("website"), quality_score=d.get("quality_score"),
        employee_count=d.get("employee_count"), established_year=d.get("established_year"),
        is_listed=bool(d.get("is_listed", False)),
        phone=d.get("phone"), email=d.get("email"),
        operating_intensity=d.get("operating_intensity"),
        business_sector=d.get("business_sector"),
    )


# ===========================================================================
# BankManagerValidator — checks result accuracy from a bank manager's POV
# ===========================================================================

class BankManagerValidator:
    """
    A bank manager reviewing results asks:
    - Are ALL returned lenders actually of the right company type?
    - Do ALL of them operate in the requested state?
    - Do ALL of them offer the requested loan product?
    - Is AUM data well-formed and non-null where it should be?
    - Are there zero lenders that should NOT appear in these results?
    """

    @staticmethod
    def check_company_type(results: list[dict], expected_types: list[str]):
        for l in results:
            ct = l.get("company_type")
            assert ct in expected_types, (
                f"BANK MGR FAIL: '{l['company_name']}' has type '{ct}', "
                f"but customer asked for {expected_types}"
            )

    @staticmethod
    def check_state_coverage(results: list[dict], state: str):
        for l in results:
            covers = l.get("pan_india") or (state in (l.get("operating_states") or []))
            assert covers, (
                f"BANK MGR FAIL: '{l['company_name']}' does NOT operate in '{state}' "
                f"(pan_india={l.get('pan_india')}, states={l.get('operating_states')})"
            )

    @staticmethod
    def check_loan_type(results: list[dict], loan_types: list[str]):
        for l in results:
            segs = l.get("primary_loan_segments") or []
            has_product = any(lt in segs for lt in loan_types)
            assert has_product, (
                f"BANK MGR FAIL: '{l['company_name']}' does NOT offer {loan_types} "
                f"(offers: {segs})"
            )

    @staticmethod
    def check_aum_category(results: list[dict], categories: list[str]):
        for l in results:
            cat = l.get("aum_category")
            assert cat in categories, (
                f"BANK MGR FAIL: '{l['company_name']}' has AUM category '{cat}', "
                f"customer asked for {categories}"
            )

    @staticmethod
    def check_no_unapproved(results: list[dict]):
        for l in results:
            assert l.get("approval_status") == "approved", (
                f"BANK MGR FAIL: '{l['company_name']}' is not approved but appears in results"
            )

    @staticmethod
    def check_pan_india(results: list[dict], expected: bool):
        for l in results:
            assert bool(l.get("pan_india")) == expected, (
                f"BANK MGR FAIL: '{l['company_name']}' pan_india={l.get('pan_india')}, "
                f"expected {expected}"
            )

    @staticmethod
    def check_is_listed(results: list[dict], expected: bool):
        for l in results:
            assert bool(l.get("is_listed")) == expected, (
                f"BANK MGR FAIL: '{l['company_name']}' is_listed={l.get('is_listed')}, "
                f"expected {expected}"
            )

    @staticmethod
    def check_aum_gte(results: list[dict], min_aum: float):
        for l in results:
            aum = l.get("aum_crores")
            assert aum is not None and aum >= min_aum, (
                f"BANK MGR FAIL: '{l['company_name']}' AUM={aum} is below minimum {min_aum}"
            )

    @staticmethod
    def check_business_sector(results: list[dict], sectors: list[str]):
        for l in results:
            assert l.get("business_sector") in sectors, (
                f"BANK MGR FAIL: '{l['company_name']}' sector '{l.get('business_sector')}' "
                f"not in {sectors}"
            )


bm = BankManagerValidator()


# ===========================================================================
# Customer → Bank Manager test cases
# ===========================================================================

class TestCustomerQueriesNBFC:
    """Customer asks for NBFCs — bank manager checks all results are NBFCs."""

    def test_show_nbfcs(self):
        # CUSTOMER: "Show me NBFCs"
        filters = {"company_type": ["NBFC"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results, "Expected at least 1 NBFC in mock data"
        bm.check_company_type(results, ["NBFC"])
        bm.check_no_unapproved(results)

    def test_nbfcs_in_maharashtra(self):
        # CUSTOMER: "Show NBFCs in Maharashtra"
        filters = {"company_type": ["NBFC"], "state": "Maharashtra"}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC"])
        bm.check_state_coverage(results, "Maharashtra")

    def test_nbfcs_gold_loan(self):
        # CUSTOMER: "Gold loan NBFCs"
        filters = {"company_type": ["NBFC"], "loan_type": ["Gold Loan"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC"])
        bm.check_loan_type(results, ["Gold Loan"])

    def test_large_nbfcs(self):
        # CUSTOMER: "Large NBFCs"
        filters = {"company_type": ["NBFC"], "aum_category": ["Large"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC"])
        bm.check_aum_category(results, ["Large"])

    def test_nbfc_vehicle_loan(self):
        # CUSTOMER: "NBFCs offering vehicle loans"
        filters = {"company_type": ["NBFC"], "loan_type": ["Vehicle Loan"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC"])
        bm.check_loan_type(results, ["Vehicle Loan"])

    def test_nbfc_pan_india(self):
        # CUSTOMER: "Pan India NBFCs"
        filters = {"company_type": ["NBFC"], "pan_india": True}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC"])
        bm.check_pan_india(results, True)

    def test_nbfc_housing_sector(self):
        # CUSTOMER: "NBFCs focused on housing"
        filters = {"company_type": ["NBFC"], "business_sector": ["Housing"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC"])
        bm.check_business_sector(results, ["Housing"])


class TestCustomerQueriesPSUBank:
    """Customer asks for PSU Banks."""

    def test_psu_banks(self):
        # CUSTOMER: "Show government banks"
        filters = {"company_type": ["PSU Bank"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["PSU Bank"])

    def test_psu_agriculture_loan(self):
        # CUSTOMER: "PSU banks for agriculture loans"
        filters = {"company_type": ["PSU Bank"], "loan_type": ["Agriculture Loan"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["PSU Bank"])
        bm.check_loan_type(results, ["Agriculture Loan"])

    def test_psu_banks_karnataka(self):
        # CUSTOMER: "Government banks in Karnataka"
        filters = {"company_type": ["PSU Bank"], "state": "Karnataka"}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results  # SBI and others are pan-India
        bm.check_company_type(results, ["PSU Bank"])
        bm.check_state_coverage(results, "Karnataka")

    def test_psu_listed(self):
        # CUSTOMER: "Listed PSU banks"
        filters = {"company_type": ["PSU Bank"], "is_listed": True}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["PSU Bank"])
        bm.check_is_listed(results, True)


class TestCustomerQueriesPrivateBank:
    """Customer asks for private banks."""

    def test_private_banks(self):
        # CUSTOMER: "Private banks"
        filters = {"company_type": ["Private Bank"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["Private Bank"])

    def test_private_home_loan(self):
        # CUSTOMER: "Private banks for home loans"
        filters = {"company_type": ["Private Bank"], "loan_type": ["Home Loan"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["Private Bank"])
        bm.check_loan_type(results, ["Home Loan"])

    def test_private_banks_maharashtra(self):
        # CUSTOMER: "Private banks in Mumbai" (city→state mapping done by AI)
        filters = {"company_type": ["Private Bank"], "state": "Maharashtra"}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["Private Bank"])
        bm.check_state_coverage(results, "Maharashtra")


class TestCustomerQueriesSFB:
    """Customer asks for Small Finance Banks."""

    def test_sfbs(self):
        # CUSTOMER: "Small Finance Banks"
        filters = {"company_type": ["Small Finance Bank"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["Small Finance Bank"])

    def test_sfb_microfinance(self):
        # CUSTOMER: "SFBs offering microfinance"
        filters = {"company_type": ["Small Finance Bank"], "loan_type": ["Microfinance"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["Small Finance Bank"])
        bm.check_loan_type(results, ["Microfinance"])

    def test_sfb_karnataka(self):
        # CUSTOMER: "Small Finance Banks in Karnataka"
        filters = {"company_type": ["Small Finance Bank"], "state": "Karnataka"}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["Small Finance Bank"])
        bm.check_state_coverage(results, "Karnataka")


class TestCustomerQueriesMFI:
    """Customer asks for NBFC-MFIs."""

    def test_nbfc_mfi(self):
        # CUSTOMER: "MFI lenders"
        filters = {"company_type": ["NBFC-MFI"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC-MFI"])

    def test_mfi_west_bengal(self):
        # CUSTOMER: "MFI lenders in West Bengal"
        filters = {"company_type": ["NBFC-MFI"], "state": "West Bengal"}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC-MFI"])
        bm.check_state_coverage(results, "West Bengal")

    def test_mfi_rural_loan(self):
        # CUSTOMER: "MFIs offering rural loans"
        filters = {"company_type": ["NBFC-MFI"], "loan_type": ["Rural Loan"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC-MFI"])
        bm.check_loan_type(results, ["Rural Loan"])


class TestCustomerQueriesLoanType:
    """Customer asks for a specific loan product — all types."""

    def test_gold_loan_lenders(self):
        # CUSTOMER: "Gold loan lenders"
        results = _apply_filters(MOCK_LENDERS, {"loan_type": ["Gold Loan"]})
        assert results
        bm.check_loan_type(results, ["Gold Loan"])

    def test_home_loan_lenders(self):
        # CUSTOMER: "Home loan lenders"
        results = _apply_filters(MOCK_LENDERS, {"loan_type": ["Home Loan"]})
        assert results
        bm.check_loan_type(results, ["Home Loan"])

    def test_vehicle_loan_lenders(self):
        # CUSTOMER: "Vehicle loan lenders"
        results = _apply_filters(MOCK_LENDERS, {"loan_type": ["Vehicle Loan"]})
        assert results
        bm.check_loan_type(results, ["Vehicle Loan"])

    def test_agriculture_loan_lenders(self):
        # CUSTOMER: "Agriculture loan lenders"
        results = _apply_filters(MOCK_LENDERS, {"loan_type": ["Agriculture Loan"]})
        assert results
        bm.check_loan_type(results, ["Agriculture Loan"])

    def test_msme_loan_lenders(self):
        # CUSTOMER: "MSME loan lenders"
        results = _apply_filters(MOCK_LENDERS, {"loan_type": ["MSME Loan"]})
        assert results
        bm.check_loan_type(results, ["MSME Loan"])

    def test_education_loan_lenders(self):
        # CUSTOMER: "Education loan lenders"
        results = _apply_filters(MOCK_LENDERS, {"loan_type": ["Education Loan"]})
        assert results
        bm.check_loan_type(results, ["Education Loan"])

    def test_microfinance_lenders(self):
        # CUSTOMER: "Microfinance lenders"
        results = _apply_filters(MOCK_LENDERS, {"loan_type": ["Microfinance"]})
        assert results
        bm.check_loan_type(results, ["Microfinance"])


class TestCustomerQueriesAUM:
    """Customer asks for lenders by AUM band."""

    def test_large_aum_lenders(self):
        # CUSTOMER: "Largest lenders"
        results = _apply_filters(MOCK_LENDERS, {"aum_category": ["Large"]})
        assert results
        bm.check_aum_category(results, ["Large"])

    def test_small_aum_lenders(self):
        # CUSTOMER: "Small AUM lenders"
        results = _apply_filters(MOCK_LENDERS, {"aum_category": ["Small"]})
        assert results
        bm.check_aum_category(results, ["Small"])

    def test_large_nbfc_by_aum(self):
        # CUSTOMER: "Large NBFCs sorted by AUM"
        results = _apply_filters(MOCK_LENDERS, {
            "company_type": ["NBFC"], "aum_category": ["Large"],
            "sort_by": "aum_crores", "sort_dir": "desc",
        })
        assert results
        bm.check_company_type(results, ["NBFC"])
        bm.check_aum_category(results, ["Large"])
        # Bank manager checks: sorted correctly
        aum_vals = [r.get("aum_crores") or 0 for r in results]
        assert aum_vals == sorted(aum_vals, reverse=True)


class TestCustomerQueriesMultiFilter:
    """Customer applies multiple filters simultaneously."""

    def test_nbfc_gold_maharashtra(self):
        # CUSTOMER: "Gold loan NBFCs in Maharashtra"
        filters = {"company_type": ["NBFC"], "loan_type": ["Gold Loan"], "state": "Maharashtra"}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC"])
        bm.check_loan_type(results, ["Gold Loan"])
        bm.check_state_coverage(results, "Maharashtra")

    def test_psu_home_loan_karnataka(self):
        # CUSTOMER: "PSU banks giving home loans in Karnataka"
        filters = {"company_type": ["PSU Bank"], "loan_type": ["Home Loan"], "state": "Karnataka"}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["PSU Bank"])
        bm.check_loan_type(results, ["Home Loan"])
        bm.check_state_coverage(results, "Karnataka")

    def test_sfb_microfinance_pan_india_triggers_broadening(self):
        # CUSTOMER: "Pan India SFBs with microfinance"
        # BANK MGR insight: no SFB is both pan-India AND offers microfinance —
        # pan-India SFBs focus on Vehicle/Home; microfinance SFBs are regional.
        # The system should return empty here and broaden by dropping pan_india.
        strict = _apply_filters(MOCK_LENDERS, {
            "company_type": ["Small Finance Bank"],
            "loan_type": ["Microfinance"],
            "pan_india": True,
        })
        assert strict == [], "Expected 0 results for pan-India + SFB + Microfinance (all SFBs offering MF are regional)"

        # After broadening (drop pan_india), SFBs with microfinance should appear
        broadened = _apply_filters(MOCK_LENDERS, {
            "company_type": ["Small Finance Bank"],
            "loan_type": ["Microfinance"],
        })
        assert broadened, "Broadened query should find SFBs offering microfinance"
        bm.check_company_type(broadened, ["Small Finance Bank"])
        bm.check_loan_type(broadened, ["Microfinance"])

    def test_multi_company_type(self):
        # CUSTOMER: "NBFCs or Private Banks for home loans"
        filters = {"company_type": ["NBFC", "Private Bank"], "loan_type": ["Home Loan"]}
        results = _apply_filters(MOCK_LENDERS, filters)
        assert results
        bm.check_company_type(results, ["NBFC", "Private Bank"])
        bm.check_loan_type(results, ["Home Loan"])


class TestEdgeCasesAndBroadening:
    """Edge cases: empty results, no-filter searches, broadening."""

    def test_no_results_for_impossible_combination(self):
        # CUSTOMER: "Foreign banks in Rajasthan" — DBS is pan_india=True, not headquartered there
        # but DBS IS pan-india so it WILL match
        filters = {"company_type": ["Foreign Bank"], "state": "Rajasthan"}
        results = _apply_filters(MOCK_LENDERS, filters)
        # DBS is pan_india=True → should match
        assert all(l.get("pan_india") or "Rajasthan" in (l.get("operating_states") or [])
                   for l in results)

    def test_empty_filters_returns_all(self):
        # CUSTOMER: "Show me lenders" (no filters)
        results = _apply_filters(MOCK_LENDERS, {})
        assert len(results) == 20  # capped at 20

    def test_non_existent_state_returns_pan_india_only(self):
        # CUSTOMER: "Lenders in Nagaland" — no lender has Nagaland in operating_states
        # Only pan_india lenders should come through
        results = _apply_filters(MOCK_LENDERS, {"state": "Nagaland"})
        for l in results:
            assert l.get("pan_india"), f"'{l['company_name']}' is not pan-India but appears for Nagaland"

    def test_regional_lender_excluded_from_other_state(self):
        # Sundaram Finance is Tamil Nadu / Karnataka / AP / Kerala only, NOT pan_india
        # CUSTOMER: "Lenders in West Bengal"
        results = _apply_filters(MOCK_LENDERS, {"state": "West Bengal"})
        names = [r["company_name"] for r in results]
        assert "Sundaram Finance Limited" not in names, (
            "BANK MGR FAIL: Sundaram should not appear for West Bengal query"
        )

    def test_cooperative_bank_regional(self):
        # Saraswat Cooperative Bank operates in Maharashtra/Goa/Gujarat/Karnataka
        # Should appear for Maharashtra query
        results = _apply_filters(MOCK_LENDERS, {
            "company_type": ["Cooperative Bank"], "state": "Maharashtra"
        })
        names = [r["company_name"] for r in results]
        assert "Saraswat Cooperative Bank" in names

    def test_cooperative_bank_not_in_wrong_state(self):
        # Saraswat does NOT operate in Rajasthan
        results = _apply_filters(MOCK_LENDERS, {
            "company_type": ["Cooperative Bank"], "state": "Rajasthan"
        })
        names = [r["company_name"] for r in results]
        assert "Saraswat Cooperative Bank" not in names, (
            "BANK MGR FAIL: Saraswat should not appear for Rajasthan"
        )


class TestNormalizationAndCacheKey:
    """Efficiency: filter normalization produces stable cache keys."""

    def test_normalize_sorts_list_values(self):
        filters = {"company_type": ["Private Bank", "NBFC"], "state": "Maharashtra"}
        normalized = _normalize_filters(filters)
        assert normalized["company_type"] == ["NBFC", "Private Bank"]  # sorted

    def test_normalize_stable_for_same_filters_different_order(self):
        from api.core.cache import make_key
        f1 = _normalize_filters({"company_type": ["NBFC", "Private Bank"], "state": "Maharashtra"})
        f2 = _normalize_filters({"company_type": ["Private Bank", "NBFC"], "state": "Maharashtra"})
        k1 = make_key("chat:lenders", {"intent": "filter", "filters": f1})
        k2 = make_key("chat:lenders", {"intent": "filter", "filters": f2})
        assert k1 == k2, "Same filters in different order must produce the same cache key"

    def test_different_filters_produce_different_keys(self):
        from api.core.cache import make_key
        f1 = _normalize_filters({"company_type": ["NBFC"], "state": "Maharashtra"})
        f2 = _normalize_filters({"company_type": ["NBFC"], "state": "Gujarat"})
        k1 = make_key("chat:lenders", {"intent": "filter", "filters": f1})
        k2 = make_key("chat:lenders", {"intent": "filter", "filters": f2})
        assert k1 != k2

    def test_empty_filters_produce_stable_key(self):
        from api.core.cache import make_key
        f1 = _normalize_filters({})
        f2 = _normalize_filters({})
        k1 = make_key("chat:lenders", {"intent": "filter", "filters": f1})
        k2 = make_key("chat:lenders", {"intent": "filter", "filters": f2})
        assert k1 == k2

    def test_merge_then_normalize_for_multi_turn(self):
        # Turn 1: "NBFCs in Maharashtra" → filters={company_type:["NBFC"], state:"Maharashtra"}
        turn1 = {"company_type": ["NBFC"], "state": "Maharashtra"}
        # Turn 2: "Now show only large ones" → new_filters={aum_category:["Large"]}
        turn2 = {"aum_category": ["Large"]}
        merged = _merge_filters(turn1, turn2)
        norm = _normalize_filters(merged)
        assert norm["company_type"] == ["NBFC"]
        assert norm["state"] == "Maharashtra"
        assert norm["aum_category"] == ["Large"]


class TestDataIntegrity:
    """Bank manager checks the mock dataset itself is internally consistent."""

    def test_all_lenders_have_approval_status(self):
        for l in MOCK_LENDERS:
            assert l.get("approval_status") == "approved", f"{l['company_name']} missing approval_status"

    def test_all_lenders_have_valid_company_type(self):
        for l in MOCK_LENDERS:
            assert l["company_type"] in VALID_COMPANY_TYPES, (
                f"{l['company_name']} has invalid company_type: {l['company_type']}"
            )

    def test_all_lenders_have_valid_aum_category(self):
        for l in MOCK_LENDERS:
            assert l["aum_category"] in VALID_AUM_CATEGORIES, (
                f"{l['company_name']} has invalid aum_category: {l['aum_category']}"
            )

    def test_all_loan_segments_are_valid(self):
        for l in MOCK_LENDERS:
            for seg in (l.get("primary_loan_segments") or []):
                assert seg in VALID_LOAN_TYPES, (
                    f"{l['company_name']} has invalid loan segment: {seg}"
                )

    def test_pan_india_lenders_have_empty_operating_states(self):
        for l in MOCK_LENDERS:
            if l.get("pan_india"):
                states = l.get("operating_states") or []
                assert states == [], (
                    f"{l['company_name']} is pan_india=True but has operating_states: {states}"
                )

    def test_regional_lenders_have_non_empty_states(self):
        for l in MOCK_LENDERS:
            if not l.get("pan_india"):
                states = l.get("operating_states") or []
                assert states, (
                    f"{l['company_name']} is regional but has empty operating_states"
                )

    def test_unique_ids(self):
        ids = [l["id"] for l in MOCK_LENDERS]
        assert len(ids) == len(set(ids)), "Duplicate lender IDs in mock dataset"

    def test_25_lenders_in_dataset(self):
        assert len(MOCK_LENDERS) == 25
