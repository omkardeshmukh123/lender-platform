"""
pipeline/borrower_evaluator.py
================================
Borrower-side policy evaluation engine.

The SQL match_lenders() function handles hard eligibility filtering and
initial scoring at DB level.  This module runs a second pass in Python that:

  1. Computes borrower-specific FOIR  (not the policy consistency check —
     this is the actual repayment burden for THIS borrower on THIS request)
  2. Evaluates employment compatibility with granular sub-types
  3. Generates a scored ranking on 6 dimensions
  4. Produces human-readable decision explanations for every result
  5. Separates hard disqualifiers from soft warnings

Called from: backend/api/routers/loans.py after match_lenders() query
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing     import Any, Dict, List, Optional

# ── FOIR thresholds (borrower-facing) ─────────────────────────────────────────
# RBI/industry norms: salaried 50-55%, self-employed 40-50%, MSME 40-60%.
# Using 65% as the hard ceiling (conservative) — 80% was dangerously high.
# Total FOIR = (new_loan_EMI + existing_EMI_obligations) / monthly_income
FOIR_HARD_MAX   = 0.65   # > 65% total FOIR → ineligible
FOIR_WARN_HIGH  = 0.50   # 50–65% → warning (income strain)
FOIR_COMFORT    = 0.40   # < 40% → comfortable (score bonus)

# Employment type compatibility map:
#   granular type   →   legacy broad type it satisfies
_EMP_COMPAT: Dict[str, str] = {
    'salaried_govt':               'salaried',
    'salaried_private':            'salaried',
    'salaried_psu':                'salaried',
    'self_employed_professional':  'self-employed',
    'self_employed_non_professional': 'self-employed',
}


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class EvaluationResult:
    policy_id:         int
    lender_id:         int
    lender_name:       str
    product_name:      Optional[str]

    eligible:          bool
    score:             float          # 0–100 composite
    sql_score:         int            # score from match_lenders() for reference

    foir_estimate:     Optional[float] = None   # borrower's actual FOIR ratio

    disqualifiers:     List[str] = field(default_factory=list)
    match_reasons:     List[str] = field(default_factory=list)
    warnings:          List[str] = field(default_factory=list)
    explanation:       str       = ''

    # Pass-through fields for response
    interest_rate_min: Optional[float] = None
    interest_rate_max: Optional[float] = None
    loan_amount_min:   Optional[float] = None
    loan_amount_max:   Optional[float] = None
    tenure_min:        Optional[int]   = None
    tenure_max:        Optional[int]   = None
    credit_score_min:  Optional[int]   = None
    processing_fee:    Optional[float] = None
    collateral_required: Optional[bool] = None
    eligibility_notes: Optional[str]  = None
    website:           Optional[str]  = None
    kyc_pan_required:  Optional[bool] = None
    kyc_aadhaar_required: Optional[bool] = None
    kyc_gstin_required:   Optional[bool] = None
    kyc_itr_years:     Optional[int]  = None


# ── EMI helper ─────────────────────────────────────────────────────────────────

def _compute_emi(principal_lakhs: float, rate_pct_pa: float,
                 tenure_months: int) -> float:
    """Standard reducing-balance EMI formula.  Returns EMI in ₹."""
    P = principal_lakhs * 100_000
    if rate_pct_pa <= 0:
        return P / tenure_months
    r = rate_pct_pa / 100.0 / 12.0
    return P * r * (1 + r) ** tenure_months / ((1 + r) ** tenure_months - 1)


def _compute_foir(loan_amount_lakhs: float, rate_pct_pa: float,
                  tenure_months: int, monthly_income_k: float,
                  existing_emi_monthly: float = 0.0) -> float:
    """Total FOIR = (new_loan_EMI + existing obligations) / monthly_income.

    Args:
        loan_amount_lakhs:   Requested loan in ₹ lakhs
        rate_pct_pa:         Interest rate % per annum (best-case: policy min)
        tenure_months:       Repayment tenure in months
        monthly_income_k:    Monthly income in ₹ thousands (e.g. 50 = ₹50,000)
        existing_emi_monthly: Sum of borrower's existing EMI obligations in ₹
                              (home loan + car loan + CC dues + etc.)
    """
    if monthly_income_k <= 0 or tenure_months <= 0:
        return 0.0
    new_emi = _compute_emi(loan_amount_lakhs, rate_pct_pa, tenure_months)
    income  = monthly_income_k * 1_000
    return round((new_emi + existing_emi_monthly) / income, 3)


# ── Employment compatibility ───────────────────────────────────────────────────

def _employment_compatible(borrower_type: str, policy_types_raw: Any) -> bool:
    """
    Check if borrower's employment type is accepted by the policy.
    Handles both granular and legacy types on both sides.
    """
    if not policy_types_raw:
        return True   # no restriction = any type accepted

    if isinstance(policy_types_raw, str):
        try:
            policy_types = json.loads(policy_types_raw)
        except Exception:
            policy_types = [policy_types_raw]
    elif isinstance(policy_types_raw, (list, tuple)):
        policy_types = list(policy_types_raw)
    else:
        return True

    if not policy_types:
        return True

    b = borrower_type.lower().strip()
    policy_set = {t.lower().strip() for t in policy_types}

    # Direct match
    if b in policy_set:
        return True

    # Granular borrower → legacy policy
    legacy = _EMP_COMPAT.get(b)
    if legacy and legacy.lower() in policy_set:
        return True

    # Legacy borrower → granular policy (e.g. policy lists 'salaried_private')
    # A legacy 'salaried' borrower can use a policy listing any salaried_* type
    reverses = {v: k for k, v in _EMP_COMPAT.items()}
    if b in reverses:
        # b is a legacy type — check if any granular variant is in policy
        granular = [g for g, leg in _EMP_COMPAT.items() if leg == b]
        if any(g in policy_set for g in granular):
            return True

    return False


# ── Core evaluation ────────────────────────────────────────────────────────────

def evaluate_policy(profile: Dict[str, Any],
                    policy:  Dict[str, Any]) -> EvaluationResult:
    """
    Full borrower-side evaluation for one policy.

    profile keys (all Optional except loan_amount, loan_type):
        loan_amount, credit_score, employment_type, monthly_income,
        age, state, business_vintage, loan_type, tenure_months

    policy keys: output of match_lenders() enriched with extended fields
        (min_monthly_income, min_age, max_age, eligible_states,
         source_confidence, kyc_*, employment_types, etc.)
    """
    result = EvaluationResult(
        policy_id    = policy['policy_id'],
        lender_id    = policy['lender_id'],
        lender_name  = policy.get('company_name', ''),
        product_name = policy.get('product_name'),
        eligible     = True,
        score        = float(policy.get('match_score', 0)),
        sql_score    = int(policy.get('match_score', 0)),
        interest_rate_min  = policy.get('interest_rate_min'),
        interest_rate_max  = policy.get('interest_rate_max'),
        loan_amount_min    = policy.get('loan_amount_min'),
        loan_amount_max    = policy.get('loan_amount_max'),
        tenure_min         = policy.get('tenure_min'),
        tenure_max         = policy.get('tenure_max'),
        credit_score_min   = policy.get('credit_score_min'),
        processing_fee     = policy.get('processing_fee'),
        collateral_required = policy.get('collateral_required'),
        eligibility_notes  = policy.get('eligibility_notes'),
        website            = policy.get('website'),
        kyc_pan_required   = policy.get('kyc_pan_required'),
        kyc_aadhaar_required = policy.get('kyc_aadhaar_required'),
        kyc_gstin_required   = policy.get('kyc_gstin_required'),
        kyc_itr_years        = policy.get('kyc_itr_years'),
    )

    loan_amount      = float(profile.get('loan_amount', 0))
    credit_score     = profile.get('credit_score')
    emp_type         = profile.get('employment_type', '')
    income           = profile.get('monthly_income')       # ₹ thousands
    existing_emi     = float(profile.get('existing_emi_monthly', 0) or 0)  # ₹ absolute
    age              = profile.get('age')
    state            = profile.get('state')
    bvintage         = profile.get('business_vintage')
    req_tenure       = profile.get('tenure_months')        # requested tenure (optional)

    # ── Hard eligibility checks ────────────────────────────────────────────
    # These override the SQL score — if any fails, policy is disqualified.

    # 1. Credit score
    cs_min = policy.get('credit_score_min')
    if credit_score is not None and cs_min is not None:
        if credit_score < cs_min:
            result.disqualifiers.append(
                f'Credit score {credit_score} below minimum {cs_min} '
                f'(need {cs_min - credit_score} more points)'
            )
        else:
            margin = credit_score - cs_min
            result.match_reasons.append(
                f'Credit score {credit_score} ✓ (exceeds minimum {cs_min} by {margin})'
            )

    # 2. Employment type compatibility (granular-aware)
    if not _employment_compatible(emp_type, policy.get('employment_types')):
        policy_types = policy.get('employment_types', [])
        if isinstance(policy_types, str):
            try:
                policy_types = json.loads(policy_types)
            except Exception:
                policy_types = [policy_types]
        result.disqualifiers.append(
            f'Employment type "{emp_type}" not accepted '
            f'(policy accepts: {", ".join(policy_types) or "unknown"})'
        )
    else:
        result.match_reasons.append(f'Employment type "{emp_type}" accepted')

    # 3. Loan amount range
    la_min = policy.get('loan_amount_min')
    la_max = policy.get('loan_amount_max')
    if la_min is not None and loan_amount < la_min:
        result.disqualifiers.append(
            f'Requested ₹{loan_amount}L below policy minimum ₹{la_min}L'
        )
    elif la_max is not None and loan_amount > la_max:
        result.disqualifiers.append(
            f'Requested ₹{loan_amount}L exceeds policy maximum ₹{la_max}L'
        )
    else:
        result.match_reasons.append(f'Loan amount ₹{loan_amount}L within range')

    # 4. Age
    min_age = policy.get('min_age')
    max_age = policy.get('max_age')
    if age is not None:
        if min_age is not None and age < min_age:
            result.disqualifiers.append(f'Age {age} below minimum {min_age}')
        elif max_age is not None and age > max_age:
            result.disqualifiers.append(f'Age {age} exceeds maximum {max_age}')

    # 5. State eligibility
    eligible_states = policy.get('eligible_states')
    if isinstance(eligible_states, str):
        try:
            eligible_states = json.loads(eligible_states)
        except Exception:
            eligible_states = None
    if state and eligible_states:
        if state not in eligible_states:
            result.disqualifiers.append(
                f'State "{state}" not in policy coverage area '
                f'({len(eligible_states)} states covered)'
            )
        else:
            result.match_reasons.append(f'Available in {state}')

    # 6. Business vintage
    min_vintage = policy.get('min_business_vintage')
    if bvintage is not None and min_vintage is not None and bvintage < min_vintage:
        result.disqualifiers.append(
            f'Business vintage {bvintage} years below minimum {min_vintage} years'
        )

    # 7. FOIR — total repayment burden (new loan + existing obligations)
    # Use requested tenure if provided, else policy max tenure (borrower's best case)
    foir_tenure = req_tenure or policy.get('tenure_max')
    ir_for_foir = policy.get('interest_rate_min')
    if income and loan_amount and foir_tenure and ir_for_foir is not None:
        foir = _compute_foir(loan_amount, ir_for_foir, foir_tenure, income, existing_emi)
        result.foir_estimate = foir
        new_emi_val = _compute_emi(loan_amount, ir_for_foir, foir_tenure)
        if foir > FOIR_HARD_MAX:
            existing_note = (
                f' (includes ₹{existing_emi:,.0f}/mo existing obligations)'
                if existing_emi > 0 else ''
            )
            result.disqualifiers.append(
                f'Total FOIR {foir:.0%} exceeds 65% limit{existing_note} — '
                f'repayment burden too high at {ir_for_foir}% for '
                f'{foir_tenure} months on ₹{loan_amount}L'
            )
        elif foir > FOIR_WARN_HIGH:
            result.warnings.append(
                f'High total FOIR {foir:.0%} — EMI ≈ ₹{new_emi_val:,.0f}/mo '
                f'will be a significant income commitment'
            )
        else:
            result.match_reasons.append(
                f'Comfortable repayment: total FOIR {foir:.0%} '
                f'(new EMI ≈ ₹{new_emi_val:,.0f}/mo)'
            )

    # ── Determine eligibility ──────────────────────────────────────────────
    result.eligible = len(result.disqualifiers) == 0

    # ── Compute composite score (only for eligible) ────────────────────────
    if result.eligible:
        result.score = _composite_score(profile, policy, result)

    # ── Soft warnings (non-blocking) ──────────────────────────────────────
    _add_warnings(policy, result)

    # ── Explanation ───────────────────────────────────────────────────────
    result.explanation = _build_explanation(result, policy)

    return result


def _composite_score(profile: Dict, policy: Dict,
                     result: EvaluationResult) -> float:
    """
    6-dimension score for eligible policies.

    Dimension          Weight  Signal
    Rate competitiveness  30   Lower rate = higher score
    Credit margin         20   How far above minimum (headroom)
    Amount fit            15   How well request fits in range
    FOIR comfort          15   Lower FOIR = better
    Data quality          10   Completeness + source confidence
    Processing fee        10   Lower fee = higher score
    """
    score = 0.0

    ir_min = policy.get('interest_rate_min')
    ir_max = policy.get('interest_rate_max')

    # 1. Rate competitiveness (30 pts)
    if ir_min is not None:
        # Normalise: 8% or below = 30pts, 36% or above = 0pts
        rate_score = max(0.0, min(30.0, (36.0 - ir_min) / (36.0 - 8.0) * 30.0))
        score += rate_score
    else:
        score += 15.0   # no rate info = neutral

    # 2. Credit margin (20 pts)
    cs_min = policy.get('credit_score_min')
    borrower_cs = profile.get('credit_score')
    if borrower_cs is not None and cs_min is not None:
        margin = borrower_cs - cs_min
        # 0 margin = 5 pts, 50+ pts margin = 20 pts
        credit_score = min(20.0, 5.0 + margin * 0.3)
        score += credit_score
    else:
        score += 10.0   # no requirement = neutral

    # 3. Amount fit (15 pts)
    la_min = policy.get('loan_amount_min')
    la_max = policy.get('loan_amount_max')
    req    = float(profile.get('loan_amount', 0))
    if la_min is not None and la_max is not None and la_max > la_min:
        mid    = (la_min + la_max) / 2.0
        span   = la_max - la_min
        # Full points if request is within middle 50% of range
        dist   = abs(req - mid) / span
        score += max(0.0, 15.0 * (1.0 - dist))
    else:
        score += 8.0

    # 4. FOIR comfort (15 pts — lower FOIR = more comfort)
    if result.foir_estimate is not None:
        if result.foir_estimate <= FOIR_COMFORT:
            score += 15.0
        elif result.foir_estimate <= FOIR_WARN_HIGH:
            # Linear between comfort and warn
            ratio  = (result.foir_estimate - FOIR_COMFORT) / (FOIR_WARN_HIGH - FOIR_COMFORT)
            score += 15.0 * (1.0 - ratio)
        # foir > FOIR_WARN_HIGH but ≤ FOIR_HARD_MAX: 0 pts (but still eligible)
    else:
        score += 8.0   # no income data = neutral

    # 5. Data quality (10 pts)
    completeness  = float(policy.get('completeness_score') or 0.5)
    confidence    = {'high': 1.0, 'medium': 0.6, 'low': 0.3}.get(
                        policy.get('source_confidence', 'low'), 0.3)
    score += 10.0 * (completeness * 0.6 + confidence * 0.4)

    # 6. Processing fee (10 pts — lower = better)
    fee = policy.get('processing_fee')
    if fee is not None:
        # 0% = 10pts, 3%+ = 0pts
        score += max(0.0, 10.0 * (1.0 - fee / 3.0))
    else:
        score += 5.0

    return round(min(100.0, score), 1)


def _add_warnings(policy: Dict, result: EvaluationResult) -> None:
    """Add non-blocking warnings that are useful for the borrower to know."""

    # Collateral required
    if policy.get('collateral_required') is True:
        result.warnings.append('Collateral required — check assets before applying')

    # High review_priority anomalies
    anomaly_flags = policy.get('anomaly_flags') or []
    if isinstance(anomaly_flags, str):
        try:
            anomaly_flags = json.loads(anomaly_flags)
        except Exception:
            anomaly_flags = []
    for flag in anomaly_flags:
        if flag.startswith('foir_high'):
            result.warnings.append('Policy has high FOIR — verify income documentation')
        elif flag.startswith('rate_outside_norm'):
            result.warnings.append('Interest rate is outside typical market range for this product')
        elif flag.startswith('wide_rate_band'):
            result.warnings.append('Lender shows wide rate range — actual rate may vary by profile')

    # KYC requirements summary
    kyc_items = []
    if policy.get('kyc_pan_required'):      kyc_items.append('PAN')
    if policy.get('kyc_aadhaar_required'):  kyc_items.append('Aadhaar')
    if policy.get('kyc_gstin_required'):    kyc_items.append('GSTIN')
    itr = policy.get('kyc_itr_years')
    if itr:  kyc_items.append(f'ITR ({itr} years)')
    if kyc_items:
        result.warnings.append(f'Required documents: {", ".join(kyc_items)}')


def _build_explanation(result: EvaluationResult, policy: Dict) -> str:
    """One-sentence human-readable decision explanation."""
    if not result.eligible:
        primary = result.disqualifiers[0] if result.disqualifiers else 'does not meet eligibility criteria'
        return f'Not eligible — {primary}'

    ir_min = policy.get('interest_rate_min')
    ir_max = policy.get('interest_rate_max')
    rate_str = (f'{ir_min}%' if ir_min == ir_max
                else f'{ir_min}–{ir_max}%') if ir_min is not None else 'rate not disclosed'

    foir_str = (f', FOIR {result.foir_estimate:.0%}'
                if result.foir_estimate is not None else '')

    warnings_count = len(result.warnings)
    warn_str = (f' — {warnings_count} note{"s" if warnings_count > 1 else ""} to review'
                if warnings_count else '')

    return (f'Eligible — {rate_str} p.a., score {result.score:.0f}/100'
            f'{foir_str}{warn_str}')


# ── Ranking ────────────────────────────────────────────────────────────────────

def rank_policies(profile: Dict[str, Any],
                  policies: List[Dict[str, Any]]) -> List[EvaluationResult]:
    """
    Evaluate and rank all policies for a borrower profile.

    Eligible policies come first, sorted by:
      1. composite score DESC
      2. interest_rate_min ASC (lower rate preferred when scores equal)

    Ineligible policies follow (sorted by how many disqualifiers — fewest first,
    so "almost eligible" appear near top for the borrower to see what they need).
    """
    evaluated = [evaluate_policy(profile, p) for p in policies]

    eligible   = sorted(
        [r for r in evaluated if r.eligible],
        key=lambda r: (-r.score, r.interest_rate_min or 999)
    )
    ineligible = sorted(
        [r for r in evaluated if not r.eligible],
        key=lambda r: len(r.disqualifiers)
    )

    return eligible + ineligible
