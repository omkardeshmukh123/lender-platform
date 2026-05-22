"""
Integration tests for intent classification accuracy.
Requires a real AI API key (OPENROUTER_API_KEY or GEMINI_API_KEY).

These tests call the live AI — they are slow and consume tokens.
Run explicitly: pytest backend/tests/test_intent_accuracy.py -v -s

Covers the 3 bugs fixed on 2026-05-22:
  P10 — multi-filter: company_type + pan_india both extracted
  P5  — "banks" synonym maps to bank company_types, not just sort
  P4/P8 — (answer-layer; tested via prompt structure in test_chat_queries.py)
Plus 7 additional edge cases.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent
sys.path.insert(0, str(_backend))
sys.path.insert(0, str(_backend / "api"))

# ---------------------------------------------------------------------------
# Skip entire module if no AI key is configured
# ---------------------------------------------------------------------------

_has_ai = bool(
    os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GEMINI_API_KEY")
)
pytestmark = pytest.mark.skipif(
    not _has_ai,
    reason="No OPENROUTER_API_KEY or GEMINI_API_KEY set — skipping live AI tests",
)


# ---------------------------------------------------------------------------
# Fixture: real AI client (created once per session)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ai_client():
    from core.ai_client import reset_ai_client, get_ai_client
    reset_ai_client()
    return get_ai_client()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse(ai_client, query: str) -> dict:
    return ai_client.parse_intent(query, [])


def _filters(parsed: dict) -> dict:
    return parsed.get("filters") or {}


def _ct(parsed: dict) -> list[str]:
    return _filters(parsed).get("company_type") or []


# ===========================================================================
# P10 — Bug: company_type dropped when pan_india present
# ===========================================================================

class TestSFBsPanIndia:
    """SFB + pan_india must BOTH appear in filters."""

    def test_sfbs_operating_pan_india(self, ai_client):
        p = _parse(ai_client, "SFBs operating pan India")
        assert p["intent"] == "filter", f"Expected filter, got {p['intent']}"
        assert "Small Finance Bank" in _ct(p), f"Missing SFB in company_type: {_ct(p)}"
        assert _filters(p).get("pan_india") is True, f"pan_india not set: {_filters(p)}"

    def test_small_finance_banks_across_india(self, ai_client):
        p = _parse(ai_client, "Small Finance Banks across India")
        assert p["intent"] == "filter"
        assert "Small Finance Bank" in _ct(p), f"company_type: {_ct(p)}"
        assert _filters(p).get("pan_india") is True, f"filters: {_filters(p)}"

    def test_nbfcs_pan_india(self, ai_client):
        p = _parse(ai_client, "NBFCs with pan India presence")
        assert p["intent"] == "filter"
        assert "NBFC" in _ct(p), f"company_type: {_ct(p)}"
        assert _filters(p).get("pan_india") is True, f"filters: {_filters(p)}"


# ===========================================================================
# P5 — Bug: "banks" queries produce no company_type filter
# ===========================================================================

class TestLargestBanks:
    """Queries about 'banks' must include bank company_types."""

    _BANK_TYPES = {"Private Bank", "PSU Bank", "Foreign Bank"}

    def _has_bank_type(self, parsed: dict) -> bool:
        return bool(self._BANK_TYPES & set(_ct(parsed)))

    def test_largest_banks_in_india(self, ai_client):
        p = _parse(ai_client, "Largest banks in India")
        assert p["intent"] == "filter"
        assert self._has_bank_type(p), f"Expected bank company_type, got: {_ct(p)}"
        assert _filters(p).get("sort_by") == "aum_crores", f"sort_by: {_filters(p)}"

    def test_top_banks_by_aum(self, ai_client):
        p = _parse(ai_client, "Top 5 banks by AUM")
        assert p["intent"] == "filter"
        assert self._has_bank_type(p), f"Expected bank company_type, got: {_ct(p)}"

    def test_psu_banks_pan_india(self, ai_client):
        """Multi-filter: specific bank type + pan_india."""
        p = _parse(ai_client, "PSU banks with pan India presence")
        assert p["intent"] == "filter"
        assert "PSU Bank" in _ct(p), f"company_type: {_ct(p)}"
        assert _filters(p).get("pan_india") is True, f"filters: {_filters(p)}"


# ===========================================================================
# Multi-filter combinations (general)
# ===========================================================================

class TestMultiFilter:
    """Verify that multiple filter dimensions are all retained."""

    def test_large_nbfcs_in_gujarat(self, ai_client):
        p = _parse(ai_client, "Large NBFCs in Gujarat")
        assert p["intent"] == "filter"
        assert "NBFC" in _ct(p), f"company_type: {_ct(p)}"
        assert _filters(p).get("state") == "Gujarat", f"state: {_filters(p).get('state')}"
        assert "Large" in (_filters(p).get("aum_category") or []), (
            f"aum_category: {_filters(p).get('aum_category')}"
        )

    def test_sfbs_in_maharashtra_msme(self, ai_client):
        """Three-way filter: company_type + state + loan_type."""
        p = _parse(ai_client, "SFBs in Maharashtra offering MSME loans")
        assert p["intent"] == "filter"
        assert "Small Finance Bank" in _ct(p), f"company_type: {_ct(p)}"
        assert _filters(p).get("state") == "Maharashtra", f"state: {_filters(p).get('state')}"
        assert "MSME Loan" in (_filters(p).get("loan_type") or []), (
            f"loan_type: {_filters(p).get('loan_type')}"
        )

    def test_state_filter_no_pan_india_override(self, ai_client):
        """'Lenders operating in Gujarat' should set state, NOT pan_india."""
        p = _parse(ai_client, "Lenders operating in Gujarat")
        assert p["intent"] == "filter"
        assert _filters(p).get("state") == "Gujarat", f"state: {_filters(p).get('state')}"
        # pan_india should not be True when user asks about a specific state
        assert _filters(p).get("pan_india") is not True, (
            f"pan_india should not be True for a state query: {_filters(p)}"
        )


# ===========================================================================
# Multi-state filter (states array)
# ===========================================================================

class TestMultiStateIntent:

    def test_two_states_produces_states_array(self, ai_client):
        r = _parse(ai_client, "Gold loan lenders in Maharashtra and Delhi")
        f = _filters(r)
        assert r["intent"] == "filter"
        assert "states" in f, "Expected states array, got: " + str(f)
        assert set(f["states"]) == {"Maharashtra", "Delhi"}

    def test_two_states_or_phrasing(self, ai_client):
        r = _parse(ai_client, "NBFCs in Gujarat or Rajasthan")
        f = _filters(r)
        assert r["intent"] == "filter"
        assert set(f.get("states", [])) == {"Gujarat", "Rajasthan"}

    def test_city_pair_resolves_to_states(self, ai_client):
        r = _parse(ai_client, "Home loan lenders in Mumbai and Chennai")
        f = _filters(r)
        assert r["intent"] == "filter"
        assert set(f.get("states", [])) == {"Maharashtra", "Tamil Nadu"}


# ===========================================================================
# Range queries — AUM and established_year
# ===========================================================================

class TestRangeQueryIntent:

    def test_aum_range(self, ai_client):
        r = _parse(ai_client, "NBFCs with AUM between 500 and 5000 crores")
        f = _filters(r)
        assert r["intent"] == "filter"
        assert f.get("aum_min") == 500
        assert f.get("aum_max") == 5000

    def test_aum_above(self, ai_client):
        r = _parse(ai_client, "Lenders with AUM above 10000 crores")
        f = _filters(r)
        assert r["intent"] == "filter"
        assert f.get("aum_min") == 10000

    def test_aum_below(self, ai_client):
        r = _parse(ai_client, "Small lenders with AUM below 1000 crores")
        f = _filters(r)
        assert r["intent"] == "filter"
        assert f.get("aum_max") == 1000

    def test_established_after(self, ai_client):
        r = _parse(ai_client, "Lenders established after 2010")
        f = _filters(r)
        assert r["intent"] == "filter"
        assert f.get("established_year_min") == 2011

    def test_established_before(self, ai_client):
        r = _parse(ai_client, "NBFCs founded before 2000")
        f = _filters(r)
        assert r["intent"] == "filter"
        assert f.get("established_year_max") == 1999

    def test_established_between(self, ai_client):
        r = _parse(ai_client, "Banks established between 1990 and 2005")
        f = _filters(r)
        assert r["intent"] == "filter"
        assert f.get("established_year_min") == 1990
        assert f.get("established_year_max") == 2005


# ===========================================================================
# Refinement intent — LLM must emit only the NEW constraint
# ===========================================================================

class TestRefinementIntent:
    """The backend merges last_filters + new filters. LLM must emit only the new constraint."""

    def test_narrow_by_company_type(self, ai_client):
        history = [
            {"role": "user", "parts": ["Gold loan lenders in Maharashtra"]},
            {"role": "model", "parts": ["Found 20 lenders in Maharashtra offering Gold Loans..."]},
        ]
        r = ai_client.parse_intent("Show only NBFCs from those", history)
        assert r["intent"] == "filter"
        f = _filters(r)
        assert "NBFC" in f.get("company_type", [])

    def test_narrow_by_state(self, ai_client):
        history = [
            {"role": "user", "parts": ["Show all home loan lenders"]},
            {"role": "model", "parts": ["Found 45 lenders offering Home Loans..."]},
        ]
        r = ai_client.parse_intent("Now show only ones in Delhi", history)
        assert r["intent"] == "filter"
        f = _filters(r)
        assert f.get("state") == "Delhi" or "Delhi" in f.get("states", [])

    def test_narrow_by_aum(self, ai_client):
        history = [
            {"role": "user", "parts": ["NBFCs in Maharashtra"]},
            {"role": "model", "parts": ["Found 30 NBFCs in Maharashtra..."]},
        ]
        r = ai_client.parse_intent("Show only large AUM from those results", history)
        assert r["intent"] == "filter"
        f = _filters(r)
        assert "Large" in f.get("aum_category", [])

    def test_narrow_by_loan_type(self, ai_client):
        history = [
            {"role": "user", "parts": ["Show all SFBs"]},
            {"role": "model", "parts": ["Found 12 Small Finance Banks..."]},
        ]
        r = ai_client.parse_intent("Filter to only those with gold loan", history)
        assert r["intent"] == "filter"
        f = _filters(r)
        assert "Gold Loan" in f.get("loan_type", [])
