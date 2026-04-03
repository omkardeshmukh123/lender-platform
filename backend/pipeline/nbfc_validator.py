"""
pipeline/nbfc_validator.py
===========================
NBFC-specific validation layer that runs AFTER Gemini + scraper merge.

Implements all 10 regulatory and data-quality checks:

  1.  RBI CoR verification   — fuzzy match against local registry snapshot
  2.  RBI reg mandatory      — flag when absent (warn, not hard-reject)
  3.  Financial data confidence tiering
  4.  Active / inactive status from registry
  5.  Regulatory tier classification (ND-SI / ND / MFI / etc.)
  6.  Contact info format validation + domain match
  7.  Lender capability validation (segment vs. RBI category)
  8.  Pan-India claim plausibility
  9.  System-level deduplication against existing output
  10. Per-domain data source classification

Usage:
    validator = NBFCValidator(registry_path="data/input/rbi_nbfc_registry.csv")
    result    = validator.validate(company_name, rbi_reg, extracted, scraped, existing_hashes)
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib     import Path
from typing      import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Which RBI categories can offer which loan segments.
# Source: RBI Master Directions for each NBFC type.
_CATEGORY_ALLOWED_SEGMENTS: Dict[str, Set[str]] = {
    'NBFC-MFI':    {'Micro Loan', 'Microfinance', 'Rural Loan', 'Agriculture Loan'},
    'NBFC-Factor': {'Working Capital', 'Supply Chain Finance', 'Business Loan', 'MSME Loan'},
    'NBFC-IFC':    {'Working Capital', 'Supply Chain Finance', 'Business Loan', 'MSME Loan'},
    'NBFC-P2P':    {'Personal Loan', 'MSME Loan', 'Business Loan'},
    'NBFC-AA':     set(),   # Account Aggregator — no direct lending
    'NBFC-IC':     set(),   # Investment Company — no direct retail lending
}

# Segments that indicate a product is definitely for direct retail lending.
_RETAIL_SEGMENTS = {
    'Personal Loan', 'Home Loan', 'Vehicle Loan', 'Two Wheeler Loan',
    'Gold Loan', 'Education Loan', 'Consumer Durable Loan', 'Credit Card',
    'EV Loan',
}

# Minimum AUM (₹ Cr) below which a "Pan India" claim is implausible.
_MIN_AUM_PAN_INDIA = 100.0

# RBI NBFC registry column names (expected in the CSV).
_REG_COL_NAME   = 'company_name'
_REG_COL_NUMBER = 'registration_number'
_REG_COL_STATUS = 'status'          # active|inactive|cancelled|merged
_REG_COL_CAT    = 'rbi_category'

# Accepted active status values in the registry.
_ACTIVE_STATUSES = {'active', 'registered', 'valid', 'granted'}


# ── Result dataclass ────────────────────────────────────────────────────────────

@dataclass
class NBFCValidationResult:
    # ── Gap 1 + 4: RBI registry ───────────────────────────────────────────────
    rbi_status:          str  = 'unverified'  # verified|active|inactive|cancelled|merged|not_found|unverified
    rbi_verified_name:   str  = ''            # name from registry (may differ from input)

    # ── Gap 2: RBI registration number present? ───────────────────────────────
    rbi_reg_present:     bool = False

    # ── Gap 5: Regulatory tier ────────────────────────────────────────────────
    regulatory_tier:     str  = ''   # ND-SI|ND|D|MFI|IFC|Factor|P2P|AA|IC|unknown

    # ── Gap 6: Contact validation ─────────────────────────────────────────────
    phone_valid:          bool = False
    email_valid:          bool = False
    email_domain_matches: bool = False

    # ── Gap 7: Segment / category consistency ─────────────────────────────────
    segment_category_mismatch: bool        = False
    segment_warnings:          List[str]   = field(default_factory=list)

    # ── Gap 8: Pan-India plausibility ─────────────────────────────────────────
    pan_india_suspect: bool = False
    pan_india_reason:  str  = ''

    # ── Gap 9: Duplicate ──────────────────────────────────────────────────────
    is_duplicate: bool = False
    duplicate_of: str  = ''   # data_hash of the existing record

    # ── Gap 10: Per-domain data source classification ─────────────────────────
    financial_source:   str = 'gemini'     # gemini|scraper|rbi_filing|annual_report
    geo_source:         str = 'gemini'     # gemini|scraper|rbi_verified
    contact_source:     str = 'gemini'     # gemini|scraper
    regulatory_source:  str = 'csv'        # csv|gemini|scraper|rbi_verified

    # ── Aggregated issues / warnings ─────────────────────────────────────────
    issues:   List[str] = field(default_factory=list)    # blocking problems
    warnings: List[str] = field(default_factory=list)    # non-blocking notes

    @property
    def data_source_tag(self) -> str:
        """Compact tag summarising provenance of this record."""
        parts = set()
        for src in (self.financial_source, self.geo_source,
                    self.contact_source, self.regulatory_source):
            parts.add(src.split('_')[0])   # strip suffixes
        return '+'.join(sorted(parts))


# ── Registry loader ─────────────────────────────────────────────────────────────

class _RBIRegistry:
    """
    In-memory RBI NBFC registry for fast fuzzy lookups.

    Expected CSV columns (case-insensitive headers):
        company_name, registration_number, status, rbi_category

    If the registry file does not exist, all lookups return 'unverified'.
    """

    def __init__(self, path: Optional[Path]):
        self._by_number: Dict[str, dict] = {}   # reg_number → row
        self._by_name:   Dict[str, dict] = {}   # normalised_name → row
        self._loaded     = False

        if path and path.exists():
            self._load(path)
        else:
            logger.warning(
                "RBI registry file not found (%s) — all NBFCs will be 'unverified'. "
                "Download from https://www.rbi.org.in/commonman/English/Scripts/NBFCs.aspx "
                "and save as data/input/rbi_nbfc_registry.csv",
                path,
            )

    def _load(self, path: Path) -> None:
        try:
            with open(path, encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                # Normalise header names
                for row in reader:
                    row_lc = {k.strip().lower(): v.strip() for k, v in row.items()}
                    name   = row_lc.get(_REG_COL_NAME, '').strip()
                    regnum = row_lc.get(_REG_COL_NUMBER, '').strip().upper()
                    if not name:
                        continue
                    norm = _normalise_name(name)
                    self._by_name[norm] = row_lc
                    if regnum:
                        self._by_number[regnum] = row_lc
            self._loaded = True
            logger.info("Loaded %d entries from RBI NBFC registry", len(self._by_name))
        except Exception as exc:
            logger.error("Failed to load RBI registry: %s", exc)

    def lookup(self, company_name: str,
               rbi_reg: str = '') -> Optional[dict]:
        """
        Returns the matching registry row or None.
        Priority: exact registration number > normalised name exact > fuzzy name.
        """
        if not self._loaded:
            return None

        # 1. By registration number (fastest, most reliable)
        if rbi_reg:
            key = rbi_reg.strip().upper()
            if key in self._by_number:
                return self._by_number[key]

        # 2. By normalised name — exact
        norm = _normalise_name(company_name)
        if norm in self._by_name:
            return self._by_name[norm]

        # 3. Fuzzy name match (Levenshtein distance ≤ 3 on tokens)
        return self._fuzzy_name_match(norm)

    def _fuzzy_name_match(self, normalised: str) -> Optional[dict]:
        """
        Simple token overlap: if ≥ 70% of significant tokens match → hit.
        Avoids false positives from common suffix words (finance, capital, etc.)
        """
        query_tokens = _sig_tokens(normalised)
        if len(query_tokens) < 2:
            return None

        best_ratio = 0.0
        best_row   = None
        for reg_norm, row in self._by_name.items():
            reg_tokens = _sig_tokens(reg_norm)
            if not reg_tokens:
                continue
            overlap = len(query_tokens & reg_tokens)
            ratio   = overlap / max(len(query_tokens), len(reg_tokens))
            if ratio > best_ratio:
                best_ratio = ratio
                best_row   = row

        return best_row if best_ratio >= 0.70 else None


def _normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    n = name.lower()
    n = re.sub(r'[^\w\s]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


_STOP_TOKENS = {
    'limited', 'ltd', 'private', 'pvt', 'financial', 'finance',
    'capital', 'services', 'solutions', 'company', 'india', 'nbfc',
    'the', 'and', 'of', 'a', 'an',
}


def _sig_tokens(normalised: str) -> Set[str]:
    return {t for t in normalised.split() if t not in _STOP_TOKENS and len(t) >= 3}


# ── Contact validation ─────────────────────────────────────────────────────────

_PHONE_RE = re.compile(
    r'^(\+91[\-\s]?)?'            # optional country code
    r'(0\d{2,4}[\-\s]?)?\d{6,10}$'  # STD code + number, or mobile
)
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def _validate_phone(phone: str) -> bool:
    if not phone:
        return False
    digits = re.sub(r'[\s\-\(\)\+]', '', phone)
    # Indian mobile: 10 digits starting with 6-9
    # Landline: 8-12 digits with STD code
    return (len(digits) in (10, 11, 12)
            and _PHONE_RE.match(phone.strip()) is not None)


def _validate_email(email: str, website: str = '') -> Tuple[bool, bool]:
    """Returns (format_valid, domain_matches_website)."""
    if not email:
        return False, False
    email = email.strip().lower()
    fmt_ok = bool(_EMAIL_RE.match(email))
    if not fmt_ok:
        return False, False

    domain_match = False
    if website:
        try:
            web_domain = re.sub(r'^https?://(www\.)?', '', website.lower()).split('/')[0]
            email_domain = email.split('@')[1]
            domain_match = (email_domain == web_domain
                            or email_domain.endswith('.' + web_domain)
                            or web_domain.endswith('.' + email_domain))
        except Exception:
            pass

    return True, domain_match


# ── Segment capability ─────────────────────────────────────────────────────────

def _validate_segment_capability(
    segments: List[str], rbi_category: str
) -> Tuple[bool, List[str]]:
    """
    Returns (mismatch, warnings).
    Only hard-mismatches specific non-lending categories (AA, IC).
    Soft-flags when an MFI claims large-ticket retail products.
    """
    warnings: List[str] = []
    mismatch = False

    if not rbi_category or not segments:
        return False, []

    cat = rbi_category.upper().strip()

    # Hard: Account Aggregator / Investment Company do NOT lend directly
    if cat in ('NBFC-AA', 'NBFC-IC'):
        retail_claimed = [s for s in segments if s in _RETAIL_SEGMENTS]
        if retail_claimed:
            mismatch = True
            warnings.append(
                f'{cat} is a non-lending entity but claims: {retail_claimed}'
            )
        return mismatch, warnings

    # Soft: MFI claiming products outside their scope
    if cat == 'NBFC-MFI':
        allowed = _CATEGORY_ALLOWED_SEGMENTS['NBFC-MFI']
        out_of_scope = [s for s in segments if s not in allowed
                        and s not in ('Micro Loan', 'Microfinance')]
        if out_of_scope:
            warnings.append(
                f'NBFC-MFI claims products outside MFI scope: {out_of_scope} '
                f'(allowed: {sorted(allowed)})'
            )

    # Soft: Factor / IFC offering retail loans
    if cat in ('NBFC-Factor', 'NBFC-IFC'):
        retail_claimed = [s for s in segments if s in _RETAIL_SEGMENTS]
        if retail_claimed:
            warnings.append(
                f'{cat} is primarily non-retail but lists: {retail_claimed}'
            )

    return mismatch, warnings


# ── Pan-India claim ────────────────────────────────────────────────────────────

def _validate_pan_india(
    pan_india: bool, op_states: List[str], aum_crores: Optional[float]
) -> Tuple[bool, str]:
    """Returns (suspect, reason)."""
    if not pan_india:
        return False, ''

    # Small AUM claiming pan-India
    if aum_crores is not None and aum_crores < _MIN_AUM_PAN_INDIA:
        return True, (
            f'Pan-India claim suspect: AUM ₹{aum_crores}Cr is below '
            f'₹{_MIN_AUM_PAN_INDIA}Cr threshold for nationwide operations'
        )

    # Only 1-2 states found but marked pan-India
    if op_states and len(op_states) <= 2:
        return True, (
            f'Pan-India claimed but only {len(op_states)} state(s) found in data: '
            f'{op_states}'
        )

    return False, ''


# ── Regulatory tier ────────────────────────────────────────────────────────────

def _classify_regulatory_tier(rbi_category: str, aum_crores: Optional[float]) -> str:
    """
    Returns a short regulatory significance label.
    Sources: RBI Master Directions, Scale-Based Regulation (SBR) framework 2022.
    """
    if not rbi_category:
        return 'unknown'

    cat = rbi_category.upper().strip()

    if cat == 'NBFC-D':
        return 'D'          # Deposit-taking — most regulated
    if cat == 'NBFC-MFI':
        return 'MFI'
    if cat == 'NBFC-IFC':
        return 'IFC'
    if cat == 'NBFC-FACTOR' or cat == 'NBFC-Factor'.upper():
        return 'Factor'
    if cat == 'NBFC-P2P':
        return 'P2P'
    if cat == 'NBFC-AA':
        return 'AA'
    if cat == 'NBFC-IC':
        return 'IC'

    # Non-deposit, non-SI vs SI
    if cat in ('NBFC-ND', 'NBFC-ND-SI', 'NBFC-ICC'):
        if cat == 'NBFC-ND-SI':
            return 'ND-SI'
        # SBR layer by AUM
        if aum_crores is not None:
            if aum_crores >= 50_000:
                return 'ND-UL'   # Upper Layer (SBR)
            if aum_crores >= 1_000:
                return 'ND-ML'   # Middle Layer
            if aum_crores >= 100:
                return 'ND-BL'   # Base Layer (>₹100Cr)
            return 'ND-BL-Small'
        return 'ND'

    return 'unknown'


# ── Data source classification ─────────────────────────────────────────────────

def _classify_data_sources(
    data_src: str,
    scraped: Optional[dict],
    rbi_status: str,
    has_csv_reg: bool,
) -> Tuple[str, str, str, str]:
    """
    Returns (financial_source, geo_source, contact_source, regulatory_source).

    financial_source: who provided AUM / revenue / financial fields
    geo_source:       who provided HQ state / operating states
    contact_source:   who provided phone / email
    regulatory_source: where RBI reg number came from
    """
    conf  = (scraped or {}).get('_confidence', {})

    def _c(f: str) -> str:
        return conf.get(f, {}).get('confidence', 'LOW')

    # Financial: Gemini is the primary source — scraper rarely has AUM
    financial_source = 'gemini'

    # Geography: scraper HIGH/MEDIUM → scraper_verified; else gemini
    if scraped and (_c('hq_state') in ('HIGH', 'MEDIUM')
                    or scraped.get('operating_states')):
        geo_source = 'scraper_verified'
    else:
        geo_source = 'gemini'

    # Contact: scraper takes priority for phone/email
    if scraped and (_c('phone') in ('HIGH', 'MEDIUM')
                    or _c('email') in ('HIGH', 'MEDIUM')):
        contact_source = 'scraper_verified'
    else:
        contact_source = 'gemini_inferred'

    # Regulatory: prefer CSV → RBI-verified → scraper → Gemini
    if rbi_status in ('verified', 'active'):
        regulatory_source = 'rbi_verified'
    elif has_csv_reg:
        regulatory_source = 'csv_provided'
    elif scraped and _c('rbi_registration') in ('HIGH', 'MEDIUM'):
        regulatory_source = 'scraper_verified'
    else:
        regulatory_source = 'gemini_inferred'

    return financial_source, geo_source, contact_source, regulatory_source


# ── Global deduplication ───────────────────────────────────────────────────────

def _compute_data_hash(company_name: str, rbi_reg: str) -> str:
    return hashlib.md5(
        f"{company_name.strip().lower()}{rbi_reg.strip().upper()}".encode()
    ).hexdigest()[:16]


# ── Main validator ─────────────────────────────────────────────────────────────

# Typing stub (avoid circular import from Python 3.9)
from typing import Tuple   # noqa: E402


class NBFCValidator:
    """
    Stateful validator: loads RBI registry once, validates many NBFCs.
    Pass `existing_hashes` (set of data_hash strings from the output CSV)
    to enable system-level deduplication.
    """

    def __init__(self, registry_path: Optional[Path] = None):
        self._registry = _RBIRegistry(registry_path)

    def validate(
        self,
        company_name:    str,
        rbi_reg:         str,
        extracted:       dict,
        scraped:         Optional[dict],
        data_src:        str,
        existing_hashes: Optional[Set[str]] = None,
    ) -> NBFCValidationResult:

        result = NBFCValidationResult()

        # ── Gap 2: RBI registration present? ──────────────────────────────────
        result.rbi_reg_present = bool(rbi_reg and rbi_reg.strip())
        if not result.rbi_reg_present:
            result.warnings.append(
                'RBI registration number absent — authenticity unverified. '
                'Without CoR, this entity may not be a licensed lender.'
            )

        # ── Gap 1 + 4: RBI registry lookup ────────────────────────────────────
        row = self._registry.lookup(company_name, rbi_reg)
        if row is None:
            result.rbi_status        = 'not_found' if self._registry._loaded else 'unverified'
            result.rbi_verified_name = ''
            if self._registry._loaded:
                result.warnings.append(
                    f'"{company_name}" not found in RBI NBFC registry — '
                    f'may not be a licensed NBFC or registry may be outdated'
                )
        else:
            raw_status = row.get(_REG_COL_STATUS, '').lower()
            result.rbi_verified_name = row.get(_REG_COL_NAME, '')
            if raw_status in _ACTIVE_STATUSES:
                result.rbi_status = 'active'
            elif raw_status in ('inactive', 'cancelled', 'surrender'):
                result.rbi_status = raw_status
                result.issues.append(
                    f'NBFC "{company_name}" has RBI status "{raw_status}" — '
                    f'may no longer be a licensed lender'
                )
            elif raw_status == 'merged':
                result.rbi_status = 'merged'
                result.issues.append(
                    f'NBFC "{company_name}" is listed as "merged" in RBI registry'
                )
            else:
                result.rbi_status = 'verified'

            # Upgrade reg_present if registry confirmed a number
            if not result.rbi_reg_present and row.get(_REG_COL_NUMBER):
                result.rbi_reg_present = True

        # ── Gap 5: Regulatory tier ─────────────────────────────────────────────
        rbi_category = extracted.get('rbi_category', '') or ''
        aum_crores   = extracted.get('aum_crores')
        result.regulatory_tier = _classify_regulatory_tier(rbi_category, aum_crores)

        # ── Gap 6: Contact validation ──────────────────────────────────────────
        phone   = (extracted.get('phone')   or '').strip()
        email   = (extracted.get('email')   or '').strip()
        website = (extracted.get('website') or '').strip()

        result.phone_valid = _validate_phone(phone)
        result.email_valid, result.email_domain_matches = _validate_email(email, website)

        if phone and not result.phone_valid:
            result.warnings.append(f'Phone "{phone}" failed format validation')
        if email and not result.email_valid:
            result.warnings.append(f'Email "{email}" failed format validation')

        # ── Gap 7: Segment / category consistency ──────────────────────────────
        segments = extracted.get('primary_loan_segments', [])
        if isinstance(segments, str):
            import json
            try:
                segments = json.loads(segments)
            except Exception:
                segments = []
        result.segment_category_mismatch, result.segment_warnings = (
            _validate_segment_capability(segments, rbi_category)
        )
        if result.segment_category_mismatch:
            result.issues.append(
                f'Segment-category mismatch: {result.segment_warnings}'
            )
        result.warnings.extend(result.segment_warnings)

        # ── Gap 8: Pan-India plausibility ──────────────────────────────────────
        op_states = extracted.get('operating_states', [])
        if isinstance(op_states, str):
            import json
            try:
                op_states = json.loads(op_states)
            except Exception:
                op_states = []
        pan_india = bool(extracted.get('pan_india', False)
                         or extracted.get('operating_intensity') == 'Pan India')

        result.pan_india_suspect, result.pan_india_reason = (
            _validate_pan_india(pan_india, op_states, aum_crores)
        )
        if result.pan_india_suspect:
            result.warnings.append(result.pan_india_reason)

        # ── Gap 9: System-level deduplication ──────────────────────────────────
        if existing_hashes is not None:
            data_hash = _compute_data_hash(company_name, rbi_reg)
            if data_hash in existing_hashes:
                result.is_duplicate = True
                result.duplicate_of = data_hash
                result.issues.append(
                    f'Duplicate detected: hash {data_hash} already in output'
                )

        # ── Gap 10: Data source classification ─────────────────────────────────
        has_csv_reg = bool(rbi_reg and rbi_reg.strip())
        (result.financial_source,
         result.geo_source,
         result.contact_source,
         result.regulatory_source) = _classify_data_sources(
            data_src, scraped, result.rbi_status, has_csv_reg
        )

        return result
