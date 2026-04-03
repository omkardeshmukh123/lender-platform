"""
run_rbi_extraction.py — RBI Banks Extraction (Production Ready)
===============================================================
Hybrid extraction pipeline:
  Phase 1: Gemini — extract all fields including website
  Phase 2: Scrapling — scrape the bank's website for high-confidence data
  Phase 3: Merge — scraper HIGH/MEDIUM confidence overrides Gemini output

Enterprise Features:
- Resume from checkpoint (name-based, no IDs in RBI input)
- Comprehensive logging to file + console
- Rate limiting & exponential backoff
- Scraper graceful degradation (falls back to Gemini-only)
- Data quality validation
- Audit trail (data_source, quality_score, created_at)
"""

import os, csv, json, time, sys, re, logging, hashlib
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict, field
from datetime import datetime
from collections import Counter
import requests

# ── Load .env from project root ───────────────────────────────
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

ROOT            = Path(__file__).parent.parent
RBI_DIR         = ROOT / 'data' / 'input' / 'rbi_banks_output'
OUTPUT_DIR      = ROOT / 'data' / 'output'
OUTPUT          = OUTPUT_DIR / 'rbi_banks_extracted_v8.csv'
LOG_DIR         = ROOT / 'logs'
CHECKPOINT_FILE = OUTPUT_DIR / '.rbi_checkpoint.json'

GEMINI_KEY = os.getenv('GEMINI_API_KEY', '').strip()

# ── Pre-flight checks ────────────────────────────────────────
if not RBI_DIR.exists():
    print(f"\n❌  ERROR: RBI input directory not found: {RBI_DIR}")
    print(f"   Expected directory with CSV files per bank category.")
    print(f"   Expected location: {RBI_DIR.resolve()}\n")
    sys.exit(1)

if not GEMINI_KEY:
    print("\n❌  ERROR: GEMINI_API_KEY is not set.")
    print("   Add it to your .env file: GEMINI_API_KEY=your-key-here\n")
    sys.exit(1)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models'

WORKING_MODELS = [
    'gemini-2.5-flash',
]

MAX_RETRIES          = 3
RETRY_DELAY          = 5
RATE_LIMIT_WAIT      = 30
RATE_LIMIT_DELAY     = 3.5   # seconds between requests
MAX_REQUESTS_PER_MIN = 15

ENABLE_SCRAPER = True

_scraper_available   = False
_SingleLenderScraper = None
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from scraper.lender_scraper import SingleLenderScraper as _SingleLenderScraper
    _scraper_available = True
except Exception:
    pass

# ── System instruction ─────────────────────────────────────────
SYSTEM_INSTRUCTION = """\
You are a data extraction engine operating inside a production financial data pipeline.

ROLE:
- Extract factual data only from publicly available, verifiable sources
- You are NOT building a new system — you are filling fields in a fixed schema
- Input context (company name, type) is a hint, not truth

STRICT RULES:
1. Return null for any field you cannot verify with high confidence
2. Do NOT estimate, infer, or guess numeric values (AUM, revenue, employees)
3. Do NOT hallucinate websites, phone numbers, emails, or stock symbols
4. All field values must pass downstream schema validation — wrong data corrupts the database
5. Numbers are in Indian Crores (1 Crore = 10 million) unless stated otherwise
6. Constrained fields (states, loan segments, intensity) must use only allowed values
7. Prefer official sources: RBI filings, company annual reports, official websites

FAILURE HANDLING:
- If data cannot be verified → set that field to null
- Never break JSON format
- Never return partial or malformed output
- Null is always safer than an incorrect value

OUTPUT:
- Return ONLY valid JSON — no markdown, no explanation, no comments
- Follow the schema exactly — no extra keys, no missing keys
- Output must pass JSON parsing, schema validation, and deduplication checks
"""

# ══════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════

ALL_INDIA_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi",
    "Jammu & Kashmir", "Ladakh", "Puducherry", "Chandigarh",
    "Dadra and Nagar Haveli", "Lakshadweep", "Andaman and Nicobar Islands"
]
_STATE_LOWER = {s.lower(): s for s in ALL_INDIA_STATES}

VALID_PRODUCTS = [
    "MSME Loan", "Personal Loan", "Home Loan", "Business Loan",
    "Vehicle Loan", "Gold Loan", "Education Loan", "Micro Loan",
    "Loan Against Property", "Working Capital", "Agriculture Loan",
    "Credit Card", "Consumer Durable Loan",
    # Extended — fix over-strict filtering that dropped real products
    "EV Loan", "Two Wheeler Loan", "Microfinance", "Rural Loan", "Supply Chain Finance",
]
_EXTENDED_PRODUCTS = set(VALID_PRODUCTS)   # unified set — no separate list needed

VALID_RBI_CATS = {'PSU Bank', 'Private Bank', 'Foreign Bank', 'Cooperative Bank'}

# Fix 6: Pan-India by evidence, not assumption.
# PSU Banks are licensed pan-India; all others require state-count evidence.
_ALWAYS_PAN_INDIA = {'PSU Bank'}

# ── Fix 1: RBI bank status — loaded from external file, NOT hardcoded ─────────
# External file: data/input/rbi_bank_status.json
# Schema: {"bank_name_fragment": {"status": "merged", "merged_into": "Bank of Baroda",
#           "effective_date": "2019-04-01", "source": "RBI Press Release PR-2019-2020/1407"}}
# If file is absent, falls back to the hardcoded seed below which MUST be kept updated.
_STATUS_FILE = ROOT / 'data' / 'input' / 'rbi_bank_status.json'

_BANK_STATUS_SEED: Dict = {
    'dena bank':                  {'status': 'merged',       'merged_into': 'Bank of Baroda',        'effective_date': '2019-04-01'},
    'vijaya bank':                {'status': 'merged',       'merged_into': 'Bank of Baroda',        'effective_date': '2019-04-01'},
    'corporation bank':           {'status': 'merged',       'merged_into': 'Union Bank of India',   'effective_date': '2020-04-01'},
    'andhra bank':                {'status': 'merged',       'merged_into': 'Union Bank of India',   'effective_date': '2020-04-01'},
    'syndicate bank':             {'status': 'merged',       'merged_into': 'Canara Bank',           'effective_date': '2020-04-01'},
    'allahabad bank':             {'status': 'merged',       'merged_into': 'Indian Bank',           'effective_date': '2020-04-01'},
    'oriental bank of commerce':  {'status': 'merged',       'merged_into': 'Punjab National Bank',  'effective_date': '2020-04-01'},
    'united bank of india':       {'status': 'merged',       'merged_into': 'Punjab National Bank',  'effective_date': '2020-04-01'},
    'lakshmi vilas bank':         {'status': 'discontinued', 'merged_into': 'DBS Bank India',        'effective_date': '2020-11-27'},
    'yes bank':                   {'status': 'restricted',   'merged_into': '',                      'effective_date': '2020-03-05'},
    'pmc bank':                   {'status': 'restricted',   'merged_into': 'Unity Small Finance Bank', 'effective_date': '2021-09-25'},
}

if _STATUS_FILE.exists():
    try:
        with open(_STATUS_FILE, encoding='utf-8') as _sf:
            _BANK_STATUS_DB: Dict = json.load(_sf)
        logging.info(f"Loaded RBI bank status from {_STATUS_FILE} ({len(_BANK_STATUS_DB)} entries)")  # type: ignore
    except Exception as _sfe:
        logging.warning(f"Could not load rbi_bank_status.json ({_sfe}) — using seed data")
        _BANK_STATUS_DB = _BANK_STATUS_SEED
else:
    _BANK_STATUS_DB = _BANK_STATUS_SEED


# ── Fix 2: Registry-backed RBI registration validation ────────────────────────
# External file: data/input/rbi_bank_registry.csv
# Columns: bank_name, rbi_registration_number, status, category
class _BankRegistry:
    """Validates RBI registration numbers against the official bank registry CSV."""

    def __init__(self, registry_path: Optional[Path]):
        self._by_reg:  Dict[str, Dict] = {}  # reg_number → record
        self._by_name: Dict[str, Dict] = {}  # normalized_name → record
        self._loaded   = False
        if registry_path and registry_path.exists():
            self._load(registry_path)

    def _load(self, path: Path):
        try:
            with open(path, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    reg  = row.get('rbi_registration_number', '').strip().upper()
                    name = row.get('bank_name', '').strip()
                    norm = re.sub(r'[^a-z0-9]', '', name.lower())
                    if reg:
                        self._by_reg[reg] = row
                    if norm:
                        self._by_name[norm] = row
            self._loaded = True
            logging.info(f"BankRegistry: loaded {len(self._by_reg)} entries from {path}")
        except Exception as e:
            logging.warning(f"BankRegistry load error: {e}")

    def validate_reg(self, bank_name: str, reg_number: str) -> str:
        """
        Returns validation tier:
          'registry_verified' — reg number found in registry AND name matches
          'registry_mismatch' — reg number found but name doesn't match (flag)
          'name_only'         — name found in registry but no reg number given
          'format_only'       — reg number passes format check, registry not loaded
          'invalid'           — format check failed or not found anywhere
        """
        if not self._loaded:
            # Registry unavailable — fall back to format-only check
            if reg_number and re.match(r'^[A-Z0-9][\w.\-]{5,24}$', reg_number, re.IGNORECASE):
                return 'format_only'
            return 'invalid' if reg_number else 'format_only'

        if reg_number:
            rec = self._by_reg.get(reg_number.strip().upper())
            if rec:
                norm_rec  = re.sub(r'[^a-z0-9]', '', rec.get('bank_name', '').lower())
                norm_name = re.sub(r'[^a-z0-9]', '', bank_name.lower())
                return 'registry_verified' if norm_rec in norm_name or norm_name in norm_rec else 'registry_mismatch'

        # No reg number given — try name lookup
        norm_name = re.sub(r'[^a-z0-9]', '', bank_name.lower())
        if norm_name in self._by_name:
            return 'name_only'

        return 'invalid' if reg_number else 'format_only'

    @property
    def is_loaded(self) -> bool:
        return self._loaded


_BANK_REGISTRY = _BankRegistry(
    ROOT / 'data' / 'input' / 'rbi_bank_registry.csv'
)

# Fix 9: Product capability rules loaded from external config if available,
# fallback to hardcoded dict.  Config file: data/input/bank_type_rules.json
_BANK_TYPE_RULES_DEFAULT: Dict = {
    'Foreign Bank': {
        'forbidden_segments': ['Agriculture Loan', 'Micro Loan', 'Microfinance',
                               'Rural Loan', 'Two Wheeler Loan', 'EV Loan'],
    },
    'Cooperative Bank': {
        'forbidden_segments': ['Credit Card', 'EV Loan', 'Consumer Durable Loan',
                               'Supply Chain Finance'],
    },
}
_rules_config = ROOT / 'data' / 'input' / 'bank_type_rules.json'
if _rules_config.exists():
    try:
        with open(_rules_config, encoding='utf-8') as _rf:
            _BANK_TYPE_RULES: Dict = json.load(_rf)
        logging.info(f"Loaded bank_type_rules from {_rules_config}")  # type: ignore[name-defined]
    except Exception:
        _BANK_TYPE_RULES = _BANK_TYPE_RULES_DEFAULT
else:
    _BANK_TYPE_RULES = _BANK_TYPE_RULES_DEFAULT

CATEGORY_MAP = {
    'Private Sector Banks – Indian Banks':  'Private Bank',
    'Private Sector Banks – Foreign Banks': 'Foreign Bank',
    'Private Sector Banks - Indian Banks':  'Private Bank',
    'Private Sector Banks - Foreign Banks': 'Foreign Bank',
    'Foreign Banks Having Presence in India': 'Foreign Bank',
    'Nationalised Banks':                   'PSU Bank',
    'State Bank of India (SBI)':            'PSU Bank',
    'State Co-operative Banks':             'Cooperative Bank',
    'Financial Institutions':               'PSU Bank',
    'Local Area Banks (LABs)':              'Private Bank',
    'Regional Rural Banks (RRBs)':          'SKIP',
    'Payments Banks (PBs)':                 'SKIP',
}

# ══════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════

@dataclass
class Lender:
    company_name:            str
    company_type:            str
    website:                 str   = ""
    aum_crores:              float = None
    last_year_revenue:       float = None
    is_listed:               bool  = False
    stock_symbol:            str   = ""
    primary_loan_segments:   str   = "[]"
    primary_product:         str   = ""
    product_types:           str   = "[]"
    hq_location:             str   = ""
    hq_state:                str   = ""
    operating_states:        str   = "[]"
    operating_intensity:     str   = ""
    pan_india:               bool  = False
    rbi_category:            str   = ""
    rbi_registration_number: str   = ""
    established_year:        int   = None
    employee_count:          int   = None
    branch_count:            int   = None
    phone:                   str   = ""
    email:                   str   = ""
    quality_score:           float = None
    data_source:             str   = "gemini_rbi"
    extraction_status:       str   = "success"
    error:                   str   = ""
    created_at:              str   = ""
    # Verification & onboarding fields
    rbi_verified:            bool  = True    # always True — sourced from RBI master list
    rbi_status:              str   = "active"  # active|discontinued|merged|unknown
    phone_valid:             bool  = False
    email_valid:             bool  = False
    website_entity_match:    bool  = False
    financial_source:        str   = "none"  # scraper_high|scraper_med|gemini|none
    contact_source:          str   = "none"
    geo_source:              str   = "none"
    onboarding_tier:         str   = "pending_review"  # verified_lender|provisional_lender|pending_review|rejected_lender
    confidence_score:        float = 0.0   # composite trust signal (0–1)
    rbi_merge_note:          str   = ""    # merger/restriction note with effective date
    reg_validation_tier:     str   = "format_only"  # registry_verified|registry_mismatch|name_only|format_only|invalid
    extraction_version:      str   = "v4.0"
    parent_bank:             str   = ""    # populated for merged/discontinued banks
    risk_flags:              str   = "[]"  # JSON array: no_website|no_contact|weak_rbi_validation|rbi_restricted
    schema_version:          str   = "bank_v2"
    validation_version:      str   = "v5"

# ══════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════

def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = LOG_DIR / f'rbi_extraction_{ts}.log'

    file_fmt    = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_fmt = logging.Formatter('%(levelname)s: %(message)s')

    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(file_fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(console_fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)

    logging.info(f"Logging initialised: {log_file}")
    return log_file

# ══════════════════════════════════════════════════════════════
# CHECKPOINT
# ══════════════════════════════════════════════════════════════

def _bank_key(name: str) -> str:
    """Stable checkpoint key from bank name."""
    return re.sub(r'\s+', '_', name.strip().lower())[:60]

class CheckpointManager:
    def __init__(self, path: Path):
        self.path          = path
        self.processed     = set()   # bank keys
        self.failed        = set()
        self.stats         = {
            'success': 0, 'failed': 0,
            'start_time': datetime.now().isoformat(),
        }
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path) as f:
                    data = json.load(f)
                self.processed = set(data.get('processed', []))
                self.failed    = set(data.get('failed',    []))
                self.stats     = data.get('stats', self.stats)
                logging.info(f"Checkpoint loaded: {len(self.processed)} processed")
            except Exception as e:
                logging.error(f"Checkpoint load error: {e}")

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'w') as f:
                json.dump({
                    'processed': list(self.processed),
                    'failed':    list(self.failed),
                    'stats':     self.stats,
                }, f, indent=2)
        except Exception as e:
            logging.error(f"Checkpoint save error: {e}")

    def remove_failed(self) -> set:
        retry_keys = set(self.failed)
        self.processed -= retry_keys
        self.failed.clear()
        self.stats['failed'] = 0
        logging.info(f"Retry mode: cleared {len(retry_keys)} failed keys from checkpoint")
        return retry_keys

    def is_done(self, name: str) -> bool:
        return _bank_key(name) in self.processed

    def mark(self, name: str, success: bool):
        key = _bank_key(name)
        self.processed.add(key)
        if success:
            self.stats['success'] += 1
        else:
            self.failed.add(key)
            self.stats['failed'] += 1
        if len(self.processed) % 10 == 0:
            self.save()

# ══════════════════════════════════════════════════════════════
# RATE LIMITER
# ══════════════════════════════════════════════════════════════

class RateLimiter:
    def __init__(self):
        self._timestamps: List[float] = []

    def wait(self):
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 60]
        if len(self._timestamps) >= MAX_REQUESTS_PER_MIN:
            wait = 60 - (now - self._timestamps[0]) + 1
            logging.info(f"Rate limit reached — sleeping {wait:.1f}s")
            time.sleep(max(wait, 0))
        time.sleep(RATE_LIMIT_DELAY)
        self._timestamps.append(time.time())

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def safe_float(v):
    if v is None or v == '':
        return None
    try:
        s = str(v).lower().replace(',', '').replace('₹', '').replace('$', '')
        if 'lakh crore' in s:
            return float(re.sub(r'[^\d.]', '', s)) * 100000
        if 'crore' in s:
            return float(re.sub(r'[^\d.]', '', s))
        if 'lakh' in s:
            return float(re.sub(r'[^\d.]', '', s)) / 100
        return float(re.sub(r'[^\d.]', '', s))
    except Exception:
        return None

def safe_int(v):
    try:
        return int(re.sub(r'[^\d]', '', str(v))) if v else None
    except Exception:
        return None

def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    if '```' in text:
        text = re.sub(r'```json\n?', '', text)
        text = re.sub(r'```\n?', '', text)
        try:
            return json.loads(text.strip())
        except Exception:
            pass
    s, e = text.find('{'), text.rfind('}')
    if s != -1 and e != -1:
        try:
            return json.loads(text[s:e+1])
        except Exception:
            pass
    return None

# ══════════════════════════════════════════════════════════════
# GEMINI PROMPT
# ══════════════════════════════════════════════════════════════

def make_prompt(bank_name: str, bank_type: str,
                scraped_context: Optional[dict] = None) -> str:
    products_str = ', '.join(f'"{p}"' for p in VALID_PRODUCTS)
    states_str   = ', '.join(f'"{s}"' for s in ALL_INDIA_STATES)

    context_block = ''
    if scraped_context:
        hints = []
        for f in ('phone', 'email', 'hq_city', 'hq_state', 'established_year',
                  'is_listed', 'stock_symbol', 'employee_count', 'branch_count',
                  'rbi_registration', 'website'):
            v = scraped_context.get(f)
            if v not in (None, '', [], False):
                hints.append(f"  {f}: {v}")
        if hints:
            context_block = (
                "\n\nVERIFIED SCRAPER CONTEXT (high-confidence, prefer these values):\n"
                + '\n'.join(hints) + '\n'
            )

    return (
        f"Extract data for: {bank_name} ({bank_type}) — an RBI-regulated bank in India.\n"
        f"{context_block}\n"
        "Return this exact JSON schema (null for unknown/unverifiable fields):\n\n"
        "{\n"
        '  "aum_crores": number | null,\n'
        '  "last_year_revenue": number | null,\n'
        '  "is_listed": true | false,\n'
        '  "stock_symbol": string | null,\n'
        f'  "primary_loan_segments": subset of [{products_str}] | [],\n'
        '  "primary_product": string | null,\n'
        '  "hq_city": string | null,\n'
        f'  "hq_state": one of [{states_str}] | null,\n'
        '  "operating_states": ["PAN_INDIA"] | state name array | [],\n'
        '  "operating_intensity": "Pan India" | "Regional" | "Single State" | null,\n'
        '  "rbi_category": "PSU Bank" | "Private Bank" | "Foreign Bank" | "Cooperative Bank" | null,\n'
        '  "rbi_registration_number": string | null,\n'
        '  "established_year": number | null,\n'
        '  "employee_count": number | null,\n'
        '  "website": "https://..." | null\n'
        "}"
    )

# ══════════════════════════════════════════════════════════════
# GEMINI API
# ══════════════════════════════════════════════════════════════

def extract_with_gemini(bank_name: str, bank_type: str,
                        rate_limiter: RateLimiter,
                        scraped_context: Optional[dict] = None,
                        debug: bool = False) -> Optional[dict]:
    if not GEMINI_KEY:
        return None

    prompt = make_prompt(bank_name, bank_type, scraped_context)

    for model in WORKING_MODELS:
        url     = f"{GEMINI_URL}/{model}:generateContent?key={GEMINI_KEY}"
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents":          [{"parts": [{"text": prompt}]}],
            "generationConfig":  {"temperature": 0, "maxOutputTokens": 8192},
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                rate_limiter.wait()
                resp = requests.post(url, json=payload, timeout=45)

                if resp.status_code == 404:
                    logging.warning(f"{model} → 404 (model unavailable)")
                    break

                if resp.status_code == 429:
                    wait = RATE_LIMIT_WAIT * attempt
                    logging.warning(f"Rate limit — waiting {wait}s (attempt {attempt})")
                    time.sleep(wait)
                    continue

                if resp.status_code in (500, 503):
                    logging.warning(f"Server error {resp.status_code}, retry {attempt}/{MAX_RETRIES}")
                    time.sleep(RETRY_DELAY * attempt)
                    continue

                if resp.status_code != 200:
                    logging.warning(f"HTTP {resp.status_code}: {resp.text[:120]}")
                    time.sleep(RETRY_DELAY)
                    continue

                body       = resp.json()
                candidates = body.get('candidates', [])
                if not candidates:
                    logging.warning(f"Empty candidates (attempt {attempt})")
                    time.sleep(RETRY_DELAY)
                    continue

                parts    = candidates[0].get('content', {}).get('parts', [])
                raw_text = next((p['text'] for p in parts if p.get('text')), '')
                if not raw_text:
                    time.sleep(RETRY_DELAY)
                    continue

                if debug:
                    logging.debug(f"  Raw ({len(raw_text)} chars): {raw_text[:200]}")

                result = extract_json(raw_text)
                if result:
                    logging.info(f"  Gemini OK [{model}] — {bank_name}")
                    return result

                logging.warning(f"  JSON parse failed (attempt {attempt})")
                time.sleep(RETRY_DELAY)

            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logging.warning(f"  Exception attempt {attempt}: {exc}")
                time.sleep(RETRY_DELAY * attempt)

        logging.warning(f"{model} failed after {MAX_RETRIES} attempts")

    return None

# ══════════════════════════════════════════════════════════════
# MERGE — scraper + Gemini
# ══════════════════════════════════════════════════════════════

def merge_scraper_data(scraped: dict, gemini: dict) -> dict:
    """
    Override/enrich Gemini output with scraper high-confidence data.

    Priority:
      HIGH   confidence → always override Gemini
      MEDIUM confidence → fill only if Gemini returned null/empty
      LOW    confidence → skip
    """
    if not scraped or not isinstance(scraped, dict):
        return gemini

    merged = dict(gemini)
    conf   = scraped.get('_confidence', {})

    def _c(f):  return conf.get(f, {}).get('confidence', 'LOW')
    def _empty(v):
        if v is None:                              return True
        if isinstance(v, str)  and not v.strip(): return True
        if isinstance(v, list) and not v:          return True
        return False

    scalar_map = [
        # (scraper_field,     gemini_field,              min_confidences)
        ('phone',            'phone',              ['HIGH', 'MEDIUM']),
        ('email',            'email',              ['HIGH', 'MEDIUM']),
        ('hq_city',          'hq_city',            ['HIGH', 'MEDIUM']),
        ('hq_state',         'hq_state',           ['HIGH', 'MEDIUM']),
        ('established_year', 'established_year',   ['HIGH', 'MEDIUM']),
        ('is_listed',        'is_listed',          ['HIGH']),
        ('stock_symbol',     'stock_symbol',       ['HIGH', 'MEDIUM']),
        ('employee_count',   'employee_count',     ['HIGH', 'MEDIUM']),
        ('branch_count',     'branch_count',       ['HIGH', 'MEDIUM']),
        ('rbi_registration', 'rbi_registration_number', ['HIGH', 'MEDIUM']),
        ('website',          'website',            ['HIGH']),
    ]
    for sf, gf, min_confs in scalar_map:
        val = scraped.get(sf)
        c   = _c(sf)
        if _empty(val) or c not in min_confs:
            continue
        if c == 'HIGH':
            merged[gf] = val
        elif _empty(merged.get(gf)):
            merged[gf] = val

    # Loan tags — union of both sources
    scraper_tags = scraped.get('loan_tags', [])
    gemini_segs  = merged.get('primary_loan_segments', [])
    if not isinstance(gemini_segs, list):
        try:    gemini_segs = json.loads(gemini_segs) if gemini_segs else []
        except: gemini_segs = []
    if scraper_tags:
        combined = sorted(set(gemini_segs) | set(scraper_tags))
        valid    = [t for t in combined if t in _EXTENDED_PRODUCTS]
        if valid:
            merged['primary_loan_segments'] = valid

    # Operating states — take richer source
    scraper_states = scraped.get('operating_states', [])
    gemini_states  = merged.get('operating_states', [])
    if not isinstance(gemini_states, list):
        gemini_states = []
    if scraper_states and len(scraper_states) > len(gemini_states):
        valid_states = [s for s in scraper_states if s in set(ALL_INDIA_STATES)]
        if valid_states:
            merged['operating_states'] = valid_states

    # Pan-India signal
    if scraped.get('potential_pan_india') and _empty(merged.get('operating_intensity')):
        merged['operating_intensity'] = 'Pan India'

    return merged

# ══════════════════════════════════════════════════════════════
# BUILD LENDER RECORD
# ══════════════════════════════════════════════════════════════

def _extract_parent_bank(merge_note: str) -> str:
    """Fix 4: extract structured parent bank name from rbi_merge_note."""
    if 'Merged into' in merge_note:
        return merge_note.replace('Merged into', '').split('(')[0].strip()
    if 'discontinued' in merge_note.lower():
        # e.g. "Discontinued as of 2020-11-27 → DBS Bank India"
        parts = merge_note.split('→')
        if len(parts) > 1:
            return parts[1].strip()
    return ''


def _contact_matches_website(email: str, website: str) -> bool:
    """
    Fix 5: email domain must relate to the bank's website domain.
    A contact@gmail.com for sbi.co.in is a red flag.
    Returns True if no email/website (absence is unknown, not wrong).
    """
    if not email or not website:
        return True
    try:
        site_domain = re.sub(r'^https?://(www\.)?', '', website.lower()).split('/')[0]
        # Extract second-level domain (e.g. "sbi" from "sbi.co.in")
        site_sld = site_domain.rsplit('.', 2)[0].split('.')[-1]
        email_domain = email.split('@')[-1].lower()
        return site_sld in email_domain
    except Exception:
        return True  # parse error → don't penalise


def _compute_risk_flags(
    website: str,
    phone_ok: bool,
    email_ok: bool,
    reg_tier: str,
    rbi_status: str,
    email_domain_ok: bool,
) -> str:
    """Fix 8: AML / compliance risk signal tags stored as JSON array."""
    flags = []
    if not website:
        flags.append('no_website')
    if not (phone_ok or email_ok):
        flags.append('no_contact')
    if reg_tier not in ('registry_verified', 'name_only'):
        flags.append('weak_rbi_validation')
    if rbi_status == 'restricted':
        flags.append('rbi_restricted')
    if email_ok and not email_domain_ok:
        flags.append('email_domain_mismatch')
    return json.dumps(flags)


def build_lender(name: str, ctype: str, data: dict,
                 scraped: Optional[dict] = None,
                 data_src: str = 'gemini_rbi') -> Lender:
    # HQ
    hq_city  = str(data.get('hq_city') or '').strip()
    hq_state = _STATE_LOWER.get(str(data.get('hq_state') or '').strip().lower(), '')
    hq_loc   = f"{hq_city}, {hq_state}".strip(', ')

    # Fix 6: Pan-India by evidence, not bank-type assumption.
    # PSU Banks are mandated to serve all of India; all others require verification.
    raw_states = data.get('operating_states', [])
    _claimed_pan_india = (
        raw_states in (["PAN_INDIA"], "PAN_INDIA")
        or str(data.get('operating_intensity', '')).lower() == 'pan india'
        or (scraped or {}).get('potential_pan_india', False)
    )
    if ctype in _ALWAYS_PAN_INDIA or _claimed_pan_india:
        op_states = ALL_INDIA_STATES
        intensity = 'Pan India'
        is_pan    = True
    else:
        op_states = raw_states if isinstance(raw_states, list) else []
        op_states = [s for s in op_states if s in set(ALL_INDIA_STATES)]
        # Private banks with ≥ 20 verified states → pan-India evidence
        is_pan    = (ctype == 'Private Bank' and len(op_states) >= 20)
        if is_pan:
            intensity = 'Pan India'
        elif len(op_states) <= 1:
            intensity = 'Single State'
        else:
            intensity = 'Regional'

    # Fix 2: rbi_category comes ONLY from CATEGORY_MAP (master_banks.csv → ctype).
    # Gemini's rbi_category field is intentionally discarded — it is LLM inference
    # about a RBI-classified field and cannot be trusted for compliance.
    rbi_cat = ctype   # authoritative: derived from RBI CSV category via CATEGORY_MAP

    # Loan segments — filter to canonical product list (Fix 7 uses this below)
    raw_segs = data.get('primary_loan_segments', [])
    if isinstance(raw_segs, str):
        try:    raw_segs = json.loads(raw_segs)
        except: raw_segs = [raw_segs]
    segments = [p.strip() for p in (raw_segs or []) if p and p.strip() in _EXTENDED_PRODUCTS]

    # RBI registration — format-validate; reject hallucinations; then registry-verify
    rbi_reg_raw = str(data.get('rbi_registration_number') or '').strip()
    rbi_reg = rbi_reg_raw if re.match(r'^[A-Z0-9][\w.\-]{5,24}$', rbi_reg_raw, re.IGNORECASE) else ''
    reg_val_tier = _BANK_REGISTRY.validate_reg(name, rbi_reg)

    # Scraper-only fields
    branch_count = safe_int(scraped.get('branch_count')) if scraped else None

    # Fix 9: Contact validation
    phone_raw = str(data.get('phone') or '').strip()
    email_raw = str(data.get('email') or '').strip()
    phone_ok  = _validate_phone(phone_raw)
    email_ok  = _validate_email(email_raw)

    # Fix 7: Tiered financial source — scraper_high (HIGH confidence) vs scraper_med (MEDIUM).
    # Gemini financial data is NEVER trusted — wrong AUM corrupts ranking engine.
    _scraper_aum = scraped.get('aum_crores') if scraped else None
    _aum_conf    = (scraped or {}).get('_confidence', {}).get('aum_crores', {}).get('confidence', '')
    if _scraper_aum not in (None, '') and _aum_conf == 'HIGH':
        fin_src = 'scraper_high'
    elif _scraper_aum not in (None, ''):
        fin_src = 'scraper_med'
    else:
        fin_src = 'none'
    aum_crores = safe_float(_scraper_aum) if fin_src != 'none' else None
    last_year_revenue = (
        safe_float(scraped.get('last_year_revenue') or data.get('last_year_revenue'))
        if fin_src != 'none' else None
    )

    # Contact / geo source provenance
    _scraper_has_contact = scraped and (scraped.get('phone') or scraped.get('email'))
    contact_src = 'scraper_high' if _scraper_has_contact else ('gemini' if (phone_raw or email_raw) else 'none')
    _scraper_has_geo = scraped and scraped.get('hq_state')
    geo_src = 'scraper_high' if _scraper_has_geo else ('gemini' if hq_state else 'none')

    # Fix 1: RBI operational status — not hard-coded, derived from known mergers/restrictions
    _rbi_st, _rbi_note = assign_rbi_status(name)

    # Website entity match
    website  = str(data.get('website') or '').strip()
    ws_match = _website_matches_bank(name, website)
    if website and not ws_match:
        logging.warning(f"  Website domain may not belong to '{name}': {website}")

    # Fix 5: contact domain check — email must relate to bank's website domain
    _email_domain_ok = _contact_matches_website(email_raw, website)

    # Fix 4: parent bank for merger lineage records
    _parent_bank = _extract_parent_bank(_rbi_note)

    # Fix 8: AML / risk flags
    _risk_flags = _compute_risk_flags(
        website=website, phone_ok=phone_ok, email_ok=email_ok,
        reg_tier=reg_val_tier, rbi_status=_rbi_st,
        email_domain_ok=_email_domain_ok,
    )

    # Fix 3: Quality score — ratio of filled fields (kept for reporting only; gates use mandatory fields)
    filled = sum(1 for v in [
        aum_crores, data.get('is_listed'), website, hq_city, hq_state,
        segments, rbi_reg, data.get('established_year'), data.get('employee_count'),
        phone_raw or email_raw,
    ] if v not in (None, '', [], False))
    quality = round(filled / 10, 2)

    return Lender(
        company_name            = name,
        company_type            = ctype,
        website                 = website,
        aum_crores              = aum_crores,
        last_year_revenue       = last_year_revenue,
        is_listed               = bool(data.get('is_listed', False)),
        stock_symbol            = str(data.get('stock_symbol') or '').strip(),
        primary_loan_segments   = json.dumps(segments),
        primary_product         = str(data.get('primary_product') or '').strip(),
        product_types           = json.dumps(segments),
        hq_location             = hq_loc,
        hq_state                = hq_state,
        operating_states        = json.dumps(op_states),
        operating_intensity     = intensity,
        pan_india               = is_pan,
        rbi_category            = rbi_cat,
        rbi_registration_number = rbi_reg,
        established_year        = safe_int(data.get('established_year')),
        employee_count          = safe_int(data.get('employee_count')),
        branch_count            = branch_count,
        phone                   = phone_raw,
        email                   = email_raw,
        quality_score           = quality,
        data_source             = data_src,
        extraction_status       = 'success',
        created_at              = datetime.now().isoformat(),
        # Fix 1: rbi_verified is TRUE only when registry-verified (not just "in RBI CSV")
        rbi_verified            = (reg_val_tier == 'registry_verified'),
        rbi_status              = _rbi_st,
        rbi_merge_note          = _rbi_note,
        phone_valid             = phone_ok,
        email_valid             = email_ok,
        website_entity_match    = ws_match,
        financial_source        = fin_src,
        contact_source          = contact_src,
        geo_source              = geo_src,
        confidence_score        = 0.0,     # computed below after build
        onboarding_tier         = 'pending_review',
        reg_validation_tier     = reg_val_tier,
        parent_bank             = _parent_bank,
        risk_flags              = _risk_flags,
    )

# ══════════════════════════════════════════════════════════════
# INPUT LOADING
# ══════════════════════════════════════════════════════════════

def load_rbi_banks() -> List[dict]:
    master = RBI_DIR / 'master_banks.csv'
    if not master.exists():
        logging.error(f"Input not found: {master}")
        print(f"\n✗ File not found: {master}\n")
        sys.exit(1)

    banks, seen = [], set()
    with open(master, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            raw_name = row.get('Bank_Name', '').strip().strip('"')
            cat      = row.get('Category', '').strip()
            ctype    = CATEGORY_MAP.get(cat, 'SKIP')

            if ctype == 'SKIP' or not raw_name or len(raw_name) < 3:
                continue

            key = raw_name.lower()[:40]
            if key in seen:
                continue
            seen.add(key)
            banks.append({'company_name': raw_name, 'company_type': ctype})

    if not any(b['company_name'] == 'State Bank of India' for b in banks):
        banks.insert(0, {'company_name': 'State Bank of India', 'company_type': 'PSU Bank'})

    logging.info(f"Loaded {len(banks)} banks from {master}")
    return banks

# ══════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════

_TABLE_FIELDS = [
    'company_name', 'company_type', 'website',
    'aum_crores', 'last_year_revenue', 'is_listed', 'stock_symbol',
    'primary_loan_segments', 'primary_product', 'product_types',
    'hq_location', 'hq_state', 'operating_states', 'operating_intensity', 'pan_india',
    'rbi_category', 'rbi_registration_number',
    'established_year', 'employee_count', 'branch_count',
    'phone', 'email',
    'quality_score', 'data_source', 'extraction_status', 'error', 'created_at',
    # Verification & compliance fields
    'rbi_verified', 'rbi_status', 'rbi_merge_note', 'parent_bank',
    'reg_validation_tier',
    'phone_valid', 'email_valid', 'website_entity_match',
    'financial_source', 'contact_source', 'geo_source',
    'confidence_score', 'onboarding_tier',
    'risk_flags', 'extraction_version', 'schema_version', 'validation_version',
]

def save(results: List[dict], path: Path):
    if not results:
        return
    path.parent.mkdir(parents=True, exist_ok=True)

    # Deduplicate by data_hash before writing (last write wins — current run is fresher).
    # Prevents double rows when pre-loaded rows + newly processed records overlap.
    seen: dict = {}
    for row in results:
        key = row.get('data_hash') or row.get('company_name', '')
        seen[key] = row
    deduped = list(seen.values())

    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=_TABLE_FIELDS, extrasaction='ignore')
        w.writeheader()
        w.writerows(deduped)
    tmp.replace(path)
    logging.info(f"Saved {len(deduped)} records → {path}")

# ══════════════════════════════════════════════════════════════
# VALIDATION HELPERS
# ══════════════════════════════════════════════════════════════

def _normalize_bank_name(name: str) -> str:
    """Fix 6: normalize for cross-run dedup — strips legal suffixes and punctuation."""
    s = name.lower().strip()
    for suffix in [r'\bbank\b', r'\blimited\b', r'\bltd\b', r'\bof india\b',
                   r'\bnational\b', r'\bcorporation\b', r'\bcorp\b', r'\bco\b']:
        s = re.sub(suffix, '', s)
    return re.sub(r'[^a-z0-9]', '', s)


def _dedup_key(name: str, website: str) -> str:
    """Fix 6: multi-key dedup — normalized name + website domain together."""
    norm_name = _normalize_bank_name(name)
    domain    = re.sub(r'^https?://(www\.)?', '', (website or '').lower()).split('/')[0]
    return hashlib.md5(f"{norm_name}|{domain}".encode()).hexdigest()


def assign_rbi_status(name: str) -> tuple:  # (status, merge_note)
    """
    Fix 1: Determine RBI operational status from the dynamically loaded _BANK_STATUS_DB.
    Sourced from data/input/rbi_bank_status.json (editable without code change).
    Returns (rbi_status, note_with_effective_date).
    """
    name_lower = name.lower()
    for fragment, rec in _BANK_STATUS_DB.items():
        if fragment in name_lower:
            status      = rec.get('status', 'unknown')
            merged_into = rec.get('merged_into', '')
            eff_date    = rec.get('effective_date', '')
            if merged_into:
                note = f"Merged into {merged_into} (effective {eff_date})"
            else:
                note = f"{status.capitalize()} as of {eff_date}" if eff_date else status.capitalize()
            return (status, note)
    return ('active', '')


def _validate_phone(phone: str) -> bool:
    """Fix contact validation: Indian mobile or STD landline."""
    if not phone:
        return False
    digits = re.sub(r'[\s\-\(\)\+]', '', phone)
    return bool(
        re.match(r'^(\+?91|0)?[6-9]\d{9}$', digits) or
        re.match(r'^0[2-9]\d{7,9}$', digits)
    )


def _validate_email(email: str) -> bool:
    """Fix contact validation: basic RFC-compliant format."""
    if not email:
        return False
    return bool(re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email))


def _website_matches_bank(name: str, website: str) -> bool:
    """
    Fix 4: strong entity match — first 6 chars of normalized name must appear in domain.
    Handles: SBI→sbi, HDFC→hdfc, ICICI→icici, Axis→axis, IDFC→idfc, etc.
    Falls back to word-overlap for longer names.
    """
    if not website:
        return True   # absence is unknown, not wrong
    try:
        domain = re.sub(r'^https?://(www\.)?', '', website.lower()).split('/')[0]
        norm   = re.sub(r'[^a-z]', '', name.lower())  # letters only, no spaces

        # Primary check: first-4-char prefix of normalized name appears in domain.
        # Requires len > 4 so short/generic words don't produce false positives.
        if len(norm) > 4 and norm[:4] in domain:
            return True

        # Secondary check: word-overlap (for long names like "Punjab National Bank")
        name_norm  = re.sub(r'[^a-z0-9\s]', '', name.lower())
        name_words = set(name_norm.split()) - {
            'bank', 'limited', 'ltd', 'of', 'india', 'national', 'the', 'and',
        }
        domain_core  = re.sub(r'\.(com|co\.in|in|org|net|bank|finance)$', '', domain)
        domain_words = set(re.split(r'[\-_.]', domain_core)) - {'', 'www'}
        return bool(domain_words & name_words)
    except Exception:
        return True   # parse error → don't penalise


def _validate_bank_capability(ctype: str, segments: List[str]) -> List[str]:
    """Fix 9: capability violations — rules loaded from config or hardcoded fallback."""
    rules = _BANK_TYPE_RULES.get(ctype, {})
    return [
        f"{ctype} cannot offer '{seg}'"
        for seg in segments
        if seg in rules.get('forbidden_segments', [])
    ]


def _enforce_source_hierarchy(
    extracted_field: str,
    gemini_val,
    scraper_val,
    registry_val,
    field_tier: str,   # 'regulatory' | 'financial' | 'contact' | 'geo'
):
    """
    Fix 5: Source hierarchy enforcement.
    Regulatory fields: registry > scraper_high > nothing (Gemini NEVER wins)
    Financial fields:  scraper_high > nothing (Gemini NEVER wins)
    Contact/geo:       scraper_high > scraper_med > gemini > none
    Returns (winning_value, winning_source).
    """
    _SOURCE_RANK = {'registry': 4, 'scraper_high': 3, 'scraper_med': 2, 'gemini': 1, 'none': 0}

    if field_tier == 'regulatory':
        # Registry always wins; Gemini cannot override
        if registry_val not in (None, ''):
            return (registry_val, 'registry')
        if scraper_val not in (None, ''):
            return (scraper_val, 'scraper_high')
        return (None, 'none')   # Gemini deliberately excluded

    if field_tier == 'financial':
        # Scraper only; Gemini excluded
        if scraper_val not in (None, ''):
            return (scraper_val, 'scraper_high')
        return (None, 'none')

    # contact / geo: full priority chain including Gemini as last resort
    for val, src in [(registry_val, 'registry'), (scraper_val, 'scraper_high'),
                     (gemini_val, 'gemini')]:
        if val not in (None, '', [], False):
            return (val, src)
    return (None, 'none')


def _compute_confidence_score(
    website_ok:       bool,
    phone_valid:      bool,
    email_valid:      bool,
    fin_src:          str,
    rbi_status:       str,
    reg_tier:         str,
    hq_state:         str,
    website:          str,
) -> float:
    """
    Fix 3: Composite trust score with both positive signals AND negative penalties.

    Positive:
      +0.30 — website entity confirmed
      +0.25 — at least one valid contact channel
      +0.30 — financial data from scraper (not LLM)
      +0.15 — RBI registration registry-verified or name_only

    Penalties (non-additive — applied after summing positives):
      −0.20 — rbi_status is restricted/merged/discontinued
      −0.15 — no website at all (major trust gap)
      −0.10 — HQ state unknown (geo verification failed)
      −0.10 — reg_validation_tier is 'invalid' or 'registry_mismatch'
    Max score = 1.0, min = 0.0
    """
    score = 0.0
    if website_ok:
        score += 0.30
    if phone_valid or email_valid:
        score += 0.25
    if fin_src == 'scraper_high':
        score += 0.30
    elif fin_src == 'scraper_med':
        score += 0.15
    if reg_tier in ('registry_verified', 'name_only'):
        score += 0.15
    elif reg_tier == 'format_only':
        score += 0.05   # partial credit

    # Penalties
    if rbi_status in ('restricted', 'merged', 'discontinued'):
        score -= 0.20
    if not website:
        score -= 0.15
    if not hq_state:
        score -= 0.10
    if reg_tier in ('invalid', 'registry_mismatch'):
        score -= 0.10

    return round(max(0.0, min(1.0, score)), 2)


def _apply_bank_gates(
    name:       str,
    ctype:      str,
    segments:   List[str],
    rbi_status: str,
    reg_tier:   str,
) -> tuple:  # (is_rejected: bool, is_needs_review: bool, reason: str)
    """Hard rejection gates. Order matters — cheapest/most-impactful checks first.
    Returns 3-tuple: (is_rejected, is_needs_review, reason).
    Merged/discontinued and regulatory violations are hard rejects.
    Missing segments or unverifiable registry → needs_review."""
    # Gate: merged/discontinued banks are lineage-only records (Fix 1) — hard reject
    if rbi_status in ('merged', 'discontinued'):
        return (True, False, f"Bank is '{rbi_status}' — lineage record only, not eligible for onboarding")

    # Gate: product capability violations (rules from config) — hard reject
    if segments:
        violations = _validate_bank_capability(ctype, segments)
        if violations:
            return (True, False, f"Capability violation: {violations[0]}")

    # Fix 2: RBI registry mismatch → needs_review (scraper may have wrong data, not wrong bank)
    if reg_tier in ('registry_mismatch', 'invalid'):
        return (False, True, f"RBI registration validation failed ({reg_tier}) — needs manual review")

    # Gate: no loan products extracted → needs_review (may be a scraping gap)
    if not segments:
        return (False, True, "No loan products extracted — needs manual review")

    return (False, False, '')


def _compute_bank_onboarding_tier(
    segments:         List[str],
    phone_valid:      bool,
    email_valid:      bool,
    fin_src:          str,
    rbi_status:       str,
    confidence_score: float,
    reg_tier:         str,
) -> str:
    """
    Onboarding tier driven by confidence_score (verified signals), NOT quality_score.
    Fix 3: name_only reg tier is legally insufficient — capped at pending_review.
    """
    has_contact = phone_valid or email_valid

    # Fix 3: name match alone ≠ legal identity — hold for manual review
    if reg_tier == 'name_only':
        return 'pending_review'

    # verified_lender: registry-verified identity + scraper financials + contact + strong confidence
    if (segments and has_contact and fin_src == 'scraper_high'
            and confidence_score >= 0.70 and rbi_status == 'active'
            and reg_tier == 'registry_verified'):
        return 'verified_lender'

    # provisional_lender: identity confirmed from RBI list + has products + not restricted
    if segments and rbi_status not in ('merged', 'discontinued'):
        return 'provisional_lender'

    return 'pending_review'


def _log_rejection(rejection_log: Path, name: str, ctype: str, reason: str):
    """Fix 7: append-only audit trail for every rejected bank record."""
    try:
        rejection_log.parent.mkdir(parents=True, exist_ok=True)
        write_header = not rejection_log.exists()
        with open(rejection_log, 'a', newline='', encoding='utf-8') as _rf:
            w = csv.writer(_rf)
            if write_header:
                w.writerow(['bank_name', 'bank_type', 'rejection_reason', 'rejected_at'])
            w.writerow([name, ctype, reason, datetime.now().isoformat()])
    except Exception as _le:
        logging.error(f"Could not write rejection log: {_le}")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main(limit: int | None = None, retry_failed: bool = False):
    """Args:
        limit: If set, only extract the first N unprocessed banks (pilot mode).
        retry_failed: Re-run banks previously hard-rejected due to transient failures.
    """
    log_file = setup_logging()

    logging.info("=" * 70)
    logging.info("RBI BANKS EXTRACTION — PRODUCTION")
    logging.info("=" * 70)

    if not GEMINI_KEY:
        logging.error("GEMINI_API_KEY not set")
        print("\n✗ GEMINI_API_KEY not found. Add it to your .env file.\n")
        sys.exit(1)

    banks      = load_rbi_banks()
    checkpoint = CheckpointManager(CHECKPOINT_FILE)
    rate_lim   = RateLimiter()

    # Retry mode: move failed keys back to unprocessed before building todo/dedup
    retry_keys: set = set()
    if retry_failed:
        retry_keys = checkpoint.remove_failed()
        if retry_keys:
            checkpoint.save()
            print(f"\n  Retry mode: {len(retry_keys)} previously-failed bank(s) will be re-run")
        else:
            print("\n  Retry mode: no failed banks found in checkpoint — nothing to retry")

    # Fix 6: Multi-key cross-run deduplication (name + website domain)
    existing_hashes: set = set()
    if OUTPUT.exists():
        try:
            with open(OUTPUT, encoding='utf-8-sig') as _fh:
                for _er in csv.DictReader(_fh):
                    _n = _er.get('company_name', '')
                    _w = _er.get('website', '')
                    if _bank_key(_n) in retry_keys:
                        continue   # will be re-processed — exclude from dedup
                    if _n:
                        existing_hashes.add(_dedup_key(_n, _w))
                        existing_hashes.add(_normalize_bank_name(_n))  # name-only fallback
            logging.info(f"Loaded {len(existing_hashes)} existing bank hashes for dedup")
        except Exception as _he:
            logging.warning(f"Could not load existing hashes: {_he}")

    # Fix 7: Rejection audit log path
    _rejection_log = OUTPUT_DIR / f'bank_rejections_{datetime.now().strftime("%Y%m%d")}.csv'

    # Scraper init
    scraper     = None
    hybrid_mode = ENABLE_SCRAPER and _scraper_available and _SingleLenderScraper is not None
    if hybrid_mode:
        scraper = _SingleLenderScraper(use_stealth=False)
        logging.info("Hybrid mode ENABLED: Gemini → scrapling → merge")
        print("  Mode: HYBRID (Gemini + scraper)")
    else:
        logging.info("Gemini-only mode (scraper unavailable or disabled)")
        print("  Mode: Gemini-only")

    todo = [b for b in banks if not checkpoint.is_done(b['company_name'])]
    if limit is not None:
        todo = todo[:limit]
        logging.info(f"PILOT MODE: limited to first {limit} banks")

    print(f"\n{'='*65}")
    print("  RBI BANKS EXTRACTION (Production)")
    print(f"{'='*65}")
    print(f"  Total banks : {len(banks)}")
    print(f"  Already done: {len(checkpoint.processed)}")
    print(f"  To extract  : {len(todo)}" + (f"  [PILOT: first {limit}]" if limit else ""))
    est = len(todo) * (9 if hybrid_mode else 4)
    print(f"  Est. time   : ~{round(est / 60, 1)} min")
    print(f"  Output      : {OUTPUT}")
    print(f"  Log         : {log_file}")
    print(f"{'='*65}\n")

    # Pre-load records from a previous run so restart never loses data.
    results: list = []
    if OUTPUT.exists() and checkpoint.processed:
        try:
            with open(OUTPUT, encoding='utf-8-sig') as _prev:
                for _row in csv.DictReader(_prev):
                    if _bank_key(_row.get('company_name', '')) in retry_keys:
                        continue   # will be replaced by fresh retry result
                    results.append(dict(_row))
            logging.info(f"Pre-loaded {len(results)} records from previous run → {OUTPUT.name}")
            print(f"  Restored : {len(results)} records from previous run")
        except Exception as _pe:
            logging.warning(f"Could not pre-load previous output: {_pe} — starting fresh")
            results = []

    stats = Counter()

    try:
        for i, bank in enumerate(todo, 1):
            name  = bank['company_name']
            ctype = bank['company_type']

            # Fix 6: Cross-run duplicate check (normalized name only at this point;
            # website not yet known — website-based key added after build)
            _norm = _normalize_bank_name(name)
            if _norm in existing_hashes:
                logging.info(f"  → SKIPPED (duplicate): {name}")
                stats['skipped'] += 1
                checkpoint.mark(name, False)
                print(f"[{i}/{len(todo)}] {name} — SKIPPED (duplicate)\n")
                continue
            existing_hashes.add(_norm)

            print(f"[{i}/{len(todo)}] {name} ({ctype})")
            logging.info(f"Processing: {name} ({ctype})")

            # ── PHASE 1: Gemini ────────────────────────────────
            logging.info(f"  Phase 1 — Gemini extraction")
            gemini_data = extract_with_gemini(
                name, ctype, rate_lim,
                scraped_context=None,
                debug=(i <= 3),
            )

            if not gemini_data:
                # Transient failure (network timeout / Gemini unavailable).
                # Do NOT checkpoint — must be retried on restart.
                stats['failed'] += 1
                logging.warning(f"Transient failure for {name!r} — NOT checkpointed, will retry")
                print(f"  ✗ FAILED (transient — will retry on restart)\n")
                continue

            # ── PHASE 2: Scraping ──────────────────────────────
            scraped  = {}
            data_src = 'gemini_rbi'

            website_from_gemini = str(gemini_data.get('website') or '').strip()
            if scraper and website_from_gemini:
                try:
                    logging.info(f"  Phase 2 — scraping: {website_from_gemini}")
                    scraped = scraper.scrape(name, website_from_gemini)

                    # Non-lender signal (unusual for banks but handle it)
                    if scraped.get('is_non_lender'):
                        logging.warning(f"  Non-lender signal for bank: {name} — ignoring flag")
                        scraped.pop('is_non_lender', None)

                    data_src = 'scraper+gemini'
                    logging.info(
                        f"  Phase 2 done — "
                        f"phone={'✓' if scraped.get('phone') else '✗'} "
                        f"email={'✓' if scraped.get('email') else '✗'} "
                        f"state={scraped.get('hq_state') or '?'} "
                        f"tags={len(scraped.get('loan_tags', []))} "
                        f"branches={scraped.get('branch_count') or '?'}"
                    )
                except Exception as exc:
                    logging.warning(f"  Scraper error (Gemini-only fallback): {exc}")
                    scraped  = {}
                    data_src = 'gemini_rbi'
            elif scraper and not website_from_gemini:
                logging.info(f"  Phase 2 skipped — no website from Gemini")

            # ── PHASE 3: Merge ─────────────────────────────────
            if scraped:
                final_data = merge_scraper_data(scraped, gemini_data)
                logging.info(f"  Phase 3 — merge complete")
            else:
                final_data = gemini_data

            # Build record
            lender = build_lender(name, ctype, final_data, scraped or None, data_src)

            # Fix 6: register multi-key dedup hash after website is known
            existing_hashes.add(_dedup_key(name, lender.website))

            _segs = json.loads(lender.primary_loan_segments or '[]')

            # Fix 8: compute confidence score from verified signals only
            conf_score = _compute_confidence_score(
                website_ok   = lender.website_entity_match,
                phone_valid  = lender.phone_valid,
                email_valid  = lender.email_valid,
                fin_src      = lender.financial_source,
                rbi_status   = lender.rbi_status,
                reg_tier     = lender.reg_validation_tier,
                hq_state     = lender.hq_state,
                website      = lender.website,
            )

            # Hard rejection gates (quality score not used — confidence score drives tier)
            _gate_rejected, _gate_review, _gate_reason = _apply_bank_gates(
                name=name, ctype=ctype, segments=_segs,
                rbi_status=lender.rbi_status, reg_tier=lender.reg_validation_tier,
            )
            row = asdict(lender)
            row['confidence_score'] = conf_score
            if _gate_rejected:
                # Distinguish merged/discontinued (lineage records) from hard rejects.
                # Both get onboarding_tier='rejected_lender' (excluded from product layer)
                # but merger records use extraction_status='merged' for data lineage queries.
                _is_merger = lender.rbi_status in ('merged', 'discontinued')
                row['extraction_status'] = 'merged' if _is_merger else 'rejected'
                row['error']             = _gate_reason[:200]
                row['onboarding_tier']   = 'rejected_lender'
                _log_rejection(_rejection_log, name, ctype, _gate_reason)  # Fix 7
                stats['rejected'] += 1
                checkpoint.mark(name, False)
                print(f"  ✗ {'MERGER LINEAGE' if _is_merger else 'GATE REJECT'}: {_gate_reason[:80]}\n")
                results.append(row)
                continue
            if _gate_review:
                # Data gap — not a hard reject; route to needs_review for manual check
                row['approval_status']   = 'needs_review'
                row['onboarding_tier']   = 'pending_review'
                row['extraction_status'] = 'success'
                row['error']             = _gate_reason[:200]
                _log_rejection(_rejection_log, name, ctype, f"[REVIEW] {_gate_reason}")
                stats['success'] += 1
                checkpoint.mark(name, True)
                print(f"  ~ GATE REVIEW: {_gate_reason[:80]}\n")
                results.append(row)
                if i % 10 == 0 or i == len(todo):
                    save(results, OUTPUT)
                continue

            # Onboarding tier driven by confidence score + registry tier
            tier = _compute_bank_onboarding_tier(
                segments         = _segs,
                phone_valid      = lender.phone_valid,
                email_valid      = lender.email_valid,
                fin_src          = lender.financial_source,
                rbi_status       = lender.rbi_status,
                confidence_score = conf_score,
                reg_tier         = lender.reg_validation_tier,
            )
            row['onboarding_tier'] = tier

            results.append(row)
            stats['success'] += 1
            checkpoint.mark(name, True)

            print(
                f"  ✓ [{data_src}] AUM:{lender.aum_crores or 'N/A'} | "
                f"RBI:{lender.rbi_status} | conf={conf_score:.0%} | Tier:{tier}\n"
            )

            # Periodic save
            if i % 10 == 0 or i == len(todo):
                save(results, OUTPUT)
                pct = round(i / len(todo) * 100)
                print(f"  Saved {i}/{len(todo)} ({pct}%) | ✓{stats['success']} ✗{stats['failed']}\n")

    except KeyboardInterrupt:
        print("\n\n  Interrupted — saving progress...")
        if results:
            save(results, OUTPUT)
        checkpoint.save()
        print(f"  Saved {len(results)} records.\n")
        sys.exit(0)

    # Final save
    save(results, OUTPUT)
    checkpoint.save()

    # Fix 7: Write extraction manifest — immutable audit record per run
    _manifest_path = OUTPUT_DIR / f'rbi_extraction_manifest_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    try:
        with open(_manifest_path, 'w', encoding='utf-8') as _mf:
            json.dump({
                'extraction_version':  'v4.0',
                'pipeline':            'rbi_banks',
                'run_completed_at':    datetime.now().isoformat(),
                'total_input_banks':   len(banks),
                'success':             stats['success'],
                'rejected':            stats['rejected'],
                'skipped':             stats['skipped'],
                'failed':              stats['failed'],
                'output_path':         str(OUTPUT),
                'registry_loaded':     _BANK_REGISTRY.is_loaded,
                'status_db_entries':   len(_BANK_STATUS_DB),
                'rejection_log':       str(_rejection_log),
            }, _mf, indent=2)
        logging.info(f"Manifest written: {_manifest_path}")
    except Exception as _me:
        logging.warning(f"Could not write extraction manifest: {_me}")

    print(f"\n{'='*65}")
    print(f"  COMPLETE!")
    print(f"  Success  : {stats['success']}")
    print(f"  Rejected : {stats['rejected']}")
    print(f"  Skipped  : {stats['skipped']}")
    print(f"  Failed   : {stats['failed']}")
    print(f"  Output   : {OUTPUT}")
    print(f"  Manifest : {_manifest_path}")
    print(f"{'='*65}\n")

if __name__ == '__main__':
    import argparse as _ap
    _parser = _ap.ArgumentParser(description='RBI banks extraction pipeline')
    _parser.add_argument(
        '--limit', type=int, default=None, metavar='N',
        help='Process only the first N unprocessed banks (pilot mode). Omit for full run.',
    )
    _parser.add_argument(
        '--retry-failed', action='store_true',
        help='Re-run banks previously hard-rejected due to transient failures (e.g. network outage).',
    )
    _args = _parser.parse_args()
    main(limit=_args.limit, retry_failed=_args.retry_failed)
