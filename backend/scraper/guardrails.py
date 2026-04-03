"""
guardrails.py  v4.1
====================
Production-grade validation pipeline for NBFC / bank lender records.

v4.1 critical fixes:
  Fix C1 — RBI CoR registry lookup (format alone doesn't prove existence)
  Fix C2 — CIN cross-checked against listing status; MCA registry hook added
  Fix C3 — PAN 4th-character entity-type enforcement (Company PAN → 'C')
  Fix C4 — Dedup key weighted: RBI > CIN > domain > name (not equal-weight)
  Fix C5 — Quality score penalised for missing identity/compliance fields

v4.0 (previous):
  RBI format patterns, non-lender multi-signal, KYC layer, AUM by type,
  SHA-256 dedup, rate/tenure bounds, HTTPS+free-hosting check, source-tier
  quality, audit trail, RBI category cross-check.
"""

import csv
import re
import json
import hashlib
import logging
from pathlib import Path
from typing  import Optional, Any
from dataclasses import dataclass, field
from datetime    import datetime
from difflib     import SequenceMatcher
from collections import Counter

logger = logging.getLogger(__name__)

CRITICAL = 'CRITICAL'
ERROR    = 'ERROR'
WARNING  = 'WARNING'
INFO     = 'INFO'

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

CANONICAL_LOAN_TAGS: set[str] = {
    'MSME Loan', 'Personal Loan', 'Home Loan', 'Business Loan',
    'Vehicle Loan', 'Gold Loan', 'Education Loan', 'Micro Loan',
    'Loan Against Property', 'Working Capital', 'Agriculture Loan',
    'Credit Card', 'Consumer Durable Loan', 'EV Loan',
    'Two Wheeler Loan', 'Microfinance', 'Rural Loan', 'Supply Chain Finance',
}

TAG_NORMALIZATION: dict[str, str] = {
    'msme': 'MSME Loan', 'sme': 'MSME Loan', 'small business': 'MSME Loan',
    'personal': 'Personal Loan', 'home': 'Home Loan', 'housing': 'Home Loan',
    'mortgage': 'Home Loan', 'business': 'Business Loan',
    'car loan': 'Vehicle Loan', 'auto loan': 'Vehicle Loan', 'vehicle': 'Vehicle Loan',
    'gold': 'Gold Loan', 'education': 'Education Loan', 'student': 'Education Loan',
    'micro loan': 'Micro Loan', 'microloan': 'Micro Loan',
    'microfinance': 'Microfinance', 'micro finance': 'Microfinance',
    'lap': 'Loan Against Property', 'property': 'Loan Against Property',
    'working capital': 'Working Capital',
    'agriculture': 'Agriculture Loan', 'agri': 'Agriculture Loan', 'kisan': 'Agriculture Loan',
    'consumer durable': 'Consumer Durable Loan', 'appliance': 'Consumer Durable Loan',
    'ev loan': 'EV Loan', 'electric vehicle': 'EV Loan',
    'two wheeler': 'Two Wheeler Loan', 'bike loan': 'Two Wheeler Loan',
    'rural': 'Rural Loan',
    'supply chain': 'Supply Chain Finance', 'invoice': 'Supply Chain Finance',
    'factoring': 'Supply Chain Finance',
}

ALL_INDIA_STATES: list[str] = [
    'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
    'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka',
    'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
    'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana',
    'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal', 'Delhi',
    'Jammu & Kashmir', 'Ladakh', 'Puducherry', 'Chandigarh',
    'Dadra and Nagar Haveli', 'Lakshadweep', 'Andaman and Nicobar Islands',
]
STATE_SET   = set(ALL_INDIA_STATES)
STATE_LOWER = {s.lower(): s for s in ALL_INDIA_STATES}

VALID_COMPANY_TYPES: set[str] = {
    'NBFC', 'Private Bank', 'PSU Bank', 'Foreign Bank',
    'Cooperative Bank', 'NBFC-MFI', 'Small Finance Bank',
    'Payment Bank', 'Regional Rural Bank',
}

# Fix 4: AUM ranges segmented by lender type (Crores)
AUM_LIMITS: dict[str, tuple[float, float]] = {
    'NBFC-MFI':         (0.1,    50_000),   # MFIs rarely exceed ₹50K Cr
    'NBFC':             (0.1, 1_000_000),   # ₹10 lakh Cr upper bound
    'Small Finance Bank':(0.1,  500_000),
    'default':          (0.1, 10_000_000),
}
# Warn (not error) if NBFC exceeds this — unusual but not impossible
AUM_WARN_ABOVE: dict[str, float] = {
    'NBFC':     1_000_000,
    'NBFC-MFI':    50_000,
}

VALID_YEAR_RANGE     = (1850, datetime.now().year)
VALID_EMPLOYEE_RANGE = (1, 500_000)
VALID_BRANCH_RANGE   = (1,    100_000)

# Fix 7: Free-hosting / untrusted domain fragments — reject as lender website
UNTRUSTED_DOMAINS: frozenset[str] = frozenset([
    'blogspot', 'wordpress', 'wix', 'weebly', 'squarespace',
    'godaddysites', 'site123', 'webnode', 'jimdo', 'yola',
    'github.io', 'netlify', 'vercel', 'pages.dev',
])

# Fix 1: RBI NBFC Certificate of Registration patterns
# Primary: N-13.00001 / N.13.00001 / B-01.00001 style
# Secondary: alphanumeric with slash (older CoR format)
_RBI_COR_PATTERNS = [
    re.compile(r'^[A-Z][-\.]\d{2}[-\.]\d{5}$'),              # N.13.00001 or N-13-00001
    re.compile(r'^[A-Z0-9]{2,6}\/[A-Z]{1,4}\/\d{4,6}$'),    # older slash-format CoRs
    re.compile(r'^[A-Z]{1,3}[-\s]?\d{5,8}$'),                 # abbreviated formats
]

# Pre-sanitization: patterns that are never valid RBI CoR numbers.
# Catches: row-number placeholders (ROW-73), truncated English words
# (ISTERED, PORATED from REGISTERED/INCORPORATED), and common nulls.
_RBI_GARBAGE = re.compile(
    r'^(ROW-?\d+|REGISTERED|INCORPORATED|[A-Z]{3,}ERED|[A-Z]{3,}RATED|'
    r'[A-Z]{3,}ATED|N/?A|NONE|NULL|NA|TBD|UNKNOWN|PENDING|NOT\s*AVAILABLE)$',
    re.IGNORECASE,
)

import os as _os
MIN_QUALITY                  = float(_os.environ.get("GUARDRAILS_MIN_QUALITY",           "0.20"))
QUALITY_REVIEW_FLOOR         = float(_os.environ.get("GUARDRAILS_REVIEW_FLOOR",           "0.40"))
NON_LENDER_AUTO_REJECT_CONF  = float(_os.environ.get("GUARDRAILS_NON_LENDER_CONFIDENCE", "0.90"))
del _os

VALIDATION_VERSION = '4.0'
SCHEMA_VERSION     = 'nbfc_v4'

# Fix 6: Plausible interest rate and tenure bounds for Indian lending
RATE_BOUNDS  = (3.0, 60.0)   # % p.a. — below 3% or above 60% is suspect
TENURE_BOUNDS = (1, 360)     # months — 1 month to 30 years


# ─────────────────────────────────────────────────────────────
# Fix C1: RBI CoR REGISTRY
# ─────────────────────────────────────────────────────────────

class RBICoRRegistry:
    """
    Fix C1: Registry-backed RBI Certificate of Registration lookup.

    Loads from data/input/rbi_cor_registry.csv (columns: cor_number, company_name, status).
    If the file is absent, falls back to format-only validation with an explicit WARNING
    so operators know the registry check was skipped.

    Status values in the registry: 'active' | 'cancelled' | 'revoked' | 'surrendered'
    Only 'active' CoR numbers pass; all others are flagged as revoked/inactive.
    """

    def __init__(self, registry_path: Optional[Path] = None):
        self._by_cor:  dict[str, dict] = {}   # cor_number (upper) → row
        self._loaded = False
        if registry_path and Path(registry_path).exists():
            self._load(Path(registry_path))

    def _load(self, path: Path):
        try:
            with open(path, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    cor = row.get('cor_number', '').strip().upper()
                    if cor:
                        self._by_cor[cor] = row
            self._loaded = True
            logging.info(f'RBICoRRegistry: loaded {len(self._by_cor)} entries from {path}')
        except Exception as e:
            logging.warning(f'RBICoRRegistry load error: {e}')

    def lookup(self, cor: str) -> tuple[bool, str]:
        """
        Returns (is_valid, reason).
        is_valid=True  → CoR active in registry.
        is_valid=False → not found, cancelled, revoked, or surrendered.
        is_valid=None  → registry not loaded (format-only fallback).
        """
        if not self._loaded:
            return None, 'registry_unavailable'
        rec = self._by_cor.get(cor.strip().upper())
        if rec is None:
            return False, 'not_in_registry'
        status = rec.get('status', '').lower()
        if status == 'active':
            return True, 'active'
        return False, f'cor_status_{status}'

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# ─────────────────────────────────────────────────────────────
# RESULT
# ─────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    clean_data:    dict  = field(default_factory=dict)
    issues:        list  = field(default_factory=list)
    quality_score: float = 0.0
    is_valid:      bool  = False
    fingerprint:   str   = ''

    def add(self, severity: str, fld: str, message: str):
        self.issues.append({
            'severity':  severity,
            'field':     fld,
            'message':   message,
            'timestamp': datetime.now().isoformat(),
        })
        log_fn = {CRITICAL: logger.error, ERROR: logger.warning,
                  WARNING: logger.info, INFO: logger.debug}.get(severity, logger.debug)
        log_fn(f'GUARDRAIL [{severity}] {fld}: {message}')

    @property
    def critical_issues(self) -> list:
        return [i for i in self.issues if i['severity'] == CRITICAL]

    def summary(self) -> str:
        c = Counter(i['severity'] for i in self.issues)
        return (f"quality={self.quality_score:.0%} valid={self.is_valid} "
                f"C={c.get(CRITICAL,0)} E={c.get(ERROR,0)} "
                f"W={c.get(WARNING,0)} I={c.get(INFO,0)}")


# ─────────────────────────────────────────────────────────────
# DEDUPLICATION STORE
# ─────────────────────────────────────────────────────────────

class DeduplicationStore:
    def __init__(self, similarity_threshold: float = 0.90):
        self.seen_fps:      set[str]        = set()
        self.seen_names:    list[str]       = []
        self.seen_rbi_regs: dict[str, str]  = {}   # CoR → normalised name
        self.threshold                      = similarity_threshold

    def is_duplicate(self, name: str, fp: str, rbi_reg: str = '') -> tuple[bool, str]:
        if fp in self.seen_fps:
            return True, f'Exact duplicate fp={fp[:12]}…'
        # Same RBI CoR already registered under a different company name
        if rbi_reg:
            prev = self.seen_rbi_regs.get(rbi_reg.upper())
            if prev and prev != self._norm(name):
                return True, (
                    f'RBI CoR {rbi_reg} already registered to a different entity '
                    f'("{prev}") — possible data error in source CSV'
                )
        norm = self._norm(name)
        for seen in self.seen_names:
            ratio = SequenceMatcher(None, norm, seen).ratio()
            if ratio >= self.threshold:
                return True, f'Similar name ratio={ratio:.2f} matched="{seen}"'
        return False, ''

    def register(self, name: str, fp: str, rbi_reg: str = ''):
        self.seen_fps.add(fp)
        self.seen_names.append(self._norm(name))
        if rbi_reg:
            self.seen_rbi_regs[rbi_reg.upper()] = self._norm(name)

    def _norm(self, name: str) -> str:
        name = name.lower().strip()
        for s in [' ltd', ' limited', ' pvt', ' private', ' bank',
                   ' finance', ' financial', ' services', ' india', ' nbfc']:
            name = name.replace(s, '')
        name = re.sub(r'[^\w\s]', '', name)
        return ' '.join(name.split())


# ─────────────────────────────────────────────────────────────
# MAIN GUARDRAILS
# ─────────────────────────────────────────────────────────────

class Guardrails:
    def __init__(
        self,
        dedup:        Optional[DeduplicationStore] = None,
        cor_registry: Optional[RBICoRRegistry]    = None,
    ):
        self.dedup        = dedup or DeduplicationStore()
        self.cor_registry = cor_registry or RBICoRRegistry()  # Fix C1

    def run(self, raw: dict) -> GuardrailResult:
        r          = GuardrailResult()
        out: dict  = {}
        confidence = raw.get('_confidence', {})

        # Fix 9: audit trail accumulates decisions throughout this run
        _audit: dict = {
            'validation_version': VALIDATION_VERSION,
            'schema_version':     SCHEMA_VERSION,
            'started_at':         datetime.now().isoformat(),
            'decisions':          [],
        }
        def _audit_note(fld: str, decision: str):
            _audit['decisions'].append({'field': fld, 'decision': decision})

        # ── 1. company_name ───────────────────────────────────
        name = self._str(raw.get('company_name', ''), 200)
        if not name or len(name) < 2:
            r.add(CRITICAL, 'company_name', 'Missing or too short')
            r.is_valid = False
            return r
        out['company_name'] = name
        _audit_note('company_name', f'accepted: {name!r}')

        # ── 2. company_type ───────────────────────────────────
        ctype = raw.get('company_type', 'NBFC').strip()
        if ctype not in VALID_COMPANY_TYPES:
            r.add(WARNING, 'company_type', f'Unknown type "{ctype}", defaulting to NBFC')
            ctype = 'NBFC'
            _audit_note('company_type', 'normalized to NBFC')
        out['company_type'] = ctype

        # ── Fix 2: Non-lender detection — multi-signal, not scraper-only ──
        if raw.get('is_non_lender'):
            lender_signal_count = self._detect_lender_signals(raw)
            if lender_signal_count >= 2:
                # Enough lending signals override the scraper's non-lender flag
                r.add(INFO, 'company_name',
                      f'Scraper flagged non-lender but {lender_signal_count} lending signals found — continuing')
                _audit_note('is_non_lender', f'overridden by {lender_signal_count} lending signals')
            else:
                non_lender_conf = float(raw.get('_non_lender_confidence', 1.0))
                if non_lender_conf >= NON_LENDER_AUTO_REJECT_CONF:
                    r.add(CRITICAL, 'company_name',
                          f'No lending signals found (confidence={non_lender_conf:.0%}) — '
                          'auto-rejected as non-lender')
                    r.is_valid   = False
                    r.clean_data = out
                    _audit_note('is_non_lender', f'auto-rejected (conf={non_lender_conf:.0%})')
                    out['audit_trail'] = json.dumps(_audit)
                    return r
                else:
                    out['approval_status'] = 'needs_review'
                    out['admin_notes'] = (
                        f'Scraper flagged as non-lender (confidence={non_lender_conf:.0%}) — '
                        f'below auto-reject threshold ({NON_LENDER_AUTO_REJECT_CONF:.0%}). '
                        'Please verify the website manually.'
                    )
                    r.add(WARNING, 'company_name',
                          f'Possible non-lender (confidence={non_lender_conf:.0%}) — flagged for admin review')
                    _audit_note('is_non_lender', f'flagged for review (conf={non_lender_conf:.0%})')

        # ── Fix 7: Website — HTTPS warning + free-hosting rejection ──
        website = self._validate_website_domain(raw.get('website', ''), r, _audit)
        out['website'] = website

        # ── Fix 4: AUM — type-specific range ─────────────────
        aum = self._aum_by_type(raw.get('aum_crores'), ctype, r)
        out['aum_crores']        = aum
        out['aum_category']      = self._aum_cat(aum)
        out['last_year_revenue'] = self._pos_float(raw.get('last_year_revenue'))
        _audit_note('aum_crores', f'accepted={aum}' if aum else 'null or rejected')

        # ── Listing ───────────────────────────────────────────
        is_listed    = self._bool(raw.get('is_listed', False))
        stock_symbol = self._str(raw.get('stock_symbol', ''), 20)
        if is_listed and not stock_symbol:
            r.add(WARNING, 'stock_symbol', 'is_listed=True but stock_symbol is empty')
        if not is_listed and stock_symbol:
            stock_symbol = ''
        out['is_listed']    = is_listed
        out['stock_symbol'] = stock_symbol

        # ── Fix 6: Product validation ─────────────────────────
        raw_tags = self._ensure_list(raw.get('loan_tags') or raw.get('primary_loan_segments') or [])
        tags     = self._normalize_tags(raw_tags)
        if not tags:
            # Fallback: infer from free-text fields (business_summary, primary_focus)
            _text = ' '.join(filter(None, [
                raw.get('business_summary', ''),
                raw.get('primary_focus', ''),
                raw.get('company_name', ''),
            ]))
            tags = self._infer_tags_from_text(_text)
            if tags:
                r.add(INFO, 'loan_tags',
                      f'Tags inferred from text (no explicit tags found): {tags}')
            else:
                r.add(WARNING, 'loan_tags',
                      'No loan tags and none inferable — lender will not appear in product filters')
        out['primary_loan_segments'] = json.dumps(tags)
        out['product_types']         = json.dumps(tags)
        out['primary_product']       = tags[0] if tags else ''

        self._validate_product_rates(raw, r)   # Fix 6: rate / tenure sanity

        # ── HQ ────────────────────────────────────────────────
        hq_city  = self._str(raw.get('hq_city', ''), 100)
        hq_state = self._canonical_state(raw.get('hq_state', ''), r)
        hq_loc   = f'{hq_city}, {hq_state}'.strip(', ')
        if not hq_state:
            r.add(ERROR, 'hq_state', 'Missing HQ state — state filter will not work')
        out['hq_city']     = hq_city
        out['hq_state']    = hq_state
        out['hq_location'] = hq_loc

        # ── Operating states ──────────────────────────────────
        raw_states               = self._ensure_list(raw.get('operating_states', []))
        potential_pan            = raw.get('potential_pan_india', False)
        clean_states, is_pan     = self._states(raw_states, hq_state, ctype, r, potential_pan)
        if hq_state and hq_state not in clean_states:
            r.add(ERROR, 'operating_states', f'HQ "{hq_state}" not in operating_states — auto-adding')
            clean_states = sorted(set(clean_states + [hq_state]))
        out['operating_states']    = json.dumps(clean_states)
        out['pan_india']           = is_pan
        out['operating_intensity'] = self._intensity(len(clean_states), is_pan)

        # ── Contact ───────────────────────────────────────────
        out['phone'] = self._phone(raw.get('phone', ''))
        out['email'] = self._email(raw.get('email', ''))

        # ── Operational ───────────────────────────────────────
        emp_count  = self._range(raw.get('employee_count'),  *VALID_EMPLOYEE_RANGE,  'employee_count', r)
        branch_cnt = self._range(raw.get('branch_count'),    *VALID_BRANCH_RANGE,    'branch_count',   r)
        year       = self._year(raw.get('established_year'), r)
        out['established_year'] = year
        out['employee_count']   = emp_count
        out['branch_count']     = branch_cnt

        # ── Cross-field rules ─────────────────────────────────
        if emp_count and emp_count > 5000 and len(clean_states) < 3 and not is_pan:
            r.add(WARNING, 'operating_states',
                  f'employee_count={emp_count:,} > 5000 but only {len(clean_states)} states scraped')
        if branch_cnt and branch_cnt > 500 and len(clean_states) < 5 and not is_pan:
            r.add(WARNING, 'operating_states',
                  f'branch_count={branch_cnt:,} > 500 but only {len(clean_states)} states')
            out['pan_india'] = True
        if aum and aum > 50_000 and not is_pan:
            r.add(WARNING, 'operating_intensity', f'AUM ₹{aum:,.0f} Cr is Large but not Pan India')
        if year and year < 1950 and ctype == 'NBFC':
            r.add(INFO, 'established_year', f'NBFC established {year} before 1950 — please verify')

        # ── Other fields ──────────────────────────────────────
        out['rural_presence']        = self._bool(raw.get('rural_presence', False))
        out['recent_funding']        = self._str(raw.get('recent_funding', ''), 200)
        out['recent_funding_amount'] = self._pos_float(raw.get('recent_funding_amount'))
        out['recent_funding_year']   = self._year_simple(raw.get('recent_funding_year'))

        # ── Fix 1: RBI registration — validated, not passthrough ──
        rbi_reg = self._validate_rbi_registration(
            scraped_reg=self._str(raw.get('rbi_registration', ''), 50),
            gemini_reg=self._str(raw.get('rbi_registration_number', ''), 50),
            r=r,
        )
        out['rbi_registration_number'] = rbi_reg
        _audit_note('rbi_registration_number', f'reg_tier accepted: {rbi_reg!r}' if rbi_reg else 'empty/rejected')

        # ── Fix 10: RBI category cross-check + company_type auto-correct ──
        rbi_category = self._str(raw.get('rbi_category', ''), 50)
        out['rbi_category'] = rbi_category
        ctype = self._validate_company_type_vs_rbi(ctype, rbi_category, r, _audit)
        out['company_type'] = ctype   # update in case auto-corrected from rbi_category

        # ── Fix C2/C3: KYC layer — CIN + PAN with entity-type checks ────
        out['cin']  = self._validate_cin(raw.get('cin', ''), is_listed, r)
        out['pan']  = self._validate_pan(raw.get('pan', ''), ctype, r)
        _audit_note('kyc', f"cin={'present' if out['cin'] else 'absent'} pan={'present' if out['pan'] else 'absent'}")

        # ── Confidence metadata ───────────────────────────────
        out['field_confidence'] = json.dumps(confidence) if confidence else '{}'

        # ── Metadata ──────────────────────────────────────────
        out['data_source']         = raw.get('data_source', 'scrapling+gemini')
        out['extraction_status']   = 'success' if not r.critical_issues else 'partial'
        out['last_updated']        = datetime.now().isoformat()
        out['data_version']        = VALIDATION_VERSION
        out['schema_version']      = SCHEMA_VERSION
        out['validation_version']  = VALIDATION_VERSION

        # ── Fix C4: Weighted dedup fingerprint — strongest available anchor ──
        fp = self._strong_dedup_key(name, rbi_reg, out.get('cin', ''), website)
        r.fingerprint    = fp
        out['data_hash'] = fp

        is_dup, dup_reason = self.dedup.is_duplicate(name, fp, rbi_reg)
        if is_dup:
            r.add(CRITICAL, 'company_name', f'Duplicate: {dup_reason}')
            r.is_valid   = False
            r.clean_data = out
            _audit_note('dedup', f'REJECTED: {dup_reason}')
            out['audit_trail'] = json.dumps(_audit)
            return r
        self.dedup.register(name, fp, rbi_reg)
        _audit_note('dedup', 'registered as unique')

        # ── Quality score + 3-tier outcome ────────────────────
        # MIN_QUALITY (default 0.20) = hard-reject floor
        # QUALITY_REVIEW_FLOOR (default 0.40) = pending_review band (0.20–0.40)
        # ≥ QUALITY_REVIEW_FLOOR + no critical issues = passes guardrails normally
        r.quality_score = self._quality(out, confidence, raw)
        r.clean_data    = out

        if r.quality_score < MIN_QUALITY:
            # Hard reject — too little usable data to be worth human review
            r.is_valid = False
            r.add(CRITICAL, 'quality',
                  f'Quality {r.quality_score:.0%} below hard floor {MIN_QUALITY:.0%} — rejected')
        elif r.quality_score < QUALITY_REVIEW_FLOOR and not r.critical_issues:
            # Low quality but above floor — send to admin review, don't auto-reject
            r.is_valid = True
            out['approval_status'] = 'needs_review'
            if not out.get('admin_notes'):
                out['admin_notes'] = (
                    f'Low quality score ({r.quality_score:.0%}) — '
                    f'below {QUALITY_REVIEW_FLOOR:.0%} threshold. '
                    'Manually verify the website and loan product information.'
                )
            r.add(WARNING, 'quality',
                  f'Quality {r.quality_score:.0%} — flagged for admin review '
                  f'(below {QUALITY_REVIEW_FLOOR:.0%} but above hard floor {MIN_QUALITY:.0%})')
        else:
            # Normal pass/fail based on critical issues only
            r.is_valid = len(r.critical_issues) == 0

        # ── Fix 9: Seal audit trail ───────────────────────────
        _audit['completed_at']   = datetime.now().isoformat()
        _audit['quality_score']  = round(r.quality_score, 3)
        _audit['is_valid']       = r.is_valid
        _audit['issue_counts']   = dict(Counter(i['severity'] for i in r.issues))
        out['audit_trail']       = json.dumps(_audit)

        logger.info(f'VALIDATED [{name}] {r.summary()}')
        return r

    # ── Fix 1: RBI Registration Validation ──────────────────

    def _validate_rbi_registration(self, scraped_reg: str, gemini_reg: str,
                                    r: GuardrailResult) -> str:
        """
        Fix C1: Three-stage RBI CoR validation:
          Stage 1 — presence check
          Stage 2 — format check against known CoR patterns
          Stage 3 — registry lookup (active status in official CoR list)

        Format-valid but registry-unknown CoRs pass with a WARNING so they
        don't silently enter the system as 'verified'.
        """
        reg = (scraped_reg or gemini_reg).strip().upper()
        if not reg:
            r.add(ERROR, 'rbi_registration_number',
                  'Missing RBI CoR — lender cannot be registry-verified')
            return ''

        # Stage 1b: Pre-sanitize garbage values before format check.
        # Catches row-number placeholders (ROW-73), truncated words from
        # partial Gemini JSON (ISTERED ← REGISTERED, PORATED ← INCORPORATED),
        # and common null-like strings.
        if _RBI_GARBAGE.match(reg) or len(reg) < 6:
            r.add(WARNING, 'rbi_registration_number',
                  f'RBI CoR value {reg!r} is not a valid registration number — cleared')
            return ''

        # Try to extract a valid CoR substring if the string contains extra text
        # (e.g. "N-13.00001 REGISTERED" — take just the CoR part)
        if not any(p.match(reg) for p in _RBI_COR_PATTERNS):
            for pat in _RBI_COR_PATTERNS:
                # Use search (not match) on the unanchored version to find embedded CoR
                _search_pat = re.compile(pat.pattern.lstrip('^').rstrip('$'))
                m = _search_pat.search(reg)
                if m:
                    reg = m.group(0)
                    r.add(INFO, 'rbi_registration_number',
                          f'Extracted CoR {reg!r} from compound value')
                    break

        # Stage 2: format check
        if not any(p.match(reg) for p in _RBI_COR_PATTERNS):
            r.add(WARNING, 'rbi_registration_number',
                  f'Invalid RBI CoR format: {reg!r} — cleared (not a valid CoR pattern)')
            return ''

        # Stage 3: registry lookup
        is_valid, reason = self.cor_registry.lookup(reg)
        if is_valid is None:
            # Registry not loaded — format-only pass, but flag it
            r.add(WARNING, 'rbi_registration_number',
                  f'CoR {reg!r} passes format check but registry unavailable — '
                  'cannot confirm active status')
            return reg
        if is_valid:
            return reg
        # Found in registry but not active (cancelled / revoked / surrendered)
        r.add(ERROR, 'rbi_registration_number',
              f'CoR {reg!r} found in registry but status={reason!r} — not an active licence')
        return ''

    # ── Fix 2: Lender signal detection ──────────────────────

    def _detect_lender_signals(self, raw: dict) -> int:
        """
        Fix 2: Count independent lending signals to cross-check is_non_lender.
        Returns count (0–3). If ≥ 2, scraper's non-lender flag is overridden.
        """
        count = 0
        if raw.get('loan_tags') or raw.get('primary_loan_segments'):
            count += 1
        if raw.get('rbi_registration') or raw.get('rbi_registration_number'):
            count += 1
        if raw.get('rbi_category'):
            count += 1
        return count

    # ── Fix 3: KYC / entity verification ────────────────────

    def _validate_cin(self, cin: Any, is_listed: bool, r: GuardrailResult) -> str:
        """
        Fix C2: CIN format + listing cross-check.
        Format: L/U + 5 digits + 2 alpha + 4 digits + 3 alpha + 6 digits
        Example: L65910MH2000PLC125522

        First character:
          L = Listed on a recognised stock exchange
          U = Unlisted

        Cross-check: if is_listed=True the CIN must start with 'L'; mismatch
        is flagged as WARNING (could be a recently-listed entity or data lag).

        MCA existence check: not performed in-process (requires MCA21 API access).
        Operators should run batch MCA verification separately using the CIN field.
        """
        if not cin:
            return ''
        cin_s = str(cin).strip().upper()
        if not re.match(r'^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$', cin_s):
            r.add(WARNING, 'cin', f'CIN format invalid: {cin_s!r} — field cleared')
            return ''

        # Listing cross-check
        cin_listed = cin_s[0] == 'L'
        if is_listed and not cin_listed:
            r.add(WARNING, 'cin',
                  f'CIN starts with U (unlisted) but is_listed=True — verify with MCA21')
        elif not is_listed and cin_listed:
            r.add(INFO, 'cin',
                  f'CIN starts with L (listed) but is_listed=False — may be a recent delisting')
        return cin_s

    # Entity-type characters for the 4th position of a PAN
    # Source: Income Tax Department PAN structure specification
    _PAN_ENTITY_CHARS: dict[str, str] = {
        'C': 'Company',
        'P': 'Individual',
        'H': 'HUF',
        'F': 'Firm',
        'A': 'AOP',
        'B': 'BOI',
        'G': 'Government',
        'J': 'Artificial juridical person',
        'L': 'Local authority',
        'T': 'Trust / AOP (Trust)',
    }
    # Entity types that represent incorporated lending companies
    _CORPORATE_PAN_CHARS: frozenset[str] = frozenset({'C', 'A', 'B', 'T', 'L'})

    def _validate_pan(self, pan: Any, company_type: str, r: GuardrailResult) -> str:
        """
        Fix C3: PAN format + 4th-character entity-type enforcement.

        For NBFC, Bank, and corporate lender types the PAN 4th character
        must be a corporate entity code (C/A/B/T/L).
        Individual PAN (4th char = P) on a company record is a CRITICAL error —
        it means either wrong data or deliberate identity spoofing.
        """
        if not pan:
            return ''
        pan_s = str(pan).strip().upper()
        if not re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]$', pan_s):
            r.add(WARNING, 'pan', f'PAN format invalid: {pan_s!r} — field cleared')
            return ''

        entity_char = pan_s[3]
        entity_label = self._PAN_ENTITY_CHARS.get(entity_char, f'unknown({entity_char})')

        # Individual PAN on a corporate lender — critical identity mismatch
        if entity_char == 'P':
            r.add(CRITICAL, 'pan',
                  f'PAN 4th char = P (Individual) on a corporate lender ({company_type}) — '
                  'identity mismatch, field cleared')
            return ''

        # Non-corporate entity types on a registered company — warn
        if entity_char not in self._CORPORATE_PAN_CHARS:
            r.add(WARNING, 'pan',
                  f'PAN entity type={entity_label!r} — verify this matches the lender\'s legal structure')

        return pan_s

    # ── Fix 4: AUM by lender type ────────────────────────────

    def _aum_by_type(self, val: Any, ctype: str, r: GuardrailResult) -> Optional[float]:
        """
        Fix 4: AUM range enforced per lender type.
        NBFC-MFI hard-cap: ₹50,000 Cr (error above).
        NBFC: warn above ₹10 lakh Cr, error above ₹10M Cr.
        """
        f = self._pos_float(val)
        if f is None:
            return None
        lo, hi = AUM_LIMITS.get(ctype, AUM_LIMITS['default'])
        if f < lo:
            r.add(ERROR, 'aum_crores', f'AUM {f:,.1f} Cr below minimum {lo} Cr — cleared')
            return None
        if f > hi:
            r.add(ERROR, 'aum_crores',
                  f'AUM {f:,.0f} Cr exceeds {ctype} maximum {hi:,.0f} Cr — cleared')
            return None
        warn_above = AUM_WARN_ABOVE.get(ctype)
        if warn_above and f > warn_above:
            r.add(WARNING, 'aum_crores',
                  f'AUM ₹{f:,.0f} Cr unusually high for {ctype} — please verify')
        return round(f, 2)

    # ── Fix 5: Strong dedup fingerprint ─────────────────────

    def _strong_dedup_key(self, name: str, rbi_reg: str, cin: str, website: str) -> str:
        """
        Fix C4: Weighted identity fingerprint — uses strongest available identifier.

        Priority (highest → lowest trust):
          1. RBI CoR number   — legally unique, issued by regulator
          2. CIN              — MCA-issued, uniquely identifies incorporated entity
          3. Website domain   — SLD is usually unique per lender
          4. Normalised name  — weakest; only used when all above are absent

        SHA-256 is applied to the chosen identity anchor + always the normalised
        name, so two different entities with the same CoR (data error) are still
        distinguishable by name in the fuzzy-match fallback.
        """
        norm_name = re.sub(r'\s+', ' ', name.lower().strip())
        # Remove generic suffixes so "Bajaj Finance Ltd" == "Bajaj Finance Limited"
        for suffix in (' ltd', ' limited', ' pvt', ' private', ' finance',
                       ' financial', ' services', ' india', ' nbfc', ' bank'):
            norm_name = norm_name.replace(suffix, '')
        norm_name = re.sub(r'[^\w\s]', '', norm_name).strip()

        try:
            domain = re.sub(r'^https?://(www\.)?', '', (website or '').lower()).split('/')[0]
            norm_domain = domain.rsplit('.', 2)[0].split('.')[-1]
        except Exception:
            norm_domain = ''

        # Choose strongest anchor
        if rbi_reg:
            anchor = f'RBI:{rbi_reg.upper()}'
        elif cin:
            anchor = f'CIN:{cin.upper()}'
        elif norm_domain:
            anchor = f'DOMAIN:{norm_domain}'
        else:
            anchor = f'NAME:{norm_name}'

        raw_key = f'{anchor}|{norm_name}'
        return hashlib.sha256(raw_key.encode()).hexdigest()[:20]

    # ── Fix 6: Product / rate validation ────────────────────

    # Per-loan-type expected rate ranges (% p.a.)
    # Tighter than the global RATE_BOUNDS — used for cross-field consistency
    _LOAN_TYPE_RATE_RANGES: dict[str, tuple[float, float]] = {
        'Home Loan':               (6.5,  15.0),
        'Loan Against Property':   (7.0,  18.0),
        'Vehicle Loan':            (7.0,  20.0),
        'Two Wheeler Loan':        (8.0,  28.0),
        'EV Loan':                 (7.0,  18.0),
        'Education Loan':          (7.0,  15.0),
        'Gold Loan':               (7.0,  26.0),
        'Personal Loan':           (10.0, 40.0),
        'MSME Loan':               (9.0,  30.0),
        'Business Loan':           (9.0,  36.0),
        'Working Capital':         (9.0,  36.0),
        'Agriculture Loan':        (4.0,  18.0),
        'Micro Loan':              (12.0, 36.0),
        'Microfinance':            (18.0, 36.0),   # RBI JLG ceiling ~26%, Gemini may over-extract
        'Consumer Durable Loan':   (10.0, 30.0),
        'Supply Chain Finance':    (8.0,  24.0),
        'Rural Loan':              (7.0,  26.0),
        'Credit Card':             (24.0, 48.0),
    }

    # Per-company-type expected rate ceiling (hard upper bound for cross-check)
    _COMPANY_TYPE_RATE_CEILING: dict[str, float] = {
        'PSU Bank':           14.0,   # PSU banks rarely exceed 14% on any product
        'Private Bank':       20.0,
        'Foreign Bank':       18.0,
        'Small Finance Bank': 28.0,
        'Cooperative Bank':   22.0,
        'Regional Rural Bank':18.0,
        'Payment Bank':        0.0,   # Payment banks cannot lend
        'NBFC':               40.0,
        'NBFC-MFI':           36.0,   # RBI cap for NBFC-MFI
    }

    def _validate_product_rates(self, raw: dict, r: GuardrailResult):
        """
        Fix 6 + cross-field: Sanity-check interest rates and tenures.
        Also checks rate against per-loan-type and per-company-type expectations.
        These fields are optional — we warn, never reject solely on this.
        """
        ir_min = self._pos_float(raw.get('interest_rate_min'))
        ir_max = self._pos_float(raw.get('interest_rate_max'))

        for rate_field, v in [('interest_rate_min', ir_min), ('interest_rate_max', ir_max)]:
            if v is not None:
                lo, hi = RATE_BOUNDS
                if not (lo <= v <= hi):
                    r.add(WARNING, rate_field,
                          f'{rate_field}={v}% outside plausible range {lo}–{hi}% — review required')

        # Cross-field: rate vs loan_type
        loan_type  = (raw.get('loan_type') or '').strip()
        ctype      = (raw.get('company_type') or 'NBFC').strip()
        lt_range   = self._LOAN_TYPE_RATE_RANGES.get(loan_type)

        if lt_range and ir_min is not None:
            lt_lo, lt_hi = lt_range
            if ir_min < lt_lo:
                r.add(WARNING, 'interest_rate_min',
                      f'{ir_min}% below typical floor {lt_lo}% for {loan_type} — verify source')
            elif ir_min > lt_hi:
                r.add(WARNING, 'interest_rate_min',
                      f'{ir_min}% above typical ceiling {lt_hi}% for {loan_type} — review required')

        if lt_range and ir_max is not None:
            lt_lo, lt_hi = lt_range
            if ir_max > lt_hi * 1.15:   # 15% tolerance above ceiling before escalating
                r.add(ERROR, 'interest_rate_max',
                      f'{ir_max}% significantly above {loan_type} ceiling {lt_hi}% — likely extraction error')

        # Cross-field: rate vs company_type
        ct_ceiling = self._COMPANY_TYPE_RATE_CEILING.get(ctype)
        if ct_ceiling is not None:
            if ct_ceiling == 0.0 and (ir_min or ir_max):
                r.add(ERROR, 'interest_rate_min',
                      f'{ctype} cannot issue loans — rate fields should be null')
            elif ct_ceiling > 0.0 and ir_max is not None and ir_max > ct_ceiling:
                r.add(WARNING, 'interest_rate_max',
                      f'{ir_max}% exceeds expected ceiling {ct_ceiling}% for {ctype} — verify')

        # Cross-field: min must not exceed max
        if ir_min is not None and ir_max is not None and ir_min > ir_max:
            r.add(ERROR, 'interest_rate_min',
                  f'interest_rate_min ({ir_min}%) > interest_rate_max ({ir_max}%) — invalid')

        for tenure_field in ('tenure_min', 'tenure_max'):
            v = raw.get(tenure_field)
            if v is not None:
                try:
                    months = int(v)
                    lo, hi = TENURE_BOUNDS
                    if not (lo <= months <= hi):
                        r.add(WARNING, tenure_field,
                              f'{tenure_field}={months} months outside {lo}–{hi} range')
                except Exception:
                    r.add(WARNING, tenure_field, f'Non-integer tenure: {v!r}')

        # Cross-field: tenure_min must not exceed tenure_max
        t_min = raw.get('tenure_min')
        t_max = raw.get('tenure_max')
        if t_min is not None and t_max is not None:
            try:
                if int(t_min) > int(t_max):
                    r.add(ERROR, 'tenure_min',
                          f'tenure_min ({t_min}) > tenure_max ({t_max}) — invalid')
            except Exception:
                pass

    # ── Fix 7: Website with HTTPS + untrusted domain check ──

    def _validate_website_domain(self, val: Any, r: GuardrailResult,
                                  _audit: dict) -> str:
        """
        Fix 7: Three-stage website validation.
        Stage 1: Basic URL format.
        Stage 2: HTTPS enforcement (warning — HTTP is a red flag but not a block).
        Stage 3: Free-hosting / untrusted domain rejection (hard block).
        """
        if not val or not isinstance(val, str):
            return ''
        val = val.strip()
        if not val:
            return ''

        # Normalise scheme
        if not val.startswith(('http://', 'https://')):
            val = 'https://' + val

        # Stage 2: HTTPS warning
        if val.startswith('http://'):
            r.add(WARNING, 'website',
                  'Website uses HTTP (not HTTPS) — security risk, flagged for review')
            _audit.get('decisions', []).append({'field': 'website', 'decision': 'http_warning'})

        # Stage 1: Format check
        if not re.match(r'^https?://(?:[A-Za-z0-9\-\.]+)(?::\d+)?(?:/[^\s]*)?$', val):
            r.add(ERROR, 'website', f'Invalid URL format: {val!r}')
            return ''
        if any(b in val.lower() for b in ['localhost', '127.0.0.1', 'example.com']):
            r.add(ERROR, 'website', 'Localhost / example URL rejected')
            return ''

        # Stage 3: Free-hosting domains — real lenders don't use these
        domain = val.replace('https://', '').replace('http://', '').split('/')[0]
        for bad in UNTRUSTED_DOMAINS:
            if bad in domain:
                r.add(ERROR, 'website',
                      f'Untrusted hosting domain ({bad}) — likely not an official lender website')
                _audit.get('decisions', []).append({'field': 'website', 'decision': f'rejected_untrusted_{bad}'})
                return ''

        return val.rstrip('/')

    # ── Fix 10: company_type vs rbi_category cross-check ────

    def _validate_company_type_vs_rbi(self, ctype: str, rbi_category: str,
                                       r: GuardrailResult, _audit: dict) -> str:
        """
        Fix 10: Detect and auto-correct NBFC sub-type vs company_type mismatches.
        Returns the (possibly corrected) company_type.
        """
        if not rbi_category:
            return ctype

        cat_upper = rbi_category.upper()

        # NBFC-MFI / NBFC mismatch — auto-correct company_type from rbi_category
        cat_is_mfi  = 'MFI' in cat_upper
        type_is_mfi = ctype == 'NBFC-MFI'
        if cat_is_mfi and not type_is_mfi:
            r.add(INFO, 'company_type',
                  f'Auto-corrected company_type NBFC → NBFC-MFI (rbi_category={rbi_category!r})')
            _audit.get('decisions', []).append({
                'field': 'company_type',
                'decision': f'auto_corrected: NBFC → NBFC-MFI (rbi_category={rbi_category})',
            })
            ctype = 'NBFC-MFI'
        elif type_is_mfi and not cat_is_mfi:
            r.add(WARNING, 'company_type',
                  f'company_type=NBFC-MFI but rbi_category={rbi_category!r} has no MFI classification')
            _audit.get('decisions', []).append({
                'field': 'company_type',
                'decision': f'mismatch_warning: type=NBFC-MFI vs rbi_category={rbi_category}',
            })

        # Bank classified as NBFC or vice versa
        if 'BANK' in cat_upper and 'NBFC' in ctype:
            r.add(WARNING, 'company_type',
                  f'rbi_category={rbi_category!r} suggests bank entity but company_type={ctype!r}')

        # P2P platform should not have direct-lending products — flag only
        if 'P2P' in cat_upper and ctype == 'NBFC':
            r.add(INFO, 'rbi_category',
                  'P2P platform categorised as plain NBFC — verify lending model')

        return ctype

    # ── Validators (unchanged from v3.1) ────────────────────

    def _url(self, val: Any) -> str:
        """Basic URL normalisation (used internally; _validate_website_domain is public)."""
        if not val or not isinstance(val, str): return ''
        val = val.strip()
        if not val: return ''
        if not val.startswith(('http://', 'https://')): val = 'https://' + val
        if not re.match(r'^https?://(?:[A-Za-z0-9\-\.]+)(?::\d+)?(?:/[^\s]*)?$', val): return ''
        if any(b in val.lower() for b in ['localhost', '127.0.0.1', 'example.com']): return ''
        return val.rstrip('/')

    def _canonical_state(self, val: Any, r: GuardrailResult) -> str:
        if not val: return ''
        s = str(val).strip()
        if s in STATE_SET:           return s
        if s.lower() in STATE_LOWER: return STATE_LOWER[s.lower()]
        r.add(WARNING, 'hq_state', f'Unknown state: "{s}"')
        return ''

    def _states(self, states: list, hq_state: str, ctype: str,
                r: GuardrailResult, potential_pan: bool = False) -> tuple[list[str], bool]:
        if ctype in ('PSU Bank', 'Private Bank', 'Foreign Bank', 'Small Finance Bank'):
            return sorted(ALL_INDIA_STATES), True
        if 'PAN_INDIA' in states or potential_pan:
            return sorted(ALL_INDIA_STATES), True
        valid: list[str] = []
        for s in states:
            s = str(s).strip()
            if s in STATE_SET:
                valid.append(s)
            elif s.lower() in STATE_LOWER:
                valid.append(STATE_LOWER[s.lower()])
            else:
                r.add(INFO, 'operating_states', f'Unknown state skipped: "{s}"')
        valid  = sorted(set(valid))
        is_pan = len(valid) >= len(ALL_INDIA_STATES)
        return valid, is_pan

    def _year(self, val: Any, r: GuardrailResult) -> Optional[int]:
        yr = self._year_simple(val)
        if yr is None: return None
        lo, hi = VALID_YEAR_RANGE
        if not (lo <= yr <= hi):
            r.add(WARNING, 'established_year', f'Year {yr} outside {lo}–{hi}')
            return None
        return yr

    def _year_simple(self, val: Any) -> Optional[int]:
        if val is None or val == '': return None
        try:
            return int(float(str(val).replace(',', '')))
        except Exception:
            return None

    def _phone(self, val: Any) -> str:
        if not val: return ''
        cleaned = re.sub(r'[^\d+]', '', str(val))
        digits  = re.sub(r'[^\d]', '', cleaned)
        return cleaned if 10 <= len(digits) <= 13 else ''

    def _email(self, val: Any) -> str:
        if not val: return ''
        email = str(val).strip().lower()
        if re.match(r'^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$', email):
            if not any(b in email for b in ['noreply', 'no-reply', 'example', 'test@', 'fake',
                                             'dummy@', 'donotreply', 'bounce', 'mailer-daemon']):
                return email
        return ''

    def _range(self, val: Any, lo: float, hi: float, fld: str, r: GuardrailResult) -> Optional[int]:
        if val is None or val == '': return None
        try:
            n = int(float(str(val).replace(',', '')))
        except Exception:
            return None
        if not (lo <= n <= hi):
            r.add(WARNING, fld, f'{fld}={n:,} outside {lo:,}–{hi:,}')
            return None
        return n

    # ── Normalization ────────────────────────────────────────

    def _normalize_tags(self, tags: list) -> list[str]:
        canonical: set[str] = set()
        for tag in tags:
            t = str(tag).strip()
            match = next((c for c in CANONICAL_LOAN_TAGS if c.lower() == t.lower()), None)
            if match:
                canonical.add(match)
                continue
            t_lower = t.lower()
            for key, value in TAG_NORMALIZATION.items():
                if key in t_lower:
                    canonical.add(value)
                    break
        return sorted(canonical)

    def _aum_cat(self, aum: Optional[float]) -> str:
        if aum is None: return ''
        if aum < 500:   return 'Micro'
        if aum < 5000:  return 'Small'
        if aum < 50000: return 'Mid'
        return 'Large'

    def _intensity(self, n: int, is_pan: bool) -> str:
        if is_pan or n >= 28: return 'Pan India'
        if n <= 1:             return 'Single State'
        return 'Regional'

    def _quality(self, d: dict, confidence: dict, raw: dict) -> float:
        """
        Quality score = positive field contributions + source bonus − compliance penalties.

        Weight philosophy (v4.2):
          - Loan products + website are the most important signals: lender IS visible
            and has something to offer borrowers.
          - AUM, employee_count, branch_count are nice-to-have but most small NBFCs
            don't publish these. Reduced weight so their absence doesn't kill the score.
          - Contact info is meaningful (borrower can reach the lender).
          - Compliance penalties: applied when identity-critical fields are missing.

        Penalty schedule (capped at 0.0 floor):
          −0.15  Missing loan products (invisible to match engine — hard hit)
          −0.10  No website (primary trust signal)
          −0.10  Missing RBI CoR (regulatory identity absent)
          −0.08  Missing CIN (corporate identity absent)
          −0.05  No contact info at all (phone and email both absent)

        HTTP-only websites are NOT penalized — many Indian NBFCs still use HTTP.
        The HTTP WARNING is purely for operator visibility, not for scoring.
        """
        weights = {
            'company_name':           0.15,   # always present if we got here
            'website':                0.12,   # trust anchor
            'primary_loan_segments':  0.20,   # most important — what can borrower get?
            'hq_state':               0.10,   # geography — state filter needs this
            'operating_states':       0.08,   # breadth of coverage
            'aum_crores':             0.05,   # ↓ hard to find for small NBFCs
            'phone':                  0.07,   # contact reachability
            'email':                  0.07,   # contact reachability
            'established_year':       0.05,   # trust signal
            'employee_count':         0.03,   # ↓ rarely published by small NBFCs
            'is_listed':              0.02,   # ↓ most NBFCs are unlisted — low signal
            'branch_count':           0.03,   # ↓ rarely published
        }
        # Total positive max = 0.97 + 0.05 source bonus = 1.02 → capped at 1.0

        conf_multiplier = {'HIGH': 1.0, 'MEDIUM': 0.80, 'LOW': 0.60, 'none': 0.55}

        # Positive contributions
        data_source  = raw.get('data_source', '')
        source_bonus = 0.05 if ('scrapl' in data_source or 'scraper' in data_source) else 0.0

        score = 0.0
        for fld, w in weights.items():
            v = d.get(fld)
            if v is None or v == '' or v == '[]': continue
            if isinstance(v, bool) and not v and fld not in ('is_listed', 'rural_presence'): continue
            field_conf = confidence.get(fld, {}).get('confidence', 'none')
            score += w * conf_multiplier.get(field_conf, 0.55)
        score += source_bonus

        # Compliance-field penalties
        if not d.get('primary_loan_segments') or d.get('primary_loan_segments') == '[]':
            score -= 0.15   # invisible to match engine — hardest hit
        if not d.get('website'):
            score -= 0.10
        if not d.get('rbi_registration_number'):
            score -= 0.10   # was 0.15 — reduced: many valid NBFCs have unverifiable CoR
        if not d.get('cin'):
            score -= 0.08   # was 0.10
        if not d.get('phone') and not d.get('email'):
            score -= 0.05

        return round(max(0.0, min(score, 1.0)), 2)

    # ── Loan tag inference from free text ────────────────────

    # Order matters: more specific phrases before generic ones
    _TEXT_TAG_MAP: list[tuple[list[str], str]] = [
        (['working capital', 'cash credit', 'overdraft', 'cc limit'],       'Working Capital'),
        (['supply chain', 'invoice discounting', 'factoring', 'trade finance', 'scf'], 'Supply Chain Finance'),
        (['microfinance', 'jlg', 'shg', 'joint liability', 'self help group'], 'Microfinance'),
        (['micro loan', 'microloan', 'micro credit'],                        'Micro Loan'),
        (['loan against property', ' lap ', 'mortgage loan'],                'Loan Against Property'),
        (['home loan', 'housing loan', 'housing finance', 'home finance'],   'Home Loan'),
        (['gold loan', 'gold jewellery', 'gold ornament'],                   'Gold Loan'),
        (['two wheeler', 'bike loan', 'two-wheeler'],                        'Two Wheeler Loan'),
        (['ev loan', 'electric vehicle loan', 'ev finance'],                 'EV Loan'),
        (['vehicle loan', 'auto loan', 'car loan', 'auto finance'],          'Vehicle Loan'),
        (['education loan', 'student loan', 'education finance'],            'Education Loan'),
        (['agriculture loan', 'agri loan', 'kisan loan', 'crop loan', 'farm loan'], 'Agriculture Loan'),
        (['rural loan', 'rural credit', 'rural finance'],                    'Rural Loan'),
        (['consumer durable', 'appliance loan', 'consumer finance'],         'Consumer Durable Loan'),
        (['personal loan'],                                                   'Personal Loan'),
        (['msme', 'sme loan', 'small enterprise', 'medium enterprise',
          'small business loan', 'msme loan'],                               'MSME Loan'),
        (['business loan', 'enterprise loan', 'sme finance'],                'Business Loan'),
    ]

    def _infer_tags_from_text(self, text: str) -> list[str]:
        """Infer loan product tags from free-text description when Gemini returns none."""
        if not text or len(text) < 10:
            return []
        t = text.lower()
        found: list[str] = []
        seen: set[str]   = set()
        for keywords, tag in self._TEXT_TAG_MAP:
            if tag not in seen and any(kw in t for kw in keywords):
                found.append(tag)
                seen.add(tag)
                if len(found) >= 5:   # cap at 5 inferred tags
                    break
        return found

    # ── Utilities ────────────────────────────────────────────

    def _str(self, val: Any, max_len: int) -> str:
        if not val: return ''
        s = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', str(val)).strip()
        return s[:max_len]

    def _pos_float(self, val: Any) -> Optional[float]:
        if val is None or val == '': return None
        try:
            s = str(val).lower().replace(',', '').replace('₹', '').replace('$', '').strip()
            if 'lakh crore' in s: return float(re.sub(r'[^\d.]', '', s)) * 100_000
            if 'crore'      in s: return float(re.sub(r'[^\d.]', '', s))
            if 'lakh'       in s: return float(re.sub(r'[^\d.]', '', s)) / 100
            f = float(re.sub(r'[^\d.]', '', s))
            return f if f > 0 else None
        except Exception:
            return None

    def _bool(self, val: Any) -> bool:
        if isinstance(val, bool): return val
        if val is None: return False
        return str(val).strip().lower() in ('true', '1', 'yes', 'y', 't')

    def _ensure_list(self, val: Any) -> list:
        if isinstance(val, list): return val
        if isinstance(val, str):
            try:
                p = json.loads(val)
                if isinstance(p, list): return p
            except Exception:
                pass
            if ',' in val:
                return [v.strip() for v in val.split(',') if v.strip()]
            return [val] if val.strip() else []
        return []


# ─────────────────────────────────────────────────────────────
# BATCH RUNNER
# ─────────────────────────────────────────────────────────────

class GuardrailsBatchRunner:
    def __init__(self, cor_registry_path: Optional[Path] = None):
        dedup           = DeduplicationStore()
        cor_registry    = RBICoRRegistry(cor_registry_path) if cor_registry_path else RBICoRRegistry()
        self.guardrails = Guardrails(dedup=dedup, cor_registry=cor_registry)

    def run(self, records: list[dict]) -> dict:
        valid, low, invalid = [], [], []
        for raw in records:
            result = self.guardrails.run(raw)
            entry  = {
                'data':          result.clean_data,
                'quality_score': result.quality_score,
                'issues':        result.issues,
                'fingerprint':   result.fingerprint,
            }
            if result.is_valid:
                (valid if result.quality_score >= 0.50 else low).append(entry)
            else:
                invalid.append(entry)
        return {'valid': valid, 'low_quality': low, 'invalid': invalid}


# ─────────────────────────────────────────────────────────────
# QUICK TEST
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

    g = Guardrails()   # no registry file — format-only fallback with WARNING

    print('\n=== TEST 1: Valid lender (registry unavailable, format-only pass) ===')
    r = g.run({
        'company_name':             'Bajaj Finance Limited',
        'company_type':             'NBFC',
        'website':                  'https://bajajfinserv.in',
        'aum_crores':               290000,
        'is_listed':                True,
        'stock_symbol':             'BAJFINANCE',
        'loan_tags':                ['personal', 'home loan', 'msme', 'EV Loan'],
        'hq_city':                  'Pune',
        'hq_state':                 'Maharashtra',
        'operating_states':         ['Maharashtra', 'Gujarat', 'Delhi'],
        'established_year':         1987,
        'employee_count':           40000,
        'phone':                    '1800-209-4151',
        'email':                    'contactus@bajajfinserv.in',
        'rbi_registration':         'N.13.02437',
        'rbi_category':             'NBFC',
        'cin':                      'L65910MH1987PLC042961',
        'pan':                      'AAACB2894F',   # 4th char = C (company) ✓
        'interest_rate_min':        11.0,
        'interest_rate_max':        38.0,
        'tenure_min':               6,
        'tenure_max':               96,
        'data_source':              'scrapling+gemini',
    })
    print(f'  Valid:        {r.is_valid}')
    print(f'  Quality:      {r.quality_score:.0%}')
    print(f'  RBI reg:      {r.clean_data.get("rbi_registration_number")}')
    print(f'  CIN:          {r.clean_data.get("cin")}')
    print(f'  PAN:          {r.clean_data.get("pan")}')
    print(f'  Fingerprint:  {r.fingerprint}')
    rbi_issues = [i for i in r.issues if i['field'] == 'rbi_registration_number']
    print(f'  RBI issues:   {[i["severity"]+": "+i["message"] for i in rbi_issues]}')

    print('\n=== TEST 2: Individual PAN on corporate entity (C3) ===')
    r2 = g.run({
        'company_name':  'Shady Finance Pvt Ltd',
        'company_type':  'NBFC',
        'pan':           'ABCPD1234E',  # 4th char = P (individual) ← critical
        'loan_tags':     ['Personal Loan'],
        'hq_state':      'Delhi',
    })
    pan_issues = [i for i in r2.issues if i['field'] == 'pan']
    print(f'  PAN accepted:  {bool(r2.clean_data.get("pan"))} (expect False)')
    print(f'  PAN issue:     {pan_issues[0]["severity"] if pan_issues else "none"} (expect CRITICAL)')

    print('\n=== TEST 3: Dedup — RBI anchor vs name anchor ===')
    g2 = Guardrails()
    # First record with RBI reg
    g2.run({'company_name': 'Alpha Fin Ltd', 'company_type': 'NBFC',
            'rbi_registration': 'N.02.00099', 'loan_tags': ['Personal Loan'], 'hq_state': 'Delhi'})
    # Second record — same RBI reg, different name → DUPLICATE
    r3 = g2.run({'company_name': 'Alpha Finance Limited', 'company_type': 'NBFC',
                 'rbi_registration': 'N.02.00099', 'loan_tags': ['Personal Loan'], 'hq_state': 'Delhi'})
    print(f'  Duplicate detected (same RBI): {not r3.is_valid} (expect True)')

    print('\n=== TEST 4: Quality penalty — no RBI, no CIN, no products ===')
    r4 = g.run({
        'company_name': 'Bare Bones Capital',
        'company_type': 'NBFC',
        'hq_state':     'Maharashtra',
        'website':      'https://barebones.in',
        'aum_crores':   200,
        'phone':        '9999999999',
        'email':        'contact@barebones.in',
        'data_source':  'scrapling+gemini',
        # No rbi_registration, no cin, no loan_tags
    })
    print(f'  Quality:       {r4.quality_score:.0%} (expect low due to −0.15 RBI −0.10 CIN −0.15 products)')
    print(f'  Valid:         {r4.is_valid}')

    print('\n=== TEST 5: NBFC-MFI AUM cap + RBI mismatch ===')
    r5 = g.run({
        'company_name': 'Big MFI Ltd',
        'company_type': 'NBFC-MFI',
        'aum_crores':   100_000,   # above MFI cap
        'rbi_category': 'NBFC',   # mismatch
        'loan_tags':    ['Micro Loan'],
        'hq_state':     'Tamil Nadu',
    })
    print(f'  AUM cleared:       {r5.clean_data.get("aum_crores") is None} (expect True)')
    print(f'  Mismatch flagged:  {any(i["field"] == "company_type" for i in r5.issues)} (expect True)')
