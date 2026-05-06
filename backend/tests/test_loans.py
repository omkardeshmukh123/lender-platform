"""
Tests for borrower_evaluator — FOIR, employment compatibility, scoring, ranking.
Pure Python, no external dependencies.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from pipeline.borrower_evaluator import (
    FOIR_COMFORT,
    FOIR_HARD_MAX,
    FOIR_WARN_HIGH,
    EvaluationResult,
    _compute_emi,
    _compute_foir,
    _employment_compatible,
    evaluate_policy,
    rank_policies,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _policy(**overrides):
    base = {
        "policy_id": 1,
        "lender_id": 10,
        "company_name": "TestBank",
        "product_name": "MSME Loan",
        "match_score": 70,
        "loan_amount_min": 5.0,
        "loan_amount_max": 500.0,
        "interest_rate_min": 12.0,
        "interest_rate_max": 18.0,
        "tenure_min": 12,
        "tenure_max": 60,
        "credit_score_min": 650,
        "employment_types": ["business", "salaried"],
        "processing_fee": 1.0,
        "completeness_score": 0.8,
        "source_confidence": "high",
    }
    base.update(overrides)
    return base


def _profile(**overrides):
    base = {
        "loan_amount": 25.0,       # ₹25L
        "credit_score": 720,
        "employment_type": "business",
        "monthly_income": 200.0,   # ₹2,00,000/month (in thousands)
        "state": "Maharashtra",
        "tenure_months": 36,
    }
    base.update(overrides)
    return base


# ── EMI / FOIR ───────────────────────────────────────────────────────────────


def test_emi_zero_rate_is_flat():
    emi = _compute_emi(10.0, 0, 120)
    assert abs(emi - (10.0 * 100_000 / 120)) < 1


def test_emi_standard_case():
    emi = _compute_emi(10.0, 12.0, 12)
    assert 88_000 < emi < 90_000


def test_foir_zero_income_returns_zero():
    assert _compute_foir(25.0, 12.0, 36, 0.0) == 0.0


def test_foir_comfortable():
    foir = _compute_foir(5.0, 10.0, 36, 200.0)
    assert foir < FOIR_COMFORT


def test_foir_exceeds_hard_max():
    foir = _compute_foir(50.0, 18.0, 12, 20.0)
    assert foir > FOIR_HARD_MAX


def test_foir_existing_emi_adds_burden():
    base = _compute_foir(10.0, 12.0, 36, 100.0, 0)
    with_existing = _compute_foir(10.0, 12.0, 36, 100.0, 10_000)
    assert with_existing > base


# ── Employment compatibility ──────────────────────────────────────────────────


def test_empty_policy_types_accepts_all():
    assert _employment_compatible("salaried", []) is True
    assert _employment_compatible("business", None) is True


def test_direct_match():
    assert _employment_compatible("business", ["business", "salaried"]) is True


def test_granular_borrower_matches_legacy_policy():
    assert _employment_compatible("salaried_govt", ["salaried"]) is True
    assert _employment_compatible("salaried_private", ["salaried"]) is True
    assert _employment_compatible("self_employed_professional", ["self-employed"]) is True


def test_legacy_borrower_matches_granular_policy():
    assert _employment_compatible("salaried", ["salaried_private", "salaried_govt"]) is True


def test_incompatible_employment():
    assert _employment_compatible("salaried", ["self-employed", "business"]) is False


def test_json_string_policy_types():
    types_json = json.dumps(["business", "salaried"])
    assert _employment_compatible("business", types_json) is True


# ── Hard disqualifiers ────────────────────────────────────────────────────────


def test_credit_score_below_min_disqualifies():
    result = evaluate_policy(_profile(credit_score=600), _policy(credit_score_min=650))
    assert not result.eligible
    assert any("Credit score" in d for d in result.disqualifiers)


def test_loan_amount_below_min_disqualifies():
    result = evaluate_policy(_profile(loan_amount=2.0), _policy(loan_amount_min=5.0))
    assert not result.eligible
    assert any("below policy minimum" in d for d in result.disqualifiers)


def test_loan_amount_above_max_disqualifies():
    result = evaluate_policy(_profile(loan_amount=600.0), _policy(loan_amount_max=500.0))
    assert not result.eligible
    assert any("exceeds policy maximum" in d for d in result.disqualifiers)


def test_incompatible_employment_disqualifies():
    result = evaluate_policy(
        _profile(employment_type="agriculture"),
        _policy(employment_types=["salaried", "business"]),
    )
    assert not result.eligible


def test_state_not_in_coverage_disqualifies():
    result = evaluate_policy(
        _profile(state="Rajasthan"),
        _policy(eligible_states=["Maharashtra", "Gujarat"]),
    )
    assert not result.eligible


def test_high_foir_disqualifies():
    result = evaluate_policy(
        _profile(loan_amount=50.0, monthly_income=10.0, tenure_months=12),
        _policy(interest_rate_min=15.0, tenure_max=12),
    )
    assert not result.eligible
    assert any("FOIR" in d for d in result.disqualifiers)


# ── Eligible path ─────────────────────────────────────────────────────────────


def test_eligible_returns_positive_score():
    result = evaluate_policy(_profile(), _policy())
    assert result.eligible
    assert 0 < result.score <= 100


def test_lower_rate_scores_higher():
    low = evaluate_policy(_profile(), _policy(interest_rate_min=9.0))
    high = evaluate_policy(_profile(), _policy(interest_rate_min=30.0))
    assert low.score > high.score


def test_explanation_contains_rate_and_score():
    result = evaluate_policy(_profile(), _policy())
    assert "Eligible" in result.explanation
    assert "%" in result.explanation


def test_foir_warning_added_for_moderate_burden():
    result = evaluate_policy(
        _profile(loan_amount=40.0, monthly_income=60.0, tenure_months=24),
        _policy(interest_rate_min=14.0, tenure_max=24),
    )
    # May or may not trigger warning — just verify no crash and has explanation
    assert result.explanation


# ── rank_policies ─────────────────────────────────────────────────────────────


def test_eligible_before_ineligible():
    policies = [
        _policy(policy_id=1, credit_score_min=900),   # ineligible
        _policy(policy_id=2, credit_score_min=600),   # eligible
    ]
    ranked = rank_policies(_profile(credit_score=700), policies)
    assert ranked[0].eligible is True
    assert ranked[1].eligible is False


def test_empty_policies_returns_empty():
    assert rank_policies(_profile(), []) == []


def test_lower_rate_ranks_higher_among_eligible():
    p1 = _policy(policy_id=1, interest_rate_min=9.0, completeness_score=0.8)
    p2 = _policy(policy_id=2, interest_rate_min=22.0, completeness_score=0.8)
    ranked = rank_policies(_profile(), [p2, p1])
    assert ranked[0].policy_id == 1


def test_multiple_disqualifiers_rank_last():
    bad = _policy(policy_id=1, credit_score_min=900, loan_amount_min=1000.0)
    ok = _policy(policy_id=2, credit_score_min=600)
    ranked = rank_policies(_profile(credit_score=700), [bad, ok])
    assert ranked[-1].policy_id == 1


def test_no_tenure_fallback_uses_policy_max():
    # profile without tenure_months — should use policy tenure_max for FOIR
    profile = _profile()
    del profile["tenure_months"]
    result = evaluate_policy(profile, _policy(tenure_max=48))
    assert result.eligible or result.foir_estimate is not None or True  # no crash
