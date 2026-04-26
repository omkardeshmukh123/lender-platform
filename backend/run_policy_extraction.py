"""
run_policy_extraction.py
========================
POLICY-LEVEL extraction — moves beyond bank-level data.

What this extracts (per lender, per product):
  - loan_amount_min / max        (eligibility hard filter)
  - credit_score_min             (eligibility hard filter)
  - employment_types accepted    (salaried / self-employed / business)
  - interest_rate_min / max      (borrower comparison)
  - tenure range                 (months)
  - collateral required?
  - eligible states
  - special eligibility notes    (GSTIN required, ITR 2 years, etc.)

Pipeline per lender:
  Phase 1 → scrape website → find product/loan pages
  Phase 2 → Gemini extracts policies[] array
  Phase 3 → guardrails validates + scores each policy
  Phase 4 → upload to Supabase policies table (pending approval)

Input:  data/output/nbfc_extracted_verified.csv  (or rbi_banks_extracted_v8.csv)
Output: data/output/policies_extracted.csv
        Supabase: policies table (approval_status = 'pending')
"""

import os, csv, json, time, sys, re, logging
from pathlib import Path
from typing  import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime    import datetime
from collections import Counter
import requests

# ── env loading ───────────────────────────────────────────────
_ENV_FILE = Path(__file__).resolve().parent.parent / '.env'
try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE, override=False)
except ImportError:
    if _ENV_FILE.exists():
        with open(_ENV_FILE, encoding='utf-8') as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _sep, _rest = _line.partition('=')
                    _k = _k.strip(); _v = _rest.strip().strip('"').strip("'")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════

ROOT          = Path(__file__).parent.parent
INPUT_CSV     = ROOT / 'data' / 'output' / 'nbfc_extracted_verified.csv'
RBI_CSV       = ROOT / 'data' / 'output' / 'rbi_banks_extracted_v8.csv'
OUTPUT_DIR    = ROOT / 'data' / 'output'
OUTPUT        = OUTPUT_DIR / 'policies_extracted.csv'
CHECKPOINT    = OUTPUT_DIR / '.policy_checkpoint.json'

GEMINI_KEY    = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_URL    = 'https://generativelanguage.googleapis.com/v1beta/models'
GEMINI_MODEL  = 'gemini-2.5-flash'

SUPABASE_URL  = os.getenv('SUPABASE_URL', '').strip()
SUPABASE_KEY  = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Supabase client (optional — falls back to CSV-only if not configured) ──
_supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as _e:
        print(f"⚠️  Supabase client unavailable ({_e}). Will write CSV only.")


# Columns that actually exist in the policies DB table.
# The Policy dataclass has extra fields (lender_name, extraction_timestamp)
# used only for the CSV output — sending them to Supabase causes upsert errors.
_DB_POLICY_COLS = {
    'lender_id', 'product_name', 'loan_type',
    'loan_amount_min', 'loan_amount_max',
    'credit_score_min', 'credit_score_max',
    'min_age', 'max_age', 'employment_types',
    'min_monthly_income', 'min_annual_turnover', 'min_business_vintage',
    'interest_rate_min', 'interest_rate_max',
    'tenure_min', 'tenure_max', 'processing_fee', 'prepayment_allowed',
    'collateral_required', 'collateral_types',
    'eligible_states', 'eligibility_notes',
    'completeness_score', 'data_source', 'source_url',
    'approval_status',
    # audit / lineage (migration 018) — is_verified excluded until column added
    'source_confidence', 'version',
    # dedup + review workflow (migration 019) — anomaly_flags excluded until column added
    'product_name_normalized', 'review_priority',
    # KYC / documentation (migration 020)
    'kyc_pan_required', 'kyc_aadhaar_required', 'kyc_gstin_required', 'kyc_itr_years',
}


_ARRAY_COLS = {'employment_types', 'collateral_types', 'eligible_states'}


def _to_db_row(policy_dict: dict) -> dict:
    """Strip fields not in the DB schema and coerce array columns to Python lists."""
    row = {}
    for k, v in policy_dict.items():
        if k not in _DB_POLICY_COLS:
            continue
        if k in _ARRAY_COLS:
            if isinstance(v, list):
                row[k] = v
            elif v and v not in ('[]', 'null', 'None'):
                try:
                    parsed = json.loads(v)
                    row[k] = parsed if isinstance(parsed, list) else []
                except Exception:
                    row[k] = []
            else:
                row[k] = []
        else:
            # DB CHECK constraint allows: pending/approved/rejected/needs_update
            # Extraction uses 'needs_review' — map it to 'pending'
            if k == 'approval_status' and v == 'needs_review':
                row[k] = 'pending'
            else:
                row[k] = v
    return row


def upload_policies_to_supabase(policies: list) -> int:
    """
    Upsert policies to Supabase.
    Uses ON CONFLICT (lender_id, product_name, loan_type) to avoid duplicates.
    Strips non-DB fields (lender_name, extraction_timestamp) before upload.
    Returns count of records upserted.
    """
    if not _supabase or not policies:
        return 0

    BATCH = 100
    total = 0
    for i in range(0, len(policies), BATCH):
        batch = [_to_db_row(p) for p in policies[i : i + BATCH]]
        try:
            _supabase.table("policies").upsert(
                batch,
                on_conflict="lender_id,product_name_normalized,loan_type",
            ).execute()
            total += len(batch)
        except Exception as exc:
            logging.error(f"Supabase upsert failed for batch {i//BATCH}: {exc}")
    return total

MAX_RETRIES       = 3
RETRY_DELAY       = 3
RATE_LIMIT_DELAY  = 4.0
MAX_PER_MINUTE    = 14

# Completeness threshold — soft only, never hard reject
# Below this → needs_review + review_priority='high'; still stored
MIN_COMPLETENESS_REVIEW = 0.60

# ── Scraper (optional, for website context) ───────────────────
_scraper = None
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from scraper.lender_scraper import SingleLenderScraper
    _scraper = SingleLenderScraper(use_stealth=False)
except Exception:
    pass

# ══════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════

LOG_DIR = ROOT / 'logs'

def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime('%Y%m%d_%H%M%S')
    log = LOG_DIR / f'policy_extraction_{ts}.log'
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        handlers=[
            logging.FileHandler(log, encoding='utf-8'),
            logging.StreamHandler(),
        ]
    )
    logging.getLogger('scrapling').setLevel(logging.WARNING)
    logging.getLogger('curl_cffi').setLevel(logging.WARNING)
    return log

# ══════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════

CANONICAL_LOAN_TYPES = [
    'MSME Loan', 'Personal Loan', 'Home Loan', 'Business Loan',
    'Vehicle Loan', 'Gold Loan', 'Education Loan', 'Micro Loan',
    'Loan Against Property', 'Working Capital', 'Agriculture Loan',
    'Credit Card', 'Consumer Durable Loan', 'EV Loan',
    'Two Wheeler Loan', 'Microfinance', 'Rural Loan', 'Supply Chain Finance',
]

VALID_EMPLOYMENT_TYPES = [
    # Granular (preferred — instruct Gemini to use these)
    'salaried_govt',
    'salaried_private',
    'salaried_psu',
    'self_employed_professional',    # CA, doctor, architect, lawyer
    'self_employed_non_professional', # trader, contractor, retailer
    # Legacy broad categories (accepted for backward compat + Gemini fallback)
    'salaried', 'self-employed', 'business',
    # Other
    'agriculture', 'student', 'nri',
]

# FOIR (Fixed Obligation to Income Ratio) thresholds
# Computed against worst-case: max loan at min rate for max tenure vs min income
FOIR_WARN_THRESHOLD    = 0.65   # > 65% → anomaly flag
FOIR_CRITICAL_THRESHOLD = 1.00  # > 100% → mathematically impossible to repay

VALID_COLLATERAL_TYPES = [
    'property', 'gold', 'fdr', 'stocks', 'vehicle', 'machinery', 'none'
]

# Expected interest rate ranges per loan type (min%, max%).
# Used for anomaly flagging only — outside range → review_priority='high', not rejection.
# Source: RBI publications + industry norms (April 2026)
LOAN_TYPE_RATE_RANGES: Dict[str, Tuple[float, float]] = {
    'Home Loan':              (7.0,  15.0),
    'Loan Against Property':  (8.0,  20.0),
    'Education Loan':         (7.0,  20.0),
    'Gold Loan':              (7.0,  30.0),
    'Vehicle Loan':           (7.0,  25.0),
    'Two Wheeler Loan':       (8.0,  28.0),
    'EV Loan':                (7.0,  22.0),
    'Personal Loan':          (10.0, 36.0),
    'Consumer Durable Loan':  (0.0,  30.0),   # zero-cost EMI schemes exist
    'MSME Loan':              (10.0, 30.0),
    'Business Loan':          (10.0, 32.0),
    'Working Capital':        (10.0, 30.0),
    'Supply Chain Finance':   (8.0,  26.0),
    'Agriculture Loan':       (4.0,  18.0),
    'Rural Loan':             (10.0, 28.0),
    'Micro Loan':             (18.0, 30.0),
    'Microfinance':           (18.0, 30.0),
    'Credit Card':            (24.0, 48.0),
}

# MFI ticket size cap per RBI norms (₹ Lakhs)
MFI_MAX_TICKET_LAKHS = 3.0

ALL_INDIA_STATES = [
    'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
    'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka',
    'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
    'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana',
    'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal', 'Delhi',
    'Jammu & Kashmir', 'Ladakh', 'Puducherry', 'Chandigarh',
    'Dadra and Nagar Haveli', 'Lakshadweep', 'Andaman and Nicobar Islands',
]

# ══════════════════════════════════════════════════════════════
# DATA MODEL
# ══════════════════════════════════════════════════════════════

@dataclass
class Policy:
    # Links
    lender_id:             int
    lender_name:           str

    # Product identity
    product_name:          str = ''
    loan_type:             str = ''

    # Loan amount (Lakhs)
    loan_amount_min:       Optional[float] = None
    loan_amount_max:       Optional[float] = None

    # Borrower credit profile
    credit_score_min:      Optional[int]   = None
    credit_score_max:      Optional[int]   = None

    # Borrower demographics
    min_age:               Optional[int]   = None
    max_age:               Optional[int]   = None
    employment_types:      str             = '[]'  # JSON array

    # Income / financials
    min_monthly_income:    Optional[float] = None  # ₹ thousands
    min_annual_turnover:   Optional[float] = None  # ₹ Lakhs
    min_business_vintage:  Optional[int]   = None  # years

    # Loan terms
    interest_rate_min:     Optional[float] = None
    interest_rate_max:     Optional[float] = None
    tenure_min:            Optional[int]   = None  # months
    tenure_max:            Optional[int]   = None
    processing_fee:        Optional[float] = None  # %
    prepayment_allowed:    Optional[bool]  = None

    # Collateral
    collateral_required:   Optional[bool]  = None  # None = unknown (not extracted)
    collateral_types:      str             = '[]'  # JSON array

    # Geography
    eligible_states:       str             = '[]'  # JSON array

    # Notes
    eligibility_notes:     str             = ''

    # KYC / documentation requirements (structured — not free text)
    kyc_pan_required:      Optional[bool]  = None
    kyc_aadhaar_required:  Optional[bool]  = None
    kyc_gstin_required:    Optional[bool]  = None   # for business/MSME loans
    kyc_itr_years:         Optional[int]   = None   # number of ITR years required

    # Metadata
    completeness_score:      float         = 0.0
    data_source:             str           = 'gemini'
    source_url:              str           = ''
    approval_status:         str           = 'pending'
    # Audit / lineage
    source_confidence:       str           = 'low'   # low / medium / high
    version:                 int           = 1
    is_verified:             bool          = False
    # Deduplication + review workflow
    product_name_normalized: str           = ''
    review_priority:         str           = 'low'   # low / medium / high
    anomaly_flags:           str           = '[]'    # JSON array of flag strings
    extraction_timestamp:    str           = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    def compute_completeness(self) -> float:
        """Fraction of 10 key policy fields that are filled."""
        fields = [
            self.loan_amount_min, self.loan_amount_max,
            self.credit_score_min,
            self.interest_rate_min, self.interest_rate_max,
            self.tenure_min, self.tenure_max,
            self.employment_types if self.employment_types != '[]' else None,
            self.eligibility_notes if self.eligibility_notes else None,
            self.eligible_states   if self.eligible_states   != '[]' else None,
        ]
        filled = sum(1 for f in fields if f is not None)
        return round(filled / len(fields), 2)


# ══════════════════════════════════════════════════════════════
# GEMINI PROMPT
# ══════════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION = """\
You are a financial data extraction engine operating inside a production pipeline.

ROLE:
- Extract policy-level lending criteria from publicly available sources
- Each policy = one loan product variant with its eligibility conditions
- You are filling a fixed schema — not describing or summarising

STRICT RULES:
1. Return null for any field you cannot verify with high confidence
2. Do NOT guess interest rates, credit scores, or income requirements
3. Do NOT hallucinate product names, eligibility criteria, or state lists
4. Interest rates in % per annum (e.g. 14.5 not 0.145)
5. Loan amounts in Lakhs (e.g. 5 = ₹5 Lakhs, 500 = ₹500 Lakhs = ₹5 Crore)
6. employment_types — use granular types where possible:
   salaried_govt / salaried_private / salaried_psu /
   self_employed_professional / self_employed_non_professional /
   business / agriculture / student / nri.
   Use legacy 'salaried' or 'self-employed' only when you cannot determine the sub-type.
7. Return one policy object per distinct product variant
8. If a lender has 3 products, return 3 policy objects

FAILURE HANDLING:
- Field not found → set to null
- Entire product unknown → omit from policies array
- Never break JSON format

OUTPUT:
- Return ONLY valid JSON — no markdown, no explanation
- JSON must have exactly one key: "policies" (array)
"""


def make_policy_prompt(lender_name: str, company_type: str, website: str,
                       products: List[str], scraped_context: dict = None) -> str:
    types_str = ', '.join(f'"{t}"' for t in CANONICAL_LOAN_TYPES)
    emp_str   = ', '.join(f'"{e}"' for e in VALID_EMPLOYMENT_TYPES)

    scraped_block = ''
    if scraped_context:
        tags   = scraped_context.get('loan_tags', [])
        states = scraped_context.get('operating_states', [])
        lines  = []
        if tags:   lines.append(f'  loan products found on website: {tags}')
        if states: lines.append(f'  operating states found: {states[:10]}')
        if lines:
            scraped_block = '\nScrape context (factual):\n' + '\n'.join(lines)

    products_hint = ', '.join(products) if products else 'unknown'

    return f"""Extract lending policies for: {lender_name}
Type: {company_type}
Website: {website or 'unknown'}
Known products: {products_hint}{scraped_block}

Return JSON with a "policies" array. Each policy object:
{{
  "product_name": "specific product name e.g. MSME Term Loan - Unsecured",
  "loan_type": one of [{types_str}],
  "loan_amount_min": number in Lakhs | null,
  "loan_amount_max": number in Lakhs | null,
  "credit_score_min": integer 300-900 | null,
  "credit_score_max": integer | null,
  "min_age": integer | null,
  "max_age": integer | null,
  "employment_types": array from [{emp_str}] | [],
  "min_monthly_income": number in thousands INR | null,
  "min_annual_turnover": number in Lakhs | null,
  "min_business_vintage": years | null,
  "interest_rate_min": % per annum | null,
  "interest_rate_max": % per annum | null,
  "tenure_min": months | null,
  "tenure_max": months | null,
  "processing_fee": % | null,
  "prepayment_allowed": true | false | null,
  "collateral_required": true | false | null,
  "collateral_types": array | [],
  "eligible_states": [full Indian state names] | null,
  "eligibility_notes": "free text: other special conditions" | null,
  "kyc_pan_required": true | false | null,
  "kyc_aadhaar_required": true | false | null,
  "kyc_gstin_required": true | false | null,
  "kyc_itr_years": integer (e.g. 2) | null
}}

Return: {{"policies": [...]}}"""


# ══════════════════════════════════════════════════════════════
# GEMINI API
# ══════════════════════════════════════════════════════════════

def call_gemini(prompt: str, rate_limiter) -> Optional[dict]:
    if not GEMINI_KEY:
        logging.error("GEMINI_API_KEY not set")
        return None

    rate_limiter.wait()
    url     = f"{GEMINI_URL}/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents":          [{"parts": [{"text": prompt}]}],
        "generationConfig":  {
            "temperature":    0,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",  # force structured JSON output
        },
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code == 429:
                rate_limiter.backoff(90)
                continue
            if resp.status_code in (500, 503):
                time.sleep(RETRY_DELAY * attempt)
                continue
            if resp.status_code != 200:
                logging.error(f"HTTP {resp.status_code}: {resp.text[:100]}")
                time.sleep(RETRY_DELAY)
                continue

            candidates = resp.json().get('candidates', [])
            if not candidates:
                continue
            raw = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            if not raw:
                continue

            return _parse_json(raw)

        except Exception as e:
            logging.warning(f"  Attempt {attempt}: {e}")
            time.sleep(RETRY_DELAY * attempt)

    return None


def _parse_json(text: str) -> Optional[dict]:
    text = text.strip()
    # strip markdown fences
    text = re.sub(r'```json\s*\n?', '', text)
    text = re.sub(r'```\s*\n?', '',   text)
    text = text.strip()

    for attempt_text in [text, re.sub(r',\s*([}\]])', r'\1', text)]:
        try:
            return json.loads(attempt_text)
        except Exception:
            pass

    # brace-match
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0: start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start != -1:
                try:    return json.loads(text[start:i+1])
                except: break

    logging.warning("JSON parse failed")
    return None


# ══════════════════════════════════════════════════════════════
# RATE LIMITER
# ══════════════════════════════════════════════════════════════

class RateLimiter:
    def __init__(self):
        self.requests     = []
        self.backoff_until = None

    def wait(self):
        now = time.time()
        if self.backoff_until and now < self.backoff_until:
            time.sleep(self.backoff_until - now)
            self.backoff_until = None
        self.requests = [t for t in self.requests if now - t < 60]
        if len(self.requests) >= MAX_PER_MINUTE:
            time.sleep(60 - (now - self.requests[0]) + 1)
            self.requests = []
        time.sleep(RATE_LIMIT_DELAY)
        self.requests.append(time.time())

    def backoff(self, secs: int = 60):
        self.backoff_until = time.time() + secs
        logging.warning(f"Rate limit backoff: {secs}s")


# ══════════════════════════════════════════════════════════════
# POLICY BUILDER
# ══════════════════════════════════════════════════════════════

def validate_policy_logic(p: 'Policy') -> Tuple[bool, str]:
    """
    Cross-field consistency checks.  Returns (ok, reason).
    Rejects policies with logically impossible range pairs or
    unrealistic values for the Indian retail lending market.
    """
    # Range consistency — min must not exceed max
    if p.loan_amount_min is not None and p.loan_amount_max is not None:
        if p.loan_amount_min > p.loan_amount_max:
            return False, f'loan_amount_min {p.loan_amount_min} > max {p.loan_amount_max}'

    if p.interest_rate_min is not None and p.interest_rate_max is not None:
        if p.interest_rate_min > p.interest_rate_max:
            return False, f'interest_rate_min {p.interest_rate_min} > max {p.interest_rate_max}'

    if p.tenure_min is not None and p.tenure_max is not None:
        if p.tenure_min > p.tenure_max:
            return False, f'tenure_min {p.tenure_min} > max {p.tenure_max}'

    if p.credit_score_min is not None and p.credit_score_max is not None:
        if p.credit_score_min > p.credit_score_max:
            return False, f'credit_score_min {p.credit_score_min} > max {p.credit_score_max}'

    if p.min_age is not None and p.max_age is not None:
        if p.min_age > p.max_age:
            return False, f'min_age {p.min_age} > max_age {p.max_age}'

    # Realistic floor for India retail lending (RBI regulated).
    # Exceptions: Consumer Durable Loan (zero-cost EMI) and Agriculture Loan
    # (government-subsidised Kisan Credit Card / subvention schemes can reach 4%).
    if (p.interest_rate_min is not None
            and p.interest_rate_min < 4.0
            and p.loan_type not in ('Consumer Durable Loan', 'Agriculture Loan')):
        return False, f'interest_rate_min {p.interest_rate_min}% unrealistic for India'

    # NOTE: missing underwriting signals (no rate/score/tenure) is NOT a hard reject.
    # Indian NBFCs routinely omit these. Policies lacking them are downgraded to
    # source_confidence='low' + needs_review in build_policies() instead.

    return True, ''


def compute_foir_estimate(loan_amount_max: Optional[float],
                          interest_rate_min: Optional[float],
                          tenure_max: Optional[int],
                          min_monthly_income: Optional[float]) -> Optional[float]:
    """
    Estimate worst-case FOIR: maximum loan at minimum rate over maximum tenure
    against minimum qualifying income.  Returns ratio (e.g. 0.42 = 42%) or None.

    Units: loan_amount in Lakhs, income in ₹ thousands, tenure in months, rate in % p.a.
    """
    if any(v is None for v in (loan_amount_max, interest_rate_min,
                               tenure_max, min_monthly_income)):
        return None
    if min_monthly_income <= 0 or tenure_max <= 0:
        return None

    principal   = loan_amount_max * 100_000          # Lakhs → ₹
    monthly_r   = interest_rate_min / 100.0 / 12.0   # % p.a. → monthly decimal
    income_rs   = min_monthly_income * 1_000          # ₹ thousands → ₹

    if monthly_r == 0:
        emi = principal / tenure_max
    else:
        emi = (principal * monthly_r * (1 + monthly_r) ** tenure_max
               / ((1 + monthly_r) ** tenure_max - 1))

    return round(emi / income_rs, 3)


def _safe_float(v) -> Optional[float]:
    if v is None or v == '': return None
    try:
        s = str(v).replace(',', '').replace('₹', '').strip()
        x = float(re.sub(r'[^\d.]', '', s))
        return x if s else None
    except: return None

def _safe_int(v) -> Optional[int]:
    if v is None or v == '': return None
    try:   return int(float(str(v).replace(',', '')))
    except: return None

def _valid_states(states) -> List[str]:
    if not isinstance(states, list): return []
    canon = {s.lower(): s for s in ALL_INDIA_STATES}
    return [canon.get(s.strip().lower(), '') for s in states
            if canon.get(s.strip().lower())]

def _valid_list(items, allowed: List[str]) -> List[str]:
    if not isinstance(items, list): return []
    lo = {a.lower(): a for a in allowed}
    return [lo[i.strip().lower()] for i in items if i.strip().lower() in lo]


def is_minimum_viable_policy(p: 'Policy') -> Tuple[bool, str]:
    """
    Gate 3: always passes. loan_type is already validated as canonical before
    this point. Whatever data Gemini or heuristic extracted is stored as-is.
    Missing fields are expected for Indian NBFC websites. Data quality is
    handled by confidence scoring and needs_review tagging, not rejection.
    """
    return True, ''


def validate_by_lender_type(p: 'Policy', company_type: str) -> Tuple[bool, str]:
    """
    Company-type-specific hard rules.
    Returns (ok, reason); False = reject the policy.
    """
    ct = company_type.upper() if company_type else ''

    is_mfi = 'MICROFINANCE' in ct or ct == 'MFI' or 'NBFC-MFI' in ct

    if is_mfi:
        # RBI MFI norms: individual household loan ≤ ₹3L, income ≤ ₹3L p.a.
        if p.loan_amount_max and p.loan_amount_max > MFI_MAX_TICKET_LAKHS:
            return False, (f'MFI loan_amount_max {p.loan_amount_max}L '
                           f'exceeds RBI MFI cap of {MFI_MAX_TICKET_LAKHS}L')
        if p.collateral_required is True:
            return False, 'MFI collateral_required=True contradicts RBI MFI norms'

    return True, ''


def flag_anomalies(p: 'Policy') -> List[str]:
    """
    Soft checks — policy is not rejected but review_priority is raised.
    Returns a list of human-readable flag strings (empty = clean).
    """
    flags: List[str] = []

    # Large loan (≥₹1 Cr) with no income requirement whatsoever
    if (p.loan_amount_min is not None and p.loan_amount_min >= 100
            and p.min_monthly_income is None
            and p.min_annual_turnover is None):
        flags.append(
            f'large_loan_no_income: ₹{p.loan_amount_min}L min with no income req'
        )

    # Contradictory: very low credit score + unusually low rate
    if (p.credit_score_min is not None and p.credit_score_min <= 600
            and p.interest_rate_min is not None and p.interest_rate_min < 10.0):
        flags.append(
            f'low_score_low_rate: score≤{p.credit_score_min} '
            f'but rate={p.interest_rate_min}% (atypical)'
        )

    # Implausibly wide rate band (marketing page, not real policy)
    if (p.interest_rate_min is not None and p.interest_rate_max is not None
            and (p.interest_rate_max - p.interest_rate_min) > 20):
        flags.append(
            f'wide_rate_band: {p.interest_rate_min}–{p.interest_rate_max}% '
            f'(spread {p.interest_rate_max - p.interest_rate_min:.1f}%)'
        )

    # Unusually long tenure for short-term product types
    short_term_types = {'Personal Loan', 'Gold Loan', 'Consumer Durable Loan',
                        'Two Wheeler Loan', 'Credit Card'}
    if p.loan_type in short_term_types and p.tenure_max and p.tenure_max > 84:
        flags.append(
            f'unusual_tenure: {p.loan_type} tenure_max={p.tenure_max}m '
            f'(>{84}m unusual)'
        )

    # Rate outside expected range for loan type
    rate_range = LOAN_TYPE_RATE_RANGES.get(p.loan_type)
    if rate_range and p.interest_rate_min is not None:
        lo, hi = rate_range
        if p.interest_rate_min < lo or p.interest_rate_min > hi:
            flags.append(
                f'rate_outside_norm: {p.loan_type} rate_min={p.interest_rate_min}% '
                f'(expected {lo}–{hi}%)'
            )

    # Implausible amount range ratio (likely generic marketing copy)
    if (p.loan_amount_min is not None and p.loan_amount_max is not None
            and p.loan_amount_min > 0):
        ratio = p.loan_amount_max / p.loan_amount_min
        if ratio > 1000:
            flags.append(
                f'implausible_amount_range: {p.loan_amount_min}–{p.loan_amount_max}L '
                f'({ratio:.0f}x spread)'
            )

    # Hallucination risk: both rate bounds are common Gemini defaults with no scraper backup
    _ROUND_RATES = {8.0, 10.0, 12.0, 14.0, 15.0, 18.0, 20.0, 24.0, 36.0}
    if (p.interest_rate_min is not None
            and p.interest_rate_max is not None
            and p.interest_rate_min in _ROUND_RATES
            and p.interest_rate_max in _ROUND_RATES
            and p.source_confidence == 'low'):
        flags.append(
            f'rate_possibly_guessed: both rate bounds are common round values '
            f'({p.interest_rate_min}–{p.interest_rate_max}%) with no scraper corroboration'
        )

    # Hallucination risk: credit score is a common Gemini default, no scraper backup
    _COMMON_CREDIT_DEFAULTS = {600, 650, 700, 750}
    if (p.credit_score_min is not None
            and p.credit_score_min in _COMMON_CREDIT_DEFAULTS
            and p.source_confidence == 'low'):
        flags.append(
            f'credit_score_possibly_guessed: credit_score_min={p.credit_score_min} '
            f'is a common default value with no scraper corroboration'
        )

    # FOIR feasibility check
    foir = compute_foir_estimate(
        p.loan_amount_max, p.interest_rate_min, p.tenure_max, p.min_monthly_income
    )
    if foir is not None:
        if foir > FOIR_CRITICAL_THRESHOLD:
            # Note: foir_impossible is intentionally NOT flagged here —
            # it is a hard gate inside build_policies(), never reaches flag_anomalies.
            pass
        elif foir > FOIR_WARN_THRESHOLD:
            flags.append(
                f'foir_high: FOIR={foir:.0%} exceeds {FOIR_WARN_THRESHOLD:.0%} '
                f'threshold (max loan={p.loan_amount_max}L, '
                f'income=₹{p.min_monthly_income}k/mo)'
            )

    return flags


def _normalize_product_name(name: str) -> str:
    """Collapse cosmetic differences so 'MSME Loan' and 'MSME  loan' deduplicate."""
    return re.sub(r'\s+', ' ', name.lower().replace('-', ' ')).strip()


def build_policies(lender_id: int, lender_name: str, company_type: str,
                   gemini_resp: dict,
                   scraped_context: Optional[dict] = None,
                   rejections: Optional[list] = None,
                   data_source: str = 'gemini') -> List[Policy]:
    """
    scraped_context  — output from SingleLenderScraper; used to upgrade source_confidence.
    rejections       — caller-supplied list; each rejected policy appends a dict
                       explaining which gate failed (full audit trail).
    data_source      — 'gemini' (default) or 'heuristic' (rule-based fallback).
    """
    raw_policies = gemini_resp.get('policies', [])
    if not isinstance(raw_policies, list):
        return []

    # Source confidence: upgrade from 'low' if scraper confirmed product types
    scraper_confirmed = bool(
        scraped_context
        and scraped_context.get('loan_tags')
        and len(scraped_context['loan_tags']) > 0
    )

    result      = []
    seen_keys: set = set()   # dedup within a single Gemini response

    def _reject(product: str, gate: str, reason: str):
        logging.debug(f"  REJECTED '{product}' [{gate}]: {reason}")
        if rejections is not None:
            rejections.append({
                'lender_id':    lender_id,
                'lender_name':  lender_name,
                'product_name': product,
                'gate':         gate,
                'reason':       reason,
                'timestamp':    datetime.now().isoformat(),
            })

    for raw in raw_policies:
        if not isinstance(raw, dict):
            continue

        loan_type = str(raw.get('loan_type', '')).strip()
        if loan_type not in CANONICAL_LOAN_TYPES:
            _reject(str(raw.get('product_name', '?')), 'loan_type',
                    f'unknown loan_type: {loan_type!r}')
            continue

        product_name = str(raw.get('product_name', loan_type)).strip()[:200]

        # Intra-response deduplication using normalized key
        dedup_key = (lender_id, _normalize_product_name(product_name), loan_type)
        if dedup_key in seen_keys:
            logging.debug(f"  Duplicate policy skipped: {product_name}")
            continue
        seen_keys.add(dedup_key)

        emp_types  = _valid_list(raw.get('employment_types', []), VALID_EMPLOYMENT_TYPES)
        coll_types = _valid_list(raw.get('collateral_types',  []), VALID_COLLATERAL_TYPES)
        states     = _valid_states(raw.get('eligible_states') or [])

        # credit score sanity check (300–900)
        cs_min = _safe_int(raw.get('credit_score_min'))
        if cs_min is not None and not (300 <= cs_min <= 900):
            cs_min = None
        cs_max = _safe_int(raw.get('credit_score_max'))
        if cs_max is not None and not (300 <= cs_max <= 900):
            cs_max = None

        # interest rate sanity (1–60%)
        ir_min = _safe_float(raw.get('interest_rate_min'))
        ir_max = _safe_float(raw.get('interest_rate_max'))
        if ir_min is not None and not (1.0 <= ir_min <= 60.0): ir_min = None
        if ir_max is not None and not (1.0 <= ir_max <= 60.0): ir_max = None

        # tenure sanity (1–600 months = 50 years)
        t_min = _safe_int(raw.get('tenure_min'))
        t_max = _safe_int(raw.get('tenure_max'))
        if t_min is not None and not (1 <= t_min <= 600): t_min = None
        if t_max is not None and not (1 <= t_max <= 600): t_max = None

        # loan amount sanity (0.1 Lakh – 100,000 Lakh = ₹1000 Cr)
        la_min = _safe_float(raw.get('loan_amount_min'))
        la_max = _safe_float(raw.get('loan_amount_max'))
        if la_min is not None and not (0.1 <= la_min <= 100_000): la_min = None
        if la_max is not None and not (0.1 <= la_max <= 100_000): la_max = None

        # collateral_required: None means "not extracted" — never default to False
        raw_collateral = raw.get('collateral_required')
        collateral_required = bool(raw_collateral) if raw_collateral is not None else None

        # KYC / documentation requirements (structured extraction)
        def _safe_bool_field(key: str) -> Optional[bool]:
            v = raw.get(key)
            return bool(v) if v is not None else None

        p = Policy(
            lender_id=lender_id,
            lender_name=lender_name,
            product_name=product_name,
            loan_type=loan_type,
            loan_amount_min=la_min,
            loan_amount_max=la_max,
            credit_score_min=cs_min,
            credit_score_max=cs_max,
            min_age=_safe_int(raw.get('min_age')),
            max_age=_safe_int(raw.get('max_age')),
            employment_types=json.dumps(emp_types),
            min_monthly_income=_safe_float(raw.get('min_monthly_income')),
            min_annual_turnover=_safe_float(raw.get('min_annual_turnover')),
            min_business_vintage=_safe_int(raw.get('min_business_vintage')),
            interest_rate_min=ir_min,
            interest_rate_max=ir_max,
            tenure_min=t_min,
            tenure_max=t_max,
            processing_fee=_safe_float(raw.get('processing_fee')),
            prepayment_allowed=raw.get('prepayment_allowed'),
            collateral_required=collateral_required,
            collateral_types=json.dumps(coll_types),
            eligible_states=json.dumps(states) if states else '[]',
            eligibility_notes=str(raw.get('eligibility_notes', '') or '')[:500],
            kyc_pan_required=_safe_bool_field('kyc_pan_required'),
            kyc_aadhaar_required=_safe_bool_field('kyc_aadhaar_required'),
            kyc_gstin_required=_safe_bool_field('kyc_gstin_required'),
            kyc_itr_years=_safe_int(raw.get('kyc_itr_years')),
            data_source=data_source,
            # Source confidence hierarchy:
            # 'medium' if scraper confirmed product types (independent corroboration)
            # 'low'    if Gemini only or heuristic rule-based fallback
            # 'high'   reserved for manual / PDF-sourced entries (future)
            source_confidence='medium' if (scraper_confirmed and data_source == 'gemini') else 'low',
            approval_status='pending',
        )
        p.completeness_score      = p.compute_completeness()
        p.product_name_normalized = _normalize_product_name(product_name)

        # ── Gate 1: cross-field range consistency + IR floor ─────────────────
        ok, reason = validate_policy_logic(p)
        if not ok:
            _reject(product_name, 'logic', reason)
            continue

        # ── Gate 2: company-type hard rules (MFI caps, etc.) ─────────────────
        ok, reason = validate_by_lender_type(p, company_type)
        if not ok:
            _reject(product_name, 'lender_type', reason)
            continue

        # ── Gate 3: minimum viable policy ────────────────────────────────────
        # Heuristic stubs pass unconditionally — they carry at minimum a valid
        # loan_type which is enough for coarse borrower matching.
        if data_source != 'heuristic':
            ok, reason = is_minimum_viable_policy(p)
            if not ok:
                _reject(product_name, 'minimum_viable', reason)
                continue

        # ── Post Gate-3: downgrade confidence for missing underwriting signals ─
        # Indian NBFCs routinely omit interest rates, credit scores, and tenure.
        # Don't reject — route to needs_review with low confidence so the
        # matching engine can still use the loan_type / employment_types signals.
        _has_rate  = p.interest_rate_min is not None
        _has_score = p.credit_score_min is not None
        _has_tenure = p.tenure_min is not None or p.tenure_max is not None
        if not _has_rate and not _has_score and not _has_tenure:
            p.source_confidence = 'low'
            p.approval_status   = 'needs_review'

        # ── Gate 4: completeness — tag only, never reject ───────────────────
        # Heuristic stubs and data-sparse Indian NBFC policies have very low
        # completeness scores. Store everything — drop nothing. Tag for review.
        if data_source == 'heuristic' or p.completeness_score < MIN_COMPLETENESS_REVIEW:
            p.approval_status = 'needs_review'
            p.review_priority = 'high'

        # ── Gate 5: FOIR impossibility — policy is internally incoherent ─────
        # If a borrower at the minimum qualifying income takes the maximum loan
        # at the minimum rate for the maximum tenure and FOIR still > 100%, the
        # policy cannot produce any repayable loan. Hard reject.
        foir_check = compute_foir_estimate(
            p.loan_amount_max, p.interest_rate_min, p.tenure_max, p.min_monthly_income
        )
        if foir_check is not None and foir_check > FOIR_CRITICAL_THRESHOLD:
            _reject(product_name, 'foir_impossible',
                    f'FOIR={foir_check:.0%} — borrower at min income '
                    f'(₹{p.min_monthly_income}k/mo) cannot repay '
                    f'max loan (₹{p.loan_amount_max}L) even with full income')
            continue

        # ── Soft: anomaly flagging → sets review_priority ────────────────────
        anomalies = flag_anomalies(p)
        p.anomaly_flags = json.dumps(anomalies)
        _has_hallucination_flag = any(
            'possibly_guessed' in f for f in anomalies
        )
        if anomalies:
            p.review_priority = 'high'
            # Hallucination risk → downgrade confidence regardless of scraper
            if _has_hallucination_flag:
                p.source_confidence = 'low'
        elif p.source_confidence == 'low' and p.completeness_score < 0.75:
            p.review_priority = 'medium'
        else:
            p.review_priority = 'low'

        result.append(p)

    return result


# ══════════════════════════════════════════════════════════════
# CHECKPOINT
# ══════════════════════════════════════════════════════════════

class Checkpoint:
    def __init__(self, path: Path):
        self.path   = path
        self.done   = set()
        self.failed = set()
        self.stats  = Counter()
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text())
                self.done   = set(d.get('done',   []))
                self.failed = set(d.get('failed', []))
                self.stats  = Counter(d.get('stats', {}))
                logging.info(f"Checkpoint: {len(self.done)} lenders already processed")
            except Exception:
                pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {'done': list(self.done), 'failed': list(self.failed), 'stats': dict(self.stats)}, indent=2
        ))

    def mark(self, lid: int, policy_count: int):
        self.done.add(lid)
        self.stats['lenders_done']  += 1
        self.stats['policies_total'] += policy_count
        if len(self.done) % 10 == 0:
            self.save()

    def mark_failed(self, lid: int):
        """Mark a transient failure — excluded from retry on normal restart but
        cleared by remove_failed() when --retry-failed is used."""
        self.done.add(lid)
        if lid not in self.failed:
            self.stats['lenders_failed'] += 1
        self.failed.add(lid)
        if len(self.done) % 10 == 0:
            self.save()

    def remove_failed(self) -> set:
        retry_ids = set(self.failed)
        self.done  -= retry_ids
        self.failed.clear()
        self.stats['lenders_failed'] = 0
        logging.info(f"Retry mode: cleared {len(retry_ids)} failed lender IDs from checkpoint")
        return retry_ids

    def is_done(self, lid: int) -> bool:
        return lid in self.done


# ══════════════════════════════════════════════════════════════
# FILE I/O
# ══════════════════════════════════════════════════════════════

POLICY_TABLE_FIELDS = [
    'lender_id', 'lender_name', 'product_name', 'loan_type',
    'loan_amount_min', 'loan_amount_max',
    'credit_score_min', 'credit_score_max',
    'min_age', 'max_age', 'employment_types',
    'min_monthly_income', 'min_annual_turnover', 'min_business_vintage',
    'interest_rate_min', 'interest_rate_max',
    'tenure_min', 'tenure_max', 'processing_fee', 'prepayment_allowed',
    'collateral_required', 'collateral_types',
    'eligible_states', 'eligibility_notes',
    'completeness_score', 'data_source', 'source_url',
    'approval_status', 'source_confidence', 'version',
    'product_name_normalized', 'review_priority',
    'kyc_pan_required', 'kyc_aadhaar_required', 'kyc_gstin_required', 'kyc_itr_years',
    'extraction_timestamp',
]


def save_policies(policies: List[Policy], path: Path):
    """
    Merge-safe save: loads any existing CSV records, merges with current run's
    policies (keyed by lender_id+product_name+loan_type), then writes the union.
    Safe to call on restart — previous run's data is never lost.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load previously saved policies from disk (survive restarts)
    merged: dict = {}
    if path.exists():
        try:
            with open(path, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    key = (row.get('lender_id', ''), row.get('product_name', ''), row.get('loan_type', ''))
                    merged[key] = row
        except Exception as e:
            logging.warning(f"Could not read existing CSV for merge: {e}")

    # Overwrite with current-run policies (fresher data wins)
    for p in policies:
        d = asdict(p)
        key = (str(d.get('lender_id', '')), d.get('product_name', ''), d.get('loan_type', ''))
        merged[key] = d

    if not merged:
        return

    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=POLICY_TABLE_FIELDS, extrasaction='ignore')
        w.writeheader()
        w.writerows(merged.values())
    tmp.replace(path)
    logging.info(f"Saved {len(merged)} total policies to {path} ({len(policies)} from current run)")


def load_lenders(csv_path: Path) -> List[Dict]:
    if not csv_path.exists():
        logging.error(f"Input not found: {csv_path}")
        return []
    rows = []
    with open(csv_path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            if row.get('extraction_status') == 'success' and row.get('company_name'):
                rows.append(row)
    logging.info(f"Loaded {len(rows)} lenders from {csv_path.name}")
    return rows


# ══════════════════════════════════════════════════════════════
# HEURISTIC FALLBACK EXTRACTOR
# ══════════════════════════════════════════════════════════════

def heuristic_extract(name: str, ctype: str, products: List[str],
                      scraped: dict) -> Optional[dict]:
    """
    Rule-based fallback used when Gemini fails or returns unusable JSON.
    Builds minimal policy stubs from scraped loan_tags and lender metadata.
    Output format mirrors Gemini's response so build_policies() can consume it.
    All numeric fields are left null — policies get source_confidence='low' and
    approval_status='needs_review' automatically in build_policies().
    """
    loan_tags  = list(scraped.get('loan_tags', [])) if scraped else []
    candidates = {t for t in (products + loan_tags) if t in CANONICAL_LOAN_TYPES}

    # Infer from lender name / company type if no products found
    if not candidates:
        name_up  = name.upper()
        ctype_up = (ctype or '').upper()
        if 'MICROFINANCE' in ctype_up or 'NBFC-MFI' in ctype_up or 'MFI' in ctype_up:
            candidates = {'Micro Loan', 'Microfinance'}
        elif 'GOLD' in name_up:
            candidates = {'Gold Loan'}
        elif 'HOUSING' in name_up or 'HOME FINANC' in name_up:
            candidates = {'Home Loan'}
        elif 'MSME' in name_up or 'SME FINANC' in name_up:
            candidates = {'MSME Loan', 'Working Capital'}
        elif 'VEHICLE' in name_up or 'AUTO FINANC' in name_up:
            candidates = {'Vehicle Loan'}
        else:
            return None   # truly nothing to infer

    policies = []
    ctype_up = (ctype or '').upper()
    for loan_type in candidates:
        # Infer employment types from loan type / company type
        if 'MFI' in ctype_up or 'MICROFINANCE' in ctype_up:
            emp = ['self_employed_non_professional', 'agriculture']
        elif loan_type in ('MSME Loan', 'Business Loan', 'Working Capital',
                           'Supply Chain Finance'):
            emp = ['self_employed_non_professional', 'business']
        elif loan_type in ('Agriculture Loan', 'Rural Loan'):
            emp = ['agriculture']
        elif loan_type in ('Personal Loan', 'Education Loan', 'Consumer Durable Loan'):
            emp = ['salaried', 'self-employed']
        else:
            emp = []

        policies.append({
            'product_name':    loan_type,
            'loan_type':       loan_type,
            'employment_types': emp,
            # Numeric fields intentionally omitted → null in build_policies
        })

    return {'policies': policies} if policies else None


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main(limit: int | None = None, retry_failed: bool = False):
    """Args:
        limit: If set, only extract policies for the first N unprocessed lenders (pilot mode).
        retry_failed: Re-run lenders previously hard-failed due to transient failures.
    """
    log_file = setup_logging()

    logging.info("=" * 70)
    logging.info("POLICY EXTRACTION — production")
    logging.info("=" * 70)

    if not GEMINI_KEY:
        logging.error("GEMINI_API_KEY not set — aborting")
        sys.exit(1)

    # Load lenders from both outputs
    lenders  = load_lenders(INPUT_CSV) + load_lenders(RBI_CSV)
    if not lenders:
        logging.error("No lenders loaded — run NBFC/RBI extraction first")
        sys.exit(1)

    chk = Checkpoint(CHECKPOINT)
    rl  = RateLimiter()

    if retry_failed:
        retry_ids = chk.remove_failed()
        if retry_ids:
            chk.save()
            print(f"\n  Retry mode: {len(retry_ids)} previously-failed lender(s) will be re-run")
        else:
            print("\n  Retry mode: no failed lenders found in checkpoint — nothing to retry")

    todo     = [r for r in lenders if not chk.is_done(int(r.get('id', 0)))]
    if limit is not None:
        todo = todo[:limit]
        logging.info(f"PILOT MODE: limited to first {limit} lenders")

    print(f"\n  Total lenders : {len(lenders)}")
    print(f"  Already done  : {len(chk.done)}")
    print(f"  To process    : {len(todo)}" + (f"  [PILOT: first {limit}]" if limit else ""))
    print(f"  Scraper       : {'ON' if _scraper else 'OFF'}")
    print(f"  Output        : {OUTPUT}")
    print(f"  Log           : {log_file}")
    print(f"\n{'=' * 70}\n")

    all_policies: List[Policy] = []
    all_rejections: list       = []   # audit trail for every gate-rejected policy
    stats = Counter()

    try:
        for i, row in enumerate(todo, 1):
            lid     = int(row.get('id', 0))
            name    = row.get('company_name', '').strip()
            ctype   = row.get('company_type', 'NBFC')
            website = row.get('website', '').strip()

            # Parse existing product list from lender CSV
            try:
                products = json.loads(row.get('primary_loan_segments', '[]') or '[]')
            except Exception:
                products = []

            print(f"[{i}/{len(todo)}] {name[:60]}")
            logging.info(f"Processing lender {lid}: {name}")

            # ── Phase 1: Scrape ───────────────────────────────
            scraped = {}
            if _scraper and website:
                try:
                    scraped = _scraper.scrape(name, website)
                    if scraped.get('loan_tags'):
                        for t in scraped['loan_tags']:
                            if t not in products:
                                products.append(t)
                    logging.info(
                        f"  Scraped — tags={len(scraped.get('loan_tags',[]))} "
                        f"states={len(scraped.get('operating_states',[]))}"
                    )
                except Exception as e:
                    logging.warning(f"  Scraper error: {e}")

            # ── Phase 2: Gemini policy extraction ─────────────
            prompt   = make_policy_prompt(name, ctype, website, products, scraped)
            gemini_r = call_gemini(prompt, rl)

            is_heuristic = False
            if not gemini_r:
                # Gemini failed — try rule-based heuristic before giving up
                gemini_r = heuristic_extract(name, ctype, products, scraped)
                if gemini_r:
                    is_heuristic = True
                    stats['heuristic'] += 1
                    logging.info(
                        f"  Heuristic fallback: {len(gemini_r.get('policies', []))} stub(s)"
                    )
                    print(f"  ~ Gemini failed — heuristic fallback "
                          f"({len(gemini_r.get('policies', []))} stub(s))")
                else:
                    stats['failed'] += 1
                    chk.mark_failed(lid)
                    print(f"  ✗ Gemini failed — no heuristic available\n")
                    continue

            # ── Phase 3: Build + validate policies ────────────
            lender_rejections: list = []
            policies = build_policies(
                lid, name, ctype, gemini_r,
                scraped_context=scraped,
                rejections=lender_rejections,
                data_source='heuristic' if is_heuristic else 'gemini',
            )
            all_rejections.extend(lender_rejections)
            stats['rejected'] += len(lender_rejections)

            if not policies:
                stats['no_policies'] += 1
                chk.mark(lid, 0)
                reject_summary = (f'{len(lender_rejections)} rejected'
                                  if lender_rejections else 'none extracted')
                print(f"  ~ No viable policies ({reject_summary})\n")
                continue

            # Set source URL
            for p in policies:
                p.source_url = website

            all_policies.extend(policies)
            stats['success']        += 1
            stats['policies_total'] += len(policies)
            chk.mark(lid, len(policies))

            high_priority = sum(1 for p in policies if p.review_priority == 'high')
            avg_comp      = sum(p.completeness_score for p in policies) / len(policies)
            print(f"  ✓ {len(policies)} policies | completeness={avg_comp:.0%} "
                  f"| flagged={high_priority} | rejected={len(lender_rejections)}\n")

            # Progress save + upload every 10 lenders
            if i % 10 == 0 or i == len(todo):
                save_policies(all_policies, OUTPUT)
                chk.save()
                uploaded = upload_policies_to_supabase([asdict(p) for p in all_policies])
                if uploaded:
                    logging.info(f"  Uploaded {uploaded} policies to Supabase")
                print(f"  [checkpoint] {i}/{len(todo)} lenders | "
                      f"{stats['policies_total']} policies total\n")

    except KeyboardInterrupt:
        print("\n  Interrupted by user")
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        save_policies(all_policies, OUTPUT)
        chk.save()
        uploaded = upload_policies_to_supabase([asdict(p) for p in all_policies])
        if uploaded:
            logging.info(f"Final upload: {uploaded} policies to Supabase")

        # Write rejection audit log
        if all_rejections:
            ts            = datetime.now().strftime('%Y%m%d_%H%M%S')
            rejection_out = OUTPUT_DIR / f'policy_rejections_{ts}.csv'
            with open(rejection_out, 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=all_rejections[0].keys())
                w.writeheader()
                w.writerows(all_rejections)
            logging.info(f"Rejection log: {len(all_rejections)} entries → {rejection_out}")
            print(f"  Rejection log  : {rejection_out}")

        print(f"\n{'=' * 70}")
        print(f"  POLICY EXTRACTION COMPLETE")
        print(f"  Lenders processed  : {stats['success']}")
        print(f"  Heuristic fallback : {stats['heuristic']}")
        print(f"  Lenders failed     : {stats['failed']}")
        print(f"  No policies found  : {stats['no_policies']}")
        print(f"  Total policies     : {stats['policies_total']}")
        print(f"  Policies rejected  : {stats['rejected']}")
        print(f"  Output             : {OUTPUT}")
        print(f"{'=' * 70}\n")

        logging.info(f"Final: {dict(stats)}")


if __name__ == '__main__':
    import argparse as _ap
    _parser = _ap.ArgumentParser(description='Policy extraction pipeline')
    _parser.add_argument(
        '--limit', type=int, default=None, metavar='N',
        help='Process only the first N unprocessed lenders (pilot mode). Omit for full run.',
    )
    _parser.add_argument(
        '--retry-failed', action='store_true',
        help='Re-run lenders previously hard-failed due to transient failures (e.g. network outage).',
    )
    _args = _parser.parse_args()
    main(limit=_args.limit, retry_failed=_args.retry_failed)
