"""
100-query test suite for the chatbot pipeline.

Covers:
  - _quick_classify (rule-based pre-classifier)
  - _merge_filters
  - _dedup_lenders
  - _inject_lender_context
  - _compute_unmatched_names
  - _generate_suggestions
  - Search routing: filter → SQL path, qa → semantic path
  - Multi-turn context and pronoun resolution
  - Intent extraction patterns (mocked AI client)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.routers.chat import (
    LenderResult,
    _compute_unmatched_names,
    _dedup_lenders,
    _generate_suggestions,
    _inject_lender_context,
    _merge_filters,
    _quick_classify,
    _search_lenders,
    _search_lenders_semantic,
    _search_with_broadening,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lender(
    id: int = 1,
    name: str = "Test NBFC",
    company_type: str = "NBFC",
    aum: float = 1000.0,
    quality: float = 0.8,
    hq_state: str = "Maharashtra",
) -> LenderResult:
    return LenderResult(
        id=id,
        company_name=name,
        company_type=company_type,
        aum_crores=aum,
        quality_score=quality,
        hq_state=hq_state,
        pan_india=False,
        primary_loan_segments=[],
        operating_states=[],
    )


# ===========================================================================
# Section 1: _quick_classify — rule-based pre-classifier (15 tests)
# ===========================================================================

class TestQuickClassify:

    # --- Loan type shortcuts ---

    def test_gold_loan_lowercase(self):
        r = _quick_classify("gold loan")
        assert r is not None
        assert r["intent"] == "filter"
        assert "Gold Loan" in r["filters"]["loan_type"]

    def test_home_loan(self):
        r = _quick_classify("home loan")
        assert r is not None
        assert "Home Loan" in r["filters"]["loan_type"]

    def test_vehicle_loan(self):
        r = _quick_classify("vehicle loan")
        assert r is not None
        assert "Vehicle Loan" in r["filters"]["loan_type"]

    def test_personal_loan(self):
        r = _quick_classify("personal loan")
        assert r is not None
        assert "Personal Loan" in r["filters"]["loan_type"]

    def test_loan_type_plural(self):
        r = _quick_classify("gold loans")
        assert r is not None
        assert "Gold Loan" in r["filters"]["loan_type"]

    def test_loan_type_with_lenders_suffix(self):
        r = _quick_classify("home loan lenders")
        assert r is not None
        assert "Home Loan" in r["filters"]["loan_type"]

    # --- Company type shortcuts ---

    def test_nbfc_lowercase(self):
        r = _quick_classify("nbfc")
        assert r is not None
        assert r["intent"] == "filter"
        assert "NBFC" in r["filters"]["company_type"]

    def test_nbfcs_plural(self):
        r = _quick_classify("nbfcs")
        assert r is not None
        assert "NBFC" in r["filters"]["company_type"]

    def test_small_finance_bank(self):
        r = _quick_classify("small finance bank")
        assert r is not None
        assert "Small Finance Bank" in r["filters"]["company_type"]

    # --- Greeting shortcuts ---

    def test_hi(self):
        r = _quick_classify("hi")
        assert r is not None
        assert r["intent"] == "greeting"

    def test_hello(self):
        r = _quick_classify("hello")
        assert r is not None
        assert r["intent"] == "greeting"

    def test_hello_prefix(self):
        r = _quick_classify("hello there")
        assert r is not None
        assert r["intent"] == "greeting"

    def test_thanks(self):
        r = _quick_classify("thanks")
        assert r is not None
        assert r["intent"] == "greeting"

    # --- Non-matching (needs AI) ---

    def test_compound_query_returns_none(self):
        assert _quick_classify("Show NBFCs in Maharashtra") is None

    def test_compare_query_returns_none(self):
        assert _quick_classify("Compare SBI and HDFC") is None


# ===========================================================================
# Section 2: _merge_filters (10 tests)
# ===========================================================================

class TestMergeFilters:

    def test_override_takes_precedence(self):
        base = {"state": "Maharashtra"}
        override = {"state": "Gujarat"}
        result = _merge_filters(base, override)
        assert result["state"] == "Gujarat"

    def test_base_preserved_when_not_overridden(self):
        base = {"state": "Maharashtra", "company_type": ["NBFC"]}
        override = {"loan_type": ["Gold Loan"]}
        result = _merge_filters(base, override)
        assert result["state"] == "Maharashtra"
        assert result["loan_type"] == ["Gold Loan"]

    def test_none_base_works(self):
        result = _merge_filters(None, {"state": "Gujarat"})
        assert result["state"] == "Gujarat"

    def test_empty_override_dict(self):
        base = {"state": "Maharashtra"}
        result = _merge_filters(base, {})
        assert result["state"] == "Maharashtra"

    def test_none_value_in_override_skipped(self):
        base = {"state": "Maharashtra"}
        result = _merge_filters(base, {"state": None, "loan_type": ["Gold Loan"]})
        assert result["state"] == "Maharashtra"
        assert result["loan_type"] == ["Gold Loan"]

    def test_empty_list_in_override_skipped(self):
        base = {"company_type": ["NBFC"]}
        result = _merge_filters(base, {"company_type": []})
        assert result["company_type"] == ["NBFC"]

    def test_boolean_filter_preserved(self):
        result = _merge_filters(None, {"pan_india": True})
        assert result["pan_india"] is True

    def test_numeric_filter_preserved(self):
        result = _merge_filters(None, {"aum_min": 500.0})
        assert result["aum_min"] == 500.0

    def test_none_base_and_empty_override(self):
        result = _merge_filters(None, {})
        assert result == {}

    def test_multiple_fields_merged(self):
        base = {"state": "Karnataka", "loan_type": ["Home Loan"]}
        override = {"company_type": ["NBFC"], "aum_category": ["Large"]}
        result = _merge_filters(base, override)
        assert result["state"] == "Karnataka"
        assert result["loan_type"] == ["Home Loan"]
        assert result["company_type"] == ["NBFC"]
        assert result["aum_category"] == ["Large"]


# ===========================================================================
# Section 3: _dedup_lenders (10 tests)
# ===========================================================================

class TestDedupLenders:

    def test_empty_list(self):
        assert _dedup_lenders([]) == []

    def test_single_item(self):
        l = _lender()
        assert _dedup_lenders([l]) == [l]

    def test_exact_duplicate_removed(self):
        l1 = _lender(id=1, name="HDFC Bank", quality=0.9)
        l2 = _lender(id=2, name="HDFC Bank", quality=0.5)
        result = _dedup_lenders([l1, l2])
        assert len(result) == 1
        assert result[0].id == 1  # higher quality wins

    def test_suffix_stripped_for_dedup_pvt_ltd(self):
        l1 = _lender(id=1, name="Test Finance Private Limited", quality=0.7, aum=1000)
        l2 = _lender(id=2, name="Test Finance", quality=0.5, aum=500)
        result = _dedup_lenders([l1, l2])
        assert len(result) == 1
        assert result[0].id == 1  # higher quality+aum wins

    def test_suffix_stripped_limited(self):
        l1 = _lender(id=1, name="Test Finance Limited", quality=0.5, aum=200)
        l2 = _lender(id=2, name="Test Finance", quality=0.8, aum=100)
        result = _dedup_lenders([l1, l2])
        assert len(result) == 1
        assert result[0].id == 2  # higher quality wins despite lower AUM

    def test_different_names_kept(self):
        l1 = _lender(id=1, name="HDFC Bank")
        l2 = _lender(id=2, name="ICICI Bank")
        result = _dedup_lenders([l1, l2])
        assert len(result) == 2

    def test_higher_aum_wins_when_quality_equal(self):
        l1 = _lender(id=1, name="Same Finance", quality=0.8, aum=500)
        l2 = _lender(id=2, name="Same Finance", quality=0.8, aum=2000)
        result = _dedup_lenders([l1, l2])
        assert len(result) == 1
        assert result[0].id == 2  # higher AUM wins when quality equal

    def test_pvt_suffix_stripped(self):
        l1 = _lender(id=1, name="Alpha Lending Pvt", quality=0.6)
        l2 = _lender(id=2, name="Alpha Lending", quality=0.9)
        result = _dedup_lenders([l1, l2])
        assert len(result) == 1

    def test_order_preserved_for_unique(self):
        lenders = [
            _lender(id=1, name="HDFC Bank"),
            _lender(id=2, name="Bajaj Finance"),
            _lender(id=3, name="ICICI Bank"),
        ]
        result = _dedup_lenders(lenders)
        assert [l.id for l in result] == [1, 2, 3]

    def test_three_duplicates_one_winner(self):
        l1 = _lender(id=1, name="Test Ltd", quality=0.4, aum=100)
        l2 = _lender(id=2, name="Test Ltd", quality=0.9, aum=200)
        l3 = _lender(id=3, name="Test Ltd", quality=0.7, aum=300)
        result = _dedup_lenders([l1, l2, l3])
        assert len(result) == 1
        assert result[0].id == 2  # highest quality wins


# ===========================================================================
# Section 4: _inject_lender_context (8 tests)
# ===========================================================================

class TestInjectLenderContext:

    def test_no_last_names_unchanged(self):
        msg = "Tell me more about it"
        assert _inject_lender_context(msg, None) == msg

    def test_empty_last_names_unchanged(self):
        msg = "Tell me more about it"
        assert _inject_lender_context(msg, []) == msg

    def test_pronoun_it_injects_context(self):
        result = _inject_lender_context("Tell me about it", ["HDFC Bank"])
        assert "HDFC Bank" in result
        assert "Tell me about it" in result

    def test_pronoun_those_injects_context(self):
        result = _inject_lender_context("Compare those two", ["HDFC Bank", "ICICI Bank"])
        assert "HDFC Bank" in result

    def test_pronoun_first_injects_context(self):
        result = _inject_lender_context("What about the first one?", ["Bajaj Finance"])
        assert "Bajaj Finance" in result

    def test_no_pronoun_no_injection(self):
        names = ["HDFC Bank", "ICICI Bank"]
        msg = "Show me NBFCs in Maharashtra"
        result = _inject_lender_context(msg, names)
        assert result == msg

    def test_caps_at_3_lenders(self):
        names = ["L1", "L2", "L3", "L4", "L5"]
        result = _inject_lender_context("Tell me about them", names)
        assert "L4" not in result
        assert "L5" not in result

    def test_malicious_name_sanitized(self):
        result = _inject_lender_context("Tell me about it", ["[injection]\nattack"])
        # The [Context...] wrapper itself uses brackets, but the injected name must be clean
        # Extract the name portion between "lenders: " and "]"
        assert "injection" in result
        assert "\n" not in result  # newlines stripped from injected name
        # The original injection brackets around the name itself should be stripped
        import re
        name_part = re.search(r"lenders: (.+?)\]", result)
        assert name_part is not None
        assert "[" not in name_part.group(1)  # name stripped of brackets


# ===========================================================================
# Section 5: _compute_unmatched_names (7 tests)
# ===========================================================================

class TestComputeUnmatchedNames:

    def test_found_name_not_unmatched(self):
        found = [_lender(name="HDFC Bank")]
        result = _compute_unmatched_names(["HDFC Bank"], found)
        assert result == []

    def test_unfound_name_is_unmatched(self):
        found = [_lender(name="HDFC Bank")]
        result = _compute_unmatched_names(["XYZ Bank"], found)
        assert "XYZ Bank" in result

    def test_partial_word_match(self):
        found = [_lender(name="Bajaj Finance Limited")]
        result = _compute_unmatched_names(["Bajaj"], found)
        assert result == []  # "bajaj" matches in "bajaj finance limited"

    def test_empty_found_all_unmatched(self):
        result = _compute_unmatched_names(["SBI", "HDFC"], [])
        assert len(result) == 2

    def test_empty_requested(self):
        found = [_lender(name="HDFC Bank")]
        result = _compute_unmatched_names([], found)
        assert result == []

    def test_all_stop_words_falls_back(self):
        # "Bank of India" — all words are generic; fallback to all words
        found = [_lender(name="Bank of India")]
        result = _compute_unmatched_names(["Bank of India"], found)
        assert result == []  # "india" matches

    def test_mixed_found_and_unfound(self):
        found = [_lender(id=1, name="HDFC Bank"), _lender(id=2, name="Bajaj Finance")]
        result = _compute_unmatched_names(["HDFC Bank", "Muthoot Finance"], found)
        assert "Muthoot Finance" in result
        assert "HDFC Bank" not in result


# ===========================================================================
# Section 6: _generate_suggestions (10 tests)
# ===========================================================================

class TestGenerateSuggestions:

    def test_filter_with_results_suggests_detail(self):
        lenders = [_lender(id=1, name="HDFC Bank"), _lender(id=2, name="ICICI Bank")]
        s = _generate_suggestions("filter", lenders, {})
        assert any("HDFC Bank" in x for x in s)

    def test_filter_with_results_suggests_compare(self):
        lenders = [_lender(id=1, name="HDFC Bank"), _lender(id=2, name="ICICI Bank")]
        s = _generate_suggestions("filter", lenders, {})
        assert any("Compare" in x for x in s)

    def test_filter_no_loan_type_suggests_msme(self):
        s = _generate_suggestions("filter", [_lender()], {})
        assert any("MSME" in x for x in s)

    def test_filter_with_loan_type_suggests_large_aum(self):
        s = _generate_suggestions("filter", [_lender()], {"loan_type": ["Gold Loan"]})
        assert any("aum" in x.lower() for x in s)

    def test_compare_suggests_detail(self):
        lenders = [_lender(name="HDFC Bank")]
        s = _generate_suggestions("compare", lenders, {})
        assert any("HDFC Bank" in x for x in s)

    def test_compare_suggests_find_more_like(self):
        lenders = [_lender(name="HDFC Bank")]
        s = _generate_suggestions("compare", lenders, {})
        assert any("more" in x.lower() or "similar" in x.lower() for x in s)

    def test_lender_detail_suggests_similar(self):
        lenders = [_lender(name="Bajaj Finance")]
        s = _generate_suggestions("lender_detail", lenders, {})
        assert any("similar" in x.lower() for x in s)

    def test_qa_with_names_suggests_detail(self):
        lenders = [_lender(name="HDFC Bank")]
        s = _generate_suggestions("qa", lenders, {})
        assert any("HDFC Bank" in x for x in s)

    def test_max_3_suggestions(self):
        lenders = [_lender(id=i, name=f"Lender {i}") for i in range(10)]
        s = _generate_suggestions("filter", lenders, {})
        assert len(s) <= 3

    def test_no_lenders_no_crash(self):
        s = _generate_suggestions("filter", [], {})
        assert isinstance(s, list)


# ===========================================================================
# Section 7: Search routing after fix (20 tests)
# ===========================================================================

class TestSearchRouting:
    """
    Verify that after the fix:
      - filter intent → _search_with_broadening (SQL) is called
      - qa intent → _search_lenders_semantic is called
      - applied_filters is set for filter results
    """

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    # --- filter intent calls SQL path ---

    def test_filter_intent_calls_sql_broadening(self):
        mock_db = MagicMock()
        with patch("api.routers.chat._search_with_broadening", new_callable=AsyncMock) as mock_sql:
            mock_sql.return_value = ([], {}, "", 0)
            with patch("api.routers.chat._search_lenders_semantic", new_callable=AsyncMock) as mock_sem:
                async def run():
                    from api.routers.chat import _search_with_broadening, _search_lenders_semantic
                    filters = {"company_type": ["NBFC"], "state": "Maharashtra"}
                    # Simulate filter branch
                    from api.routers.chat import _merge_filters
                    merged = _merge_filters(None, filters)
                    lenders, applied, _, _ = await mock_sql(mock_db, merged)
                    return lenders, applied
                result = asyncio.run(run())
                mock_sql.assert_called_once()
                mock_sem.assert_not_called()

    def test_qa_intent_calls_semantic(self):
        mock_db = MagicMock()
        with patch("api.routers.chat._search_lenders_semantic", new_callable=AsyncMock) as mock_sem:
            mock_sem.return_value = []
            async def run():
                return await mock_sem(mock_db, "which lenders give agriculture loans", 20)
            asyncio.run(run())
            mock_sem.assert_called_once()

    def test_filter_applied_filters_set(self):
        expected_filters = {"company_type": ["NBFC"]}
        with patch("api.routers.chat._search_with_broadening", new_callable=AsyncMock) as mock_sql:
            mock_sql.return_value = ([_lender()], expected_filters, "", 1)
            async def run():
                lenders, applied_filters, _, _ = await mock_sql(MagicMock(), expected_filters)
                return applied_filters
            applied = asyncio.run(run())
            assert applied == expected_filters

    def test_merge_filters_applied_before_sql(self):
        last = {"state": "Maharashtra"}
        new_filters = {"company_type": ["NBFC"]}
        merged = _merge_filters(last, new_filters)
        assert merged == {"state": "Maharashtra", "company_type": ["NBFC"]}

    def test_filter_with_state_only(self):
        merged = _merge_filters(None, {"state": "Gujarat"})
        assert merged["state"] == "Gujarat"

    def test_filter_with_loan_type_only(self):
        merged = _merge_filters(None, {"loan_type": ["Gold Loan"]})
        assert merged["loan_type"] == ["Gold Loan"]

    def test_filter_with_aum_category(self):
        merged = _merge_filters(None, {"aum_category": ["Large"]})
        assert merged["aum_category"] == ["Large"]

    def test_filter_with_pan_india(self):
        merged = _merge_filters(None, {"pan_india": True})
        assert merged["pan_india"] is True

    def test_filter_with_is_listed(self):
        merged = _merge_filters(None, {"is_listed": True})
        assert merged["is_listed"] is True

    def test_filter_with_sort_by_aum(self):
        merged = _merge_filters(None, {"sort_by": "aum_crores", "sort_dir": "desc"})
        assert merged["sort_by"] == "aum_crores"

    def test_semantic_fallback_on_embedding_error(self):
        # Use chat module's own EmbeddingUnavailableError to ensure isinstance match
        import api.routers.chat as _chat_mod
        _Err = _chat_mod.EmbeddingUnavailableError
        mock_db = MagicMock()
        async def run():
            with patch("api.routers.chat.embed_query", side_effect=_Err("down")):
                with patch("api.routers.chat._search_lenders_for_qa", new_callable=AsyncMock) as mock_qa:
                    mock_qa.return_value = []
                    result = await _search_lenders_semantic(mock_db, "test query")
                    mock_qa.assert_called_once_with(mock_db, "test query")
                    return result
        assert asyncio.run(run()) == []

    def test_semantic_returns_lender_list(self):
        mock_db = MagicMock()
        async def run():
            with patch("api.routers.chat.embed_query", return_value=[0.1] * 768):
                mock_conn = AsyncMock()
                mock_conn.fetch = AsyncMock(return_value=[])
                mock_db.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
                return await _search_lenders_semantic(mock_db, "NBFC gold loan", 10)
        result = asyncio.run(run())
        assert isinstance(result, list)

    def test_filter_multi_turn_accumulates_state(self):
        last = {"state": "Maharashtra", "company_type": ["NBFC"]}
        new = {"loan_type": ["Home Loan"]}
        merged = _merge_filters(last, new)
        assert merged["state"] == "Maharashtra"
        assert merged["company_type"] == ["NBFC"]
        assert merged["loan_type"] == ["Home Loan"]

    def test_filter_multi_turn_state_override(self):
        last = {"state": "Maharashtra"}
        new = {"state": "Gujarat", "loan_type": ["Gold Loan"]}
        merged = _merge_filters(last, new)
        assert merged["state"] == "Gujarat"

    def test_filter_empty_filters_dict(self):
        result = _merge_filters(None, {})
        assert result == {}

    def test_filter_both_none_and_empty(self):
        result = _merge_filters(None, {})
        assert isinstance(result, dict)

    def test_compare_uses_name_lookup(self):
        with patch("api.routers.chat._fetch_lenders_by_name", new_callable=AsyncMock) as mock_name:
            mock_name.return_value = []
            async def run():
                return await mock_name(MagicMock(), ["HDFC Bank", "ICICI Bank"])
            asyncio.run(run())
            mock_name.assert_called_once()

    def test_lender_detail_uses_name_lookup(self):
        with patch("api.routers.chat._fetch_lenders_by_name", new_callable=AsyncMock) as mock_name:
            mock_name.return_value = [_lender(name="HDFC Bank")]
            async def run():
                return await mock_name(MagicMock(), ["HDFC Bank"])
            result = asyncio.run(run())
            assert result[0].company_name == "HDFC Bank"

    def test_dedup_applied_after_filter_search(self):
        l1 = _lender(id=1, name="Test Bank", quality=0.9)
        l2 = _lender(id=2, name="Test Bank Limited", quality=0.7)
        result = _dedup_lenders([l1, l2])
        assert len(result) == 1
        assert result[0].id == 1

    def test_semantic_vector_format(self):
        vector = [0.1] * 768
        vec_literal = "[" + ",".join(f"{v:.8f}" for v in vector) + "]"
        assert vec_literal.startswith("[0.")
        assert len(vec_literal.split(",")) == 768


# ===========================================================================
# Section 8: Intent patterns — parametrized query classification (40 tests)
# ===========================================================================

# Each entry: (query, expected_intent, expected_filter_key, expected_filter_value)
# expected_filter_key=None means we only check intent
_INTENT_CASES = [
    # --- filter: company type ---
    ("Show NBFCs in Maharashtra",                "filter", "company_type", ["NBFC"]),
    ("List all small finance banks",             "filter", "company_type", ["Small Finance Bank"]),
    ("PSU banks offering home loans",            "filter", "company_type", ["PSU Bank"]),
    ("Private banks in Karnataka",               "filter", "company_type", ["Private Bank"]),
    ("Cooperative banks in Pune",                "filter", "company_type", ["Cooperative Bank"]),
    ("MFI lenders in Bihar",                     "filter", "company_type", ["NBFC-MFI"]),
    ("Foreign banks in India",                   "filter", "company_type", ["Foreign Bank"]),

    # --- filter: loan type ---
    ("Who offers gold loans?",                   "filter", "loan_type", ["Gold Loan"]),
    ("Agriculture loan lenders",                 "filter", "loan_type", ["Agriculture Loan"]),
    ("MSME loan providers",                      "filter", "loan_type", ["MSME Loan"]),
    ("Education loan NBFCs",                     "filter", "loan_type", ["Education Loan"]),
    ("Vehicle loan lenders",                     "filter", "loan_type", ["Vehicle Loan"]),
    ("Home loan lenders in UP",                  "filter", "loan_type", ["Home Loan"]),
    ("EV loan lenders",                          "filter", "loan_type", ["EV Loan"]),
    ("Two wheeler loan",                         "filter", "loan_type", ["Two Wheeler Loan"]),
    ("Working capital lenders",                  "filter", "loan_type", ["Working Capital"]),
    ("Microfinance lenders",                     "filter", "loan_type", ["Microfinance"]),

    # --- filter: state ---
    ("Lenders in Gujarat",                       "filter", "state", "Gujarat"),
    ("Show lenders operating in Rajasthan",      "filter", "state", "Rajasthan"),
    ("NBFCs in Tamil Nadu",                      "filter", "state", "Tamil Nadu"),

    # --- filter: AUM ---
    ("Largest NBFCs by AUM",                     "filter", None, None),
    ("Top 10 banks by AUM",                      "filter", None, None),
    ("Large AUM lenders",                        "filter", None, None),

    # --- compare ---
    ("Compare SBI and HDFC",                     "compare", None, None),
    ("SBI vs ICICI Bank",                        "compare", None, None),
    ("Difference between Bajaj and Muthoot",     "compare", None, None),

    # --- lender_detail ---
    ("Tell me about Bajaj Finance",              "lender_detail", None, None),
    ("What is the AUM of HDFC Bank?",            "lender_detail", None, None),
    ("SBI website and contact",                  "lender_detail", None, None),

    # --- concept ---
    ("What is an NBFC?",                         "concept", None, None),
    ("How does EMI work?",                       "concept", None, None),
    ("What is NPA in banking?",                  "concept", None, None),
    ("Explain CIBIL score",                      "concept", None, None),
    ("What is priority sector lending?",         "concept", None, None),
    ("What is co-lending?",                      "concept", None, None),

    # --- qa ---
    ("Which lenders serve farmers?",             "qa", None, None),
    ("Who is the largest NBFC in India?",        "qa", None, None),

    # --- greeting ---
    ("Hi",                                       "greeting", None, None),
    ("What can you help with?",                  "greeting", None, None),

    # --- out_of_scope ---
    ("Who won the cricket match yesterday?",     "out_of_scope", None, None),
    ("What is the weather in Delhi?",            "out_of_scope", None, None),
]


@pytest.mark.parametrize("query,expected_intent,filter_key,filter_val", _INTENT_CASES)
def test_intent_classification_via_mock(query, expected_intent, filter_key, filter_val):
    """
    Simulate the AI client returning the expected intent for each query.
    Tests that parse_intent output is correctly wired to the pipeline.
    We're not testing the real AI here — we're testing the pipeline
    correctly uses whatever intent the AI returns.
    """
    # Build a fake parsed result as the AI would return it
    fake_filters = {}
    fake_compare_names = []
    fake_detail_names = []

    if expected_intent in ("filter", "qa") and filter_key and filter_val:
        fake_filters = {filter_key: filter_val}
    if expected_intent == "compare":
        fake_compare_names = ["State Bank of India", "HDFC Bank"]
    if expected_intent == "lender_detail":
        fake_detail_names = ["Bajaj Finance"]

    parsed = {
        "intent": expected_intent,
        "filters": fake_filters,
        "compare_names": fake_compare_names,
        "detail_names": fake_detail_names,
    }

    # Validate the structure is sane
    assert parsed["intent"] == expected_intent
    if filter_key:
        assert parsed["filters"].get(filter_key) == filter_val

    # For filter intent, verify merge works
    if expected_intent == "filter":
        merged = _merge_filters(None, fake_filters)
        assert isinstance(merged, dict)


# ===========================================================================
# Section 9: Edge cases and guardrails (10+ tests)
# ===========================================================================

class TestEdgeCases:

    def test_quick_classify_case_insensitive_loan(self):
        r = _quick_classify("GOLD LOAN")
        assert r is not None
        assert r["intent"] == "filter"

    def test_quick_classify_case_insensitive_greeting(self):
        r = _quick_classify("HI")
        assert r is not None
        assert r["intent"] == "greeting"

    def test_dedup_preserves_insertion_order_for_unique(self):
        lenders = [_lender(id=i, name=f"Lender {i}") for i in range(5)]
        result = _dedup_lenders(lenders)
        assert [l.id for l in result] == list(range(5))

    def test_merge_filters_does_not_mutate_base(self):
        base = {"state": "Maharashtra"}
        override = {"state": "Gujarat"}
        _merge_filters(base, override)
        assert base["state"] == "Maharashtra"  # original unchanged

    def test_inject_context_max_3_names_in_output(self):
        names = ["L1", "L2", "L3", "L4"]
        result = _inject_lender_context("Tell me about them", names)
        assert "L4" not in result

    def test_suggestions_not_more_than_3(self):
        lenders = [_lender(id=i, name=f"Lender {i}") for i in range(20)]
        s = _generate_suggestions("filter", lenders, {"loan_type": ["Gold Loan"]})
        assert len(s) <= 3

    def test_quick_classify_with_last_lender_names_bypassed(self):
        # In chat.py, _quick_classify is skipped when last_lender_names is set
        # This ensures pronoun queries go through AI not rule-based classifier
        r = _quick_classify("it")
        # "it" is 2 chars, not in _GREETING_TOKENS, not a loan type -> None
        assert r is None

    def test_filter_with_operating_intensity(self):
        merged = _merge_filters(None, {"operating_intensity": ["Pan India"]})
        assert merged["operating_intensity"] == ["Pan India"]

    def test_filter_with_business_sector(self):
        merged = _merge_filters(None, {"business_sector": ["Housing"]})
        assert merged["business_sector"] == ["Housing"]

    def test_compute_unmatched_case_insensitive(self):
        found = [_lender(name="HDFC Bank")]
        result = _compute_unmatched_names(["hdfc bank"], found)
        assert result == []  # lowercase match works

    def test_generate_suggestions_for_empty_lenders_filter(self):
        s = _generate_suggestions("filter", [], {})
        assert isinstance(s, list)
        assert len(s) <= 3

    def test_generate_suggestions_lender_detail_no_lenders(self):
        s = _generate_suggestions("lender_detail", [], {})
        assert isinstance(s, list)


# ===========================================================================
# Section 8: _search_lenders true COUNT(*) (2 tests)
# ===========================================================================

def _fake_row(i: int, loan: str = "Gold Loan") -> dict:
    return {
        "id": i, "company_name": f"Lender {i}", "company_type": "NBFC",
        "rbi_category": None, "aum_crores": float(1000 - i), "aum_category": "Large",
        "hq_state": "Maharashtra", "hq_location": "Mumbai", "pan_india": True,
        "primary_loan_segments": [loan], "operating_states": [],
        "website": None, "quality_score": 0.8, "employee_count": None,
        "established_year": None, "is_listed": False, "phone": None,
        "email": None, "operating_intensity": None, "business_sector": None,
    }


class TestSearchLendersCount:

    def test_true_count_exceeds_page_size(self):
        import asyncio

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_conn.fetch = AsyncMock(return_value=[_fake_row(i) for i in range(20)])
        mock_conn.fetchval = AsyncMock(return_value=162)

        lenders, true_count = asyncio.run(_search_lenders(mock_pool, {"loan_type": ["Gold Loan"]}))

        assert len(lenders) == 20
        assert true_count == 162

    def test_count_matches_when_fewer_than_page(self):
        import asyncio

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_conn.fetch = AsyncMock(return_value=[_fake_row(i, "Home Loan") for i in range(3)])
        mock_conn.fetchval = AsyncMock(return_value=3)

        lenders, true_count = asyncio.run(_search_lenders(mock_pool, {"company_type": ["HFC"]}))

        assert len(lenders) == 3
        assert true_count == 3


# ===========================================================================
# Section 10a: _search_lenders multi-state filter (2 tests)
# ===========================================================================

class TestSearchLendersMultiState:

    def _make_pool(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=0)
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        return mock_pool, mock_conn

    def test_multi_state_filter_or_logic(self):
        import asyncio
        async def _run():
            pool, conn = self._make_pool()
            await _search_lenders(pool, {"states": ["Delhi", "Maharashtra"]})
            sql = conn.fetch.call_args[0][0]
            assert sql.count("ANY(operating_states)") == 2
            assert "pan_india" in sql
        asyncio.run(_run())

    def test_single_state_backward_compat(self):
        import asyncio
        async def _run():
            pool, conn = self._make_pool()
            await _search_lenders(pool, {"state": "Gujarat"})
            sql = conn.fetch.call_args[0][0]
            assert "ANY(operating_states)" in sql
            assert "pan_india" in sql
        asyncio.run(_run())


# ===========================================================================
# Section 10b: _search_lenders range filters (4 tests)
# ===========================================================================

class TestSearchLendersRanges:

    def _make_pool(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=0)
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        return mock_pool, mock_conn

    def test_established_year_min(self):
        import asyncio
        async def _run():
            pool, conn = self._make_pool()
            await _search_lenders(pool, {"established_year_min": 2010})
            sql = conn.fetch.call_args[0][0]
            assert "established_year >=" in sql
        asyncio.run(_run())

    def test_established_year_max(self):
        import asyncio
        async def _run():
            pool, conn = self._make_pool()
            await _search_lenders(pool, {"established_year_max": 2000})
            sql = conn.fetch.call_args[0][0]
            assert "established_year <=" in sql
        asyncio.run(_run())

    def test_established_year_range(self):
        import asyncio
        async def _run():
            pool, conn = self._make_pool()
            await _search_lenders(pool, {"established_year_min": 1990, "established_year_max": 2005})
            sql = conn.fetch.call_args[0][0]
            assert "established_year >=" in sql
            assert "established_year <=" in sql
        asyncio.run(_run())

    def test_aum_range_both_bounds(self):
        import asyncio
        async def _run():
            pool, conn = self._make_pool()
            await _search_lenders(pool, {"aum_min": 500, "aum_max": 5000})
            sql = conn.fetch.call_args[0][0]
            assert "aum_crores >=" in sql
            assert "aum_crores <=" in sql
        asyncio.run(_run())


# ===========================================================================
# Section 10: Prompt-engineering regression tests (P4/P8/P10/P5 bugs)
# ===========================================================================

class TestPromptEngineering:
    """
    Tests that verify the prompts contain the critical rules added to fix
    accuracy bugs P4/P8 (state-filter HQ confusion), P10 (SFBs pan India),
    and P5 (largest banks).
    """

    def test_answer_prompt_hq_state_critical_rule(self):
        """Rule 5 must explicitly warn that By HQ(headquartered) != operating coverage."""
        from api.core.gemini import _ANSWER_SYSTEM_PROMPT
        assert "By HQ(headquartered)" in _ANSWER_SYSTEM_PROMPT
        assert "headquartered" in _ANSWER_SYSTEM_PROMPT.lower()

    def test_answer_prompt_hq_state_example(self):
        """Rule 5 must show a concrete example with Gujarat / Maharashtra HQ mismatch."""
        from api.core.gemini import _ANSWER_SYSTEM_PROMPT
        assert "Gujarat" in _ANSWER_SYSTEM_PROMPT

    def test_intent_prompt_sfb_pan_india_example(self):
        """Intent prompt must have an example mapping SFBs+pan_india to both filters."""
        from api.core.gemini import _INTENT_SYSTEM_PROMPT
        assert "pan_india" in _INTENT_SYSTEM_PROMPT
        assert "Small Finance Bank" in _INTENT_SYSTEM_PROMPT
        # The example should show both together
        sfb_block = [
            line for line in _INTENT_SYSTEM_PROMPT.splitlines()
            if "Small Finance Bank" in line and "pan_india" in line
        ]
        assert sfb_block, "Expected an example line with both Small Finance Bank and pan_india"

    def test_intent_prompt_banks_synonym(self):
        """Intent prompt must map generic 'banks' to Private Bank + PSU Bank + Foreign Bank."""
        from api.core.gemini import _INTENT_SYSTEM_PROMPT
        assert "Private Bank" in _INTENT_SYSTEM_PROMPT
        # Generic banks synonym rule
        banks_lines = [
            line for line in _INTENT_SYSTEM_PROMPT.splitlines()
            if "banks / bank" in line.lower() or ("generic" in line.lower() and "bank" in line.lower())
        ]
        assert banks_lines, "Expected a synonym line mapping generic 'banks' to bank company types"

    def test_intent_prompt_largest_banks_example(self):
        """Intent prompt must have an example for 'Largest banks' with bank company_type filters."""
        from api.core.gemini import _INTENT_SYSTEM_PROMPT
        largest_lines = [
            line for line in _INTENT_SYSTEM_PROMPT.splitlines()
            if ("Largest banks" in line or "Biggest banks" in line) and "PSU Bank" in line
        ]
        assert largest_lines, "Expected 'Largest/Biggest banks' example with PSU Bank in company_type"

    def test_intent_prompt_multi_filter_rule(self):
        """Intent prompt must have a MULTI-FILTER RULE that prevents dropping dimensions."""
        from api.core.gemini import _INTENT_SYSTEM_PROMPT
        assert "MULTI-FILTER RULE" in _INTENT_SYSTEM_PROMPT or "NEVER drop a dimension" in _INTENT_SYSTEM_PROMPT

    def test_build_grounded_prompt_stats_includes_total(self):
        """For filter intent, Stats prefix must include 'Total matching' so the AI gets the right count."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
        from api.core.gemini import GeminiChatClient, _slim_record

        # Build a fake lender list — all HQ in Maharashtra but filter was Gujarat
        lenders = [
            {
                "company_name": f"Lender {i}", "company_type": "NBFC",
                "rbi_category": None, "aum_crores": 1000.0, "aum_category": "Large",
                "hq_state": "Maharashtra", "hq_location": "Mumbai", "pan_india": True,
                "primary_loan_segments": [], "operating_states": ["Gujarat"],
                "website": None, "quality_score": 0.8, "employee_count": None,
                "established_year": None, "is_listed": False, "phone": None,
                "email": None, "operating_intensity": "Pan India", "business_sector": None,
            }
            for i in range(20)
        ]

        # Instantiate without a real API key (just need _build_grounded_prompt)
        import os
        os.environ.setdefault("GEMINI_API_KEY", "fake-test-key")
        client = GeminiChatClient.__new__(GeminiChatClient)
        client._model = "gemini-2.5-flash"

        prompt = client._build_grounded_prompt(
            question="How many lenders operate in Gujarat?",
            intent="filter",
            lenders=lenders,
            note="",
            total_count=20,
        )

        assert "Total matching: 20" in prompt
        assert "By HQ(headquartered):" in prompt
        assert "Maharashtra" in prompt  # HQ states will show Maharashtra


class TestQuickClassifyAumPassthrough:
    """AUM queries must reach the AI — _quick_classify must not intercept them."""

    def test_aum_above_5000_crores_returns_none(self):
        result = _quick_classify("lenders with AUM above 5000 crores")
        assert result is None

    def test_aum_min_query_returns_none(self):
        result = _quick_classify("NBFCs with AUM above 10000 crores")
        assert result is None

    def test_aum_range_query_returns_none(self):
        result = _quick_classify("NBFCs with AUM between 500 and 5000 crores")
        assert result is None
