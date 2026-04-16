"""
run_nbfc_extraction_PRODUCTION_v2.py
=====================================
PRODUCTION-GRADE NBFC EXTRACTION SYSTEM

Enterprise Features:
- Input validation & sanitization
- Comprehensive error handling
- Logging system
- Data quality checks
- Resume from checkpoint
- Rate limiting & backoff
- Output validation
- Audit trail
- Health checks
- Secure API key loading from .env file
"""

import os, csv, json, time, sys, re, logging, hashlib
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from collections import Counter
import requests

# Load environment variables from project root .env (works regardless of CWD)
_ENV_FILE = Path(__file__).resolve().parent.parent / '.env'
try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE, override=False)
except ImportError:
    # Fallback: manual parser (handles quoted values but not multi-line)
    if _ENV_FILE.exists():
        with open(_ENV_FILE, encoding='utf-8') as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _sep, _v = _line.partition('=')
                    _k = _k.strip()
                    _v = _v.strip().strip('"').strip("'")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════

ROOT       = Path(__file__).parent.parent
INPUT_CSV  = ROOT / 'data' / 'input' / 'nbfc_names.csv'
OUTPUT_DIR = ROOT / 'data' / 'output'
OUTPUT     = OUTPUT_DIR / 'nbfc_extracted_verified.csv'
LOG_DIR    = ROOT / 'logs'
CHECKPOINT_FILE = OUTPUT_DIR / '.checkpoint.json'

# API keys — must be assigned before pre-flight checks
GEMINI_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models'

# ── Pre-flight checks ────────────────────────────────────────
if not INPUT_CSV.exists():
    print(f"\n❌  ERROR: Input file not found: {INPUT_CSV}")
    print(f"   Create this file with columns: id,company_name,original_website,business_summary,primary_focus")
    print(f"   Expected location: {INPUT_CSV.resolve()}\n")
    sys.exit(1)

if not GEMINI_KEY:
    print("\n❌  ERROR: GEMINI_API_KEY is not set.")
    print("   Add it to your .env file: GEMINI_API_KEY=your-key-here\n")
    sys.exit(1)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

WORKING_MODELS = [
    'gemini-2.5-flash',          # only model available on this API key (v1beta)
]

# ── Gemini system instruction ──────────────────────────────────
# Sent as systemInstruction (separate from the per-NBFC user prompt).
# Defines the extraction engine role, strict rules, and failure handling.
SYSTEM_INSTRUCTION = """\
You are a data extraction engine operating inside a production financial data pipeline.

ROLE:
- Extract factual data only from publicly available, verifiable sources
- You are NOT building a new system — you are filling fields in a fixed schema
- Input context (company name, website, summary) is a hint, not truth

STRICT RULES:
1. Return null for any field you cannot verify with high confidence
2. Do NOT estimate, infer, or guess numeric values (AUM, revenue, employees)
3. Do NOT hallucinate websites, phone numbers, emails, or stock symbols
4. All field values must pass downstream schema validation — wrong data corrupts the database
5. Numbers are in Indian Crores (1 Crore = 10 million) unless stated otherwise
6. Constrained fields (rbi_category, states, loan segments, intensity) must use only allowed values
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

# Rate limiting (prevent API abuse)
MAX_RETRIES = 3
RETRY_DELAY = 3
RATE_LIMIT_DELAY = 3.5  # Seconds between requests
MAX_REQUESTS_PER_MINUTE = 15

# ── Hybrid extraction mode ────────────────────────────────────
# Phase 1: scrapling (real website data, HIGH confidence)
# Phase 2: Gemini enrichment (fills financial/regulatory fields)
# Phase 3: merge — scraper HIGH > Gemini > scraper MEDIUM > scraper LOW
ENABLE_SCRAPER = True   # False = Gemini-only fallback

_scraper_available  = False
_SingleLenderScraper = None
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from scraper.lender_scraper import SingleLenderScraper as _SingleLenderScraper
    _scraper_available = True
except Exception:
    pass   # scrapling not installed — graceful degradation

_Guardrails = None
_guardrails_available = False
try:
    from scraper.guardrails import Guardrails as _Guardrails
    _guardrails_available = True
except Exception:
    pass   # guardrails import failed — basic validation only

_NBFCValidator = None
_nbfc_validator_available = False
try:
    from pipeline.nbfc_validator import NBFCValidator as _NBFCValidator
    _nbfc_validator_available = True
except Exception:
    pass   # validator unavailable — skips RBI verification + contact checks

# Quality thresholds
MIN_CONFIDENCE_THRESHOLD = 0.3
MAX_FIELD_LENGTH = 500
MIN_AUM_VALUE    = 10        # ₹10 Crore — practical minimum for a registered NBFC
MAX_AUM_VALUE    = 10000000  # ₹1 trillion maximum
MIN_QUALITY_SCORE = 0.35     # Hard rejection floor — records below this are not DB-worthy

# ══════════════════════════════════════════════════════════════
# LOGGING SETUP
# ══════════════════════════════════════════════════════════════

def setup_logging():
    """Configure production-grade logging"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = LOG_DIR / f'nbfc_extraction_{timestamp}.log'
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    console_formatter = logging.Formatter(
        '%(levelname)s: %(message)s'
    )
    
    # File handler (detailed logs)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    
    # Console handler (user-friendly)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)
    
    # Root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    logging.info(f"Logging initialized: {log_file}")
    return log_file

# ══════════════════════════════════════════════════════════════
# CONSTANTS & VALIDATION
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

VALID_PRODUCTS = [
    "MSME Loan", "Personal Loan", "Home Loan", "Business Loan",
    "Vehicle Loan", "Gold Loan", "Education Loan", "Micro Loan",
    "Loan Against Property", "Working Capital", "Agriculture Loan",
    "Credit Card", "Consumer Durable Loan",
    # Extended — previously dropped, causing data loss in matching engine
    "EV Loan", "Two Wheeler Loan", "Microfinance", "Rural Loan",
    "Supply Chain Finance",
]

VALID_RBI_CATEGORIES = [
    "NBFC-D", "NBFC-ND", "NBFC-ND-SI", "NBFC-MFI", "NBFC-IFC",
    "NBFC-Factor", "NBFC-ICC", "NBFC-P2P", "NBFC-AA", "NBFC-IC"
]

# ══════════════════════════════════════════════════════════════
# INPUT VALIDATION
# ══════════════════════════════════════════════════════════════

def sanitize_string(s: str, max_length: int = MAX_FIELD_LENGTH) -> str:
    """Sanitize and validate string input"""
    if not s or not isinstance(s, str):
        return ""
    
    # Remove control characters
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', s)
    
    # Trim whitespace
    s = s.strip()
    
    # Truncate if too long
    if len(s) > max_length:
        s = s[:max_length]
        logging.warning(f"String truncated to {max_length} chars")
    
    return s

def validate_website(url: str) -> bool:
    """Validate website URL format"""
    if not url:
        return False
    
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # or IP
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE
    )
    
    return bool(url_pattern.match(url))

def validate_aum(aum: float) -> bool:
    """Validate AUM value is reasonable"""
    if aum is None:
        return True  # null is ok
    
    if not isinstance(aum, (int, float)):
        return False
    
    return MIN_AUM_VALUE <= aum <= MAX_AUM_VALUE

# ══════════════════════════════════════════════════════════════
# DATA MODELS
# ══════════════════════════════════════════════════════════════

@dataclass
class NBFCLender:
    """Data model matching EXACT lenders table schema"""
    # From CSV (existing - validated)
    id:                      int
    company_name:            str
    company_type:            str = "NBFC"  # Default to NBFC
    website:                 str = ""
    
    # Financial intelligence
    aum_crores:              Optional[float] = None
    aum_category:            str = ""  # Auto-computed: Micro/Small/Mid/Large
    last_year_revenue:       Optional[float] = None
    is_listed:               bool = False
    stock_symbol:            str = ""
    
    # Business intelligence
    primary_loan_segments:   str = "[]"  # JSON array
    primary_product:         str = ""
    product_types:           str = "[]"  # JSON array
    
    # Geographic intelligence
    hq_location:             str = ""
    hq_state:                str = ""
    operating_states:        str = "[]"  # JSON array
    operating_intensity:     str = ""
    pan_india:               bool = False
    
    # Regulatory & funding
    rbi_category:            str = ""
    rbi_registration_number: str = ""
    recent_funding:          str = ""
    recent_funding_amount:   Optional[float] = None
    recent_funding_year:     Optional[int] = None
    
    # Operational
    established_year:        Optional[int] = None
    employee_count:          Optional[int] = None
    ticket_size_min:         Optional[float] = None
    ticket_size_max:         Optional[float] = None
    has_subsidiaries:        bool = False
    phone:                   str = ""
    email:                   str = ""
    
    # Metadata (matches table exactly)
    data_source:             str = "gemini+csv"
    extraction_status:       str = "pending"
    error:                   str = ""

    # Onboarding classification (Fix J)
    onboarding_tier:         str = "pending_review"  # verified_lender|provisional_lender|pending_review|rejected_lender

    # Validator results (migration 022)
    rbi_status:              str = "unverified"   # verified|active|inactive|cancelled|merged|not_found|unverified
    rbi_verified_name:       str = ""
    regulatory_tier:         str = "unknown"       # ND-SI|ND|D|MFI|IFC|Factor|P2P|AA|IC|unknown
    phone_valid:             bool = False
    email_valid:             bool = False
    email_domain_matches:    bool = False
    segment_category_mismatch: bool = False
    pan_india_suspect:       bool = False
    financial_source:        str = "none"
    geo_source:              str = "none"
    contact_source:          str = "none"
    regulatory_source:       str = "none"

    # Internal tracking (not in table, for processing only)
    _existing_summary:       str = field(default="", repr=False)
    _primary_focus:          str = field(default="", repr=False)
    _verified_fields:        str = field(default="", repr=False)
    _confidence_score:       float = field(default=0.0, repr=False)
    _extraction_timestamp:   str = field(default_factory=lambda: datetime.now().isoformat(), repr=False)
    _data_hash:              str = field(default="", repr=False)
    
    # Timestamps (will be set by database trigger)
    created_at:              str = field(default_factory=lambda: datetime.now().isoformat())
    last_updated:            str = field(default_factory=lambda: datetime.now().isoformat())
    
    def __post_init__(self):
        """Validate data after initialization"""
        # Sanitize strings
        self.company_name = sanitize_string(self.company_name)
        self.rbi_category = sanitize_string(self.rbi_category, 50)
        self.error = sanitize_string(self.error, 200)
        
        # Auto-compute AUM category
        self.aum_category = self._compute_aum_category()
        
        # Validate AUM
        if self.aum_crores is not None and not validate_aum(self.aum_crores):
            logging.warning(f"Invalid AUM for {self.company_name}: {self.aum_crores}")
            self.aum_crores = None
            self.error += " | Invalid AUM value"
        
        # Validate year
        _cur_year = datetime.now().year
        if self.established_year and not (1850 <= self.established_year <= _cur_year):
            logging.warning(f"Invalid year for {self.company_name}: {self.established_year}")
            self.established_year = None
        
        # Generate data hash for duplicate detection
        self._data_hash = hashlib.md5(
            f"{self.company_name}{self.rbi_registration_number}".encode()
        ).hexdigest()[:16]
    
    def _compute_aum_category(self) -> str:
        """
        Auto-compute AUM category:
        Micro: < 500 CR
        Small: 500 - 5,000 CR
        Mid: 5,000 - 50,000 CR
        Large: > 50,000 CR
        """
        if self.aum_crores is None:
            return ""
        
        if self.aum_crores < 500:
            return "Micro"
        elif self.aum_crores < 5000:
            return "Small"
        elif self.aum_crores < 50000:
            return "Mid"
        else:
            return "Large"

# ══════════════════════════════════════════════════════════════
# CHECKPOINT SYSTEM (Resume capability)
# ══════════════════════════════════════════════════════════════

class CheckpointManager:
    """Manage extraction checkpoints for resume capability"""
    
    def __init__(self, checkpoint_file: Path):
        self.checkpoint_file = checkpoint_file
        self.processed_ids = set()
        self.failed_ids = set()
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'start_time': datetime.now().isoformat(),
        }
        self.load()
    
    def load(self):
        """Load existing checkpoint"""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, 'r') as f:
                    data = json.load(f)
                    self.processed_ids = set(data.get('processed_ids', []))
                    self.failed_ids = set(data.get('failed_ids', []))
                    self.stats = data.get('stats', self.stats)
                    logging.info(f"Loaded checkpoint: {len(self.processed_ids)} processed")
            except Exception as e:
                logging.error(f"Checkpoint load error: {e}")
    
    def save(self):
        """Save checkpoint"""
        try:
            self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.checkpoint_file, 'w') as f:
                json.dump({
                    'processed_ids': list(self.processed_ids),
                    'failed_ids': list(self.failed_ids),
                    'stats': self.stats,
                }, f, indent=2)
        except Exception as e:
            logging.error(f"Checkpoint save error: {e}")
    
    def remove_failed(self) -> set:
        """Remove failed IDs from the processed set so they will be retried.

        Returns the set of IDs that were removed (the retry set).
        The caller is responsible for saving the checkpoint afterwards.
        """
        retry_ids = set(self.failed_ids)
        self.processed_ids -= retry_ids
        self.failed_ids.clear()
        self.stats['failed'] = 0
        logging.info(f"Retry mode: cleared {len(retry_ids)} failed IDs from checkpoint")
        return retry_ids

    def mark_processed(self, nbfc_id: int, success: bool):
        """Mark NBFC as processed"""
        self.processed_ids.add(nbfc_id)
        if success:
            self.stats['success'] += 1
        else:
            self.failed_ids.add(nbfc_id)
            self.stats['failed'] += 1
        
        if len(self.processed_ids) % 20 == 0:
            self.save()
    
    def is_processed(self, nbfc_id: int) -> bool:
        """Check if NBFC already processed"""
        return nbfc_id in self.processed_ids

# ══════════════════════════════════════════════════════════════
# RATE LIMITER
# ══════════════════════════════════════════════════════════════

class RateLimiter:
    """Intelligent rate limiting with exponential backoff"""
    
    def __init__(self, max_per_minute: int = MAX_REQUESTS_PER_MINUTE):
        self.max_per_minute = max_per_minute
        self.requests = []
        self.backoff_until = None
    
    def wait_if_needed(self):
        """Wait if rate limit exceeded"""
        now = time.time()
        
        # Check if in backoff period
        if self.backoff_until and now < self.backoff_until:
            wait_time = self.backoff_until - now
            logging.warning(f"Rate limit backoff: waiting {wait_time:.1f}s")
            time.sleep(wait_time)
            self.backoff_until = None
        
        # Remove old requests (>1 minute ago)
        self.requests = [t for t in self.requests if now - t < 60]
        
        # Check if at limit
        if len(self.requests) >= self.max_per_minute:
            wait_time = 60 - (now - self.requests[0])
            logging.warning(f"Rate limit: waiting {wait_time:.1f}s")
            time.sleep(wait_time)
            self.requests = []
        
        # Normal delay between requests
        time.sleep(RATE_LIMIT_DELAY)
        
        self.requests.append(now)
    
    def trigger_backoff(self, duration: int = 60):
        """Trigger exponential backoff on 429 errors"""
        self.backoff_until = time.time() + duration
        logging.warning(f"Triggered backoff: {duration}s")

# ══════════════════════════════════════════════════════════════
# GEMINI API WITH PRODUCTION FEATURES
# ══════════════════════════════════════════════════════════════

def make_strict_prompt(nbfc_name, website, existing_summary, primary_focus,
                       scraped_context: Optional[Dict] = None):
    """
    Per-NBFC extraction prompt.
    Rules and role are defined in SYSTEM_INSTRUCTION (sent separately).
    When scraped_context is provided (hybrid mode), pre-verified facts are injected
    so Gemini focuses only on the fields scraping couldn't fill.
    """
    products_str = ', '.join(f'"{p}"' for p in VALID_PRODUCTS)
    valid_rbi    = ', '.join(VALID_RBI_CATEGORIES)

    context_block = ''
    if existing_summary:
        context_block += f'\nContext: {existing_summary[:300]}'
    if primary_focus:
        context_block += f'\nPrimary business: {primary_focus}'

    # ── Inject pre-verified scraped facts ─────────────────────
    scraped_block = ''
    if scraped_context and isinstance(scraped_context, dict):
        conf     = scraped_context.get('_confidence', {})
        verified = []

        def _c(field):
            return conf.get(field, {}).get('confidence', 'LOW')

        _map = [
            ('phone',            'phone',            ['HIGH', 'MEDIUM']),
            ('email',            'email',             ['HIGH', 'MEDIUM']),
            ('hq_state',         'hq_state',          ['HIGH', 'MEDIUM']),
            ('established_year', 'established_year',  ['HIGH', 'MEDIUM']),
            ('employee_count',   'employee_count',    ['HIGH', 'MEDIUM']),
            ('is_listed',        'is_listed',         ['HIGH']),
            ('stock_symbol',     'stock_symbol',      ['HIGH', 'MEDIUM']),
            ('rbi_registration', 'rbi_registration',  ['HIGH', 'MEDIUM']),
        ]
        for scraper_field, label, min_confs in _map:
            val = scraped_context.get(scraper_field)
            if val not in (None, '', [], False) and _c(scraper_field) in min_confs:
                verified.append(f'  {label}: {val}')

        # Loan tags (any confidence)
        tags = scraped_context.get('loan_tags', [])
        if tags:
            verified.append(f'  loan_products: {tags}')

        # Operating states (any confidence)
        states = scraped_context.get('operating_states', [])
        if states:
            verified.append(f'  operating_states: {states}')

        # Context quality: count how many HIGH/MEDIUM fields were verified.
        # If the website was blocked or thin, the scraper returns few fields.
        # Gemini must not fill gaps from training data in that case — it hallucinates.
        verified_count = len(verified)

        if verified:
            scraped_block = (
                '\n\nPRE-VERIFIED DATA (scraped from company website — treat as FACTUAL):\n'
                + '\n'.join(verified)
                + '\n\nFor pre-verified fields above: return the same value in the JSON. '
                  'Focus your knowledge on the REMAINING fields (aum, revenue, rbi_category, '
                  'ticket_size, subsidiaries, primary_product, operating_intensity).'
            )
        else:
            verified_count = 0

        if verified_count == 0:
            # Website was blocked, thin, or scraper failed entirely.
            # No factual anchor — Gemini must not guess structural fields.
            scraped_block += (
                '\n\nWARNING: No website data was scraped for this lender. '
                'Return null for: aum_crores, last_year_revenue, operating_states, '
                'operating_intensity, hq_city, hq_state, phone, email, rbi_registration_number. '
                'Only fill fields you can confirm from RBI/SEBI/MCA official registries.'
            )

    return f"""Extract data for: {nbfc_name} — an Indian NBFC.
Website: {website or "unknown"}{context_block}{scraped_block}

Return this JSON schema (null for unknown fields):
{{
  "aum_crores": number | null,
  "last_year_revenue": number | null,
  "is_listed": true | false,
  "stock_symbol": string | null,
  "primary_loan_segments": subset of [{products_str}] | [],
  "primary_product": string | null,
  "hq_city": string | null,
  "hq_state": full Indian state name | null,
  "operating_states": ["PAN_INDIA"] | state name array | [],
  "operating_intensity": "Pan India" | "Regional" | "Single State" | null,
  "rbi_category": one of [{valid_rbi}] | null,
  "rbi_registration_number": "RBI CoR / registration number e.g. N.13.02437" | null,
  "established_year": 4-digit year | null,
  "employee_count": number | null,
  "website": "https://..." | null,
  "phone": string | null,
  "email": string | null
}}"""

def extract_json(text: str) -> Optional[Dict]:
    """Robust JSON extraction with extensive debugging"""
    if not text:
        logging.debug("Empty text received")
        return None
    
    # Log raw response for debugging
    logging.debug(f"Raw response preview: {text[:200]}...")
    
    # Try direct parse
    try:
        result = json.loads(text.strip())
        logging.debug("Direct JSON parse succeeded")
        return result
    except Exception as e:
        logging.debug(f"Direct parse failed: {e}")
    
    # Remove markdown fences and try again
    if '```' in text:
        # Remove all markdown formatting
        text = re.sub(r'```json\s*\n?', '', text)
        text = re.sub(r'```\s*\n?', '', text)
        text = text.strip()
        
        try:
            result = json.loads(text)
            logging.debug("Markdown-cleaned parse succeeded")
            return result
        except Exception as e:
            logging.debug(f"Markdown-cleaned parse failed: {e}")
    
    # Find JSON object by braces (handles trailing text)
    brace_count = 0
    start_idx = -1
    end_idx = -1
    
    for i, char in enumerate(text):
        if char == '{':
            if brace_count == 0:
                start_idx = i
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0 and start_idx != -1:
                end_idx = i
                break
    
    if start_idx != -1 and end_idx != -1:
        json_str = text[start_idx:end_idx+1]
        try:
            result = json.loads(json_str)
            logging.debug("Brace-matching parse succeeded")
            return result
        except Exception as e:
            logging.debug(f"Brace-matching parse failed: {e}")
            logging.debug(f"Attempted to parse: {json_str[:200]}...")
    
    # Last resort: try to fix common JSON issues
    try:
        # Remove trailing commas
        fixed = re.sub(r',\s*}', '}', text)
        fixed = re.sub(r',\s*]', ']', fixed)
        
        result = json.loads(fixed)
        logging.debug("Fixed JSON parse succeeded")
        return result
    except:
        pass
    
    logging.warning(f"All JSON parse attempts failed. Text length: {len(text)}")
    logging.warning(f"Text preview: {text[:300]}")
    return None

def extract_with_gemini(
    nbfc_name: str,
    website: str,
    summary: str,
    focus: str,
    rate_limiter: RateLimiter,
    debug: bool = False,
    scraped_context: Optional[Dict] = None,
) -> Optional[Dict]:
    """Production-grade Gemini extraction"""
    
    if not GEMINI_KEY:
        logging.error("GEMINI_API_KEY not set")
        return None
    
    rate_limiter.wait_if_needed()
    
    for model_name in WORKING_MODELS:
        url = f"{GEMINI_URL}/{model_name}:generateContent?key={GEMINI_KEY}"
        
        payload = {
            "systemInstruction": {
                "parts": [{"text": SYSTEM_INSTRUCTION}]
            },
            "contents": [{
                "parts": [{"text": make_strict_prompt(
                    nbfc_name, website, summary, focus, scraped_context
                )}]
            }],
            "generationConfig": {
                "temperature": 0,      # 0 = deterministic, no creative guessing
                "maxOutputTokens": 8192,
            }
        }
        
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(url, json=payload, timeout=60)
                
                # Handle errors
                if resp.status_code == 404:
                    logging.debug(f"{model_name} not available")
                    break
                
                if resp.status_code == 429:
                    logging.warning("Rate limited by API")
                    rate_limiter.trigger_backoff(90)
                    continue
                
                if resp.status_code in (500, 503):
                    logging.warning(f"Server error {resp.status_code}, retry {attempt}")
                    time.sleep(RETRY_DELAY * attempt)  # Exponential backoff
                    continue
                
                if resp.status_code != 200:
                    logging.error(f"HTTP {resp.status_code}: {resp.text[:100]}")
                    time.sleep(RETRY_DELAY)
                    continue
                
                # Parse response
                try:
                    body = resp.json()
                except json.JSONDecodeError as e:
                    logging.error(f"Invalid JSON response: {e}")
                    continue
                
                candidates = body.get('candidates', [])
                if not candidates:
                    logging.debug("Empty candidates")
                    continue
                
                # Extract text
                parts = candidates[0].get('content', {}).get('parts', [])
                raw_text = ''
                for part in parts:
                    if part.get('text'):
                        raw_text = part['text']
                        break
                
                if not raw_text:
                    logging.debug("Empty response text")
                    continue
                
                # Enable debug for first 5 NBFCs
                if debug:
                    logging.info(f"RAW RESPONSE ({len(raw_text)} chars):")
                    logging.info(f"{raw_text[:500]}")
                    logging.info("---")
                
                # Parse JSON
                result = extract_json(raw_text)
                if result:
                    logging.debug(f"Success with {model_name}")
                    return result
                
                logging.warning(f"JSON parse failed, attempt {attempt}")
                time.sleep(RETRY_DELAY)
                
            except KeyboardInterrupt:
                raise
            except requests.exceptions.Timeout:
                logging.warning(f"Timeout on attempt {attempt}")
                time.sleep(RETRY_DELAY * attempt)
            except Exception as e:
                logging.error(f"Unexpected error: {str(e)[:100]}")
                time.sleep(RETRY_DELAY)
        
        logging.debug(f"{model_name} failed after {MAX_RETRIES} attempts")
    
    return None

# ══════════════════════════════════════════════════════════════
# HYBRID MERGE — scraper + Gemini
# ══════════════════════════════════════════════════════════════

_EXTENDED_PRODUCTS = set(VALID_PRODUCTS)   # already contains extended set after Fix H

def merge_scraper_data(scraped: dict, gemini: dict) -> dict:
    """
    Phase 3: merge scrapling output into Gemini extracted dict.

    Priority rules (per field):
      HIGH   scraper confidence → always override Gemini
      MEDIUM scraper confidence → fill only if Gemini returned null / empty
      LOW    scraper confidence → skip (Gemini knowledge is likely better)

    Special handling:
      loan_tags / operating_states → union (both sources contribute)
      rbi_registration             → scraper HIGH/MEDIUM always wins
      is_non_lender                → caller must check before calling this
      potential_pan_india          → sets operating_intensity if Gemini null
    """
    if not scraped or not isinstance(scraped, dict):
        return gemini

    merged = dict(gemini)  # start from Gemini output
    conf   = scraped.get('_confidence', {})

    def _conf(field: str) -> str:
        return conf.get(field, {}).get('confidence', 'LOW')

    def _empty(val) -> bool:
        if val is None:              return True
        if isinstance(val, str)  and not val.strip(): return True
        if isinstance(val, list) and not val:         return True
        return False

    # ── Scalar fields ──────────────────────────────────────────
    scalar_map = [
        # (scraper_field,      gemini_field,              min_confidences)
        ('phone',             'phone',              ['HIGH', 'MEDIUM']),
        ('email',             'email',              ['HIGH', 'MEDIUM']),
        ('hq_city',           'hq_city',            ['HIGH', 'MEDIUM']),
        ('hq_state',          'hq_state',           ['HIGH', 'MEDIUM']),
        ('established_year',  'established_year',   ['HIGH', 'MEDIUM']),
        ('is_listed',         'is_listed',          ['HIGH']),
        ('stock_symbol',      'stock_symbol',       ['HIGH', 'MEDIUM']),
        ('employee_count',    'employee_count',     ['HIGH', 'MEDIUM']),
        ('branch_count',      'branch_count',       ['HIGH', 'MEDIUM']),
        ('rbi_registration',  'rbi_registration_number', ['HIGH', 'MEDIUM']),
        ('website',           'website',            ['HIGH']),
    ]
    for scraper_f, gemini_f, min_confs in scalar_map:
        val  = scraped.get(scraper_f)
        c    = _conf(scraper_f)
        if _empty(val) or c not in min_confs:
            continue
        if c == 'HIGH':
            merged[gemini_f] = val          # always override
        elif _empty(merged.get(gemini_f)):
            merged[gemini_f] = val          # fill gap

    # ── Loan tags (union of both sources) ─────────────────────
    scraper_tags = scraped.get('loan_tags', [])
    gemini_segs  = merged.get('primary_loan_segments', [])
    if not isinstance(gemini_segs, list):
        try:
            gemini_segs = json.loads(gemini_segs) if gemini_segs else []
        except Exception:
            gemini_segs = []
    if scraper_tags:
        combined = sorted(set(gemini_segs) | set(scraper_tags))
        valid    = [t for t in combined if t in _EXTENDED_PRODUCTS]
        if valid:
            merged['primary_loan_segments'] = valid

    # ── Operating states (take the richer source) ─────────────
    scraper_states = scraped.get('operating_states', [])
    gemini_states  = merged.get('operating_states', [])
    if not isinstance(gemini_states, list):
        gemini_states = []
    if scraper_states and len(scraper_states) > len(gemini_states):
        valid_states = [s for s in scraper_states if s in set(ALL_INDIA_STATES)]
        if valid_states:
            merged['operating_states'] = valid_states

    # ── Pan-India signal from scraper ─────────────────────────
    if scraped.get('potential_pan_india') and _empty(merged.get('operating_intensity')):
        merged['operating_intensity'] = 'Pan India'

    return merged


# ══════════════════════════════════════════════════════════════
# VERIFICATION & QUALITY CHECKS
# ══════════════════════════════════════════════════════════════

def verify_extracted_data(
    extracted: Dict,
    existing_summary: str,
    primary_focus: str
) -> Tuple[Dict, float, str]:
    """
    Multi-layer verification system
    Returns: (verified_data, confidence_score, verified_fields)
    """
    verified_fields = []
    confidence = 0.0
    
    # 1. Loan segments match focus
    segments = extracted.get('primary_loan_segments', [])
    if segments and primary_focus:
        focus_map = {
            'MSME': ['MSME Loan', 'Business Loan', 'Working Capital'],
            'Gold': ['Gold Loan'],
            'Vehicle': ['Vehicle Loan'],
            'Home': ['Home Loan', 'Loan Against Property'],
            'Personal': ['Personal Loan'],
            'Micro': ['Micro Loan'],
        }
        
        for key, values in focus_map.items():
            if key.lower() in primary_focus.lower():
                if any(seg in values for seg in segments):
                    verified_fields.append('loan_segments')
                    confidence += 0.20
                    break
    
    # 2. HQ state validation
    hq_state = extracted.get('hq_state')
    if hq_state and hq_state in ALL_INDIA_STATES:
        verified_fields.append('hq_state')
        confidence += 0.15
    
    # 3. RBI category format
    rbi_cat = extracted.get('rbi_category', '')
    if rbi_cat in VALID_RBI_CATEGORIES:
        verified_fields.append('rbi_category')
        confidence += 0.15
    
    # 4. AUM reasonableness
    aum = extracted.get('aum_crores')
    if aum and validate_aum(aum):
        verified_fields.append('aum')
        confidence += 0.20
    
    # 5. Operating states validation
    op_states = extracted.get('operating_states', [])
    if op_states:
        if op_states == ["PAN_INDIA"]:
            verified_fields.append('operating_states')
            confidence += 0.10
        elif all(s in ALL_INDIA_STATES for s in op_states):
            verified_fields.append('operating_states')
            confidence += 0.15
    
    # 6. Year validation
    year = extracted.get('established_year')
    if year and 1850 <= year <= datetime.now().year:
        verified_fields.append('established_year')
        confidence += 0.05
    
    return extracted, confidence, ','.join(verified_fields)

# ══════════════════════════════════════════════════════════════
# DATA BUILDERS
# ══════════════════════════════════════════════════════════════

def safe_float(v) -> Optional[float]:
    """Parse float with validation"""
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
    except:
        return None

def safe_int(v) -> Optional[int]:
    try:
        return int(re.sub(r'[^\d]', '', str(v))) if v else None
    except:
        return None

def build_nbfc_lender(
    csv_row: Dict,
    extracted_data: Dict,
    confidence: float,
    verified: str,
    data_source: str = 'gemini+csv_verified',
) -> NBFCLender:
    """Build validated NBFCLender object matching exact table schema"""
    
    # Operating states — Fix G: multi-signal Pan-India logic
    raw_states = extracted_data.get('operating_states', [])
    if raw_states == "PAN_INDIA" or raw_states == ["PAN_INDIA"]:
        # Explicit signal: treat as claim, validate below against AUM
        op_states = ALL_INDIA_STATES
        intensity = "Pan India"
        is_pan    = True
    else:
        op_states = [s for s in raw_states if s in ALL_INDIA_STATES] if isinstance(raw_states, list) else []
        is_pan    = False
        # ≥ 20 validated states = strong geographic evidence of pan-India lending
        # 15–19 = likely regional; scraper-claimed "Pan India" used as tiebreak
        _claimed_pan = (
            str(extracted_data.get('operating_intensity', '')).lower() == 'pan india'
        )
        if len(op_states) >= 20:
            intensity = "Pan India"; is_pan = True
        elif _claimed_pan and len(op_states) >= 20:
            # LLM/scraper claims pan-India AND actual validated states ≥ 20
            # Threshold raised from 10 → 20: marketing text "PAN India" on regional
            # lender sites was causing false positives (e.g. Cocta Finance → Telangana)
            intensity = "Pan India"; is_pan = True
        elif len(op_states) <= 1:
            intensity = "Single State"
        else:
            intensity = "Regional"
    # Gemini-stated intensity allowed to upgrade "Regional" → "Pan India"
    # ONLY if state evidence supports it (prevents hallucination acceptance)
    _gem_intensity = extracted_data.get('operating_intensity', '')
    if _gem_intensity and _gem_intensity != 'Pan India':
        intensity = _gem_intensity   # downgrade claims always accepted
    elif _gem_intensity == 'Pan India' and is_pan:
        intensity = 'Pan India'      # confirm only when evidence agrees
    
    # Loan segments — filter to canonical product list (Fix H: extended set prevents data loss)
    raw_segments = extracted_data.get('primary_loan_segments', [])
    if not raw_segments:
        raw_segments = []
    if isinstance(raw_segments, str):
        raw_segments = [raw_segments]
    _valid_set = set(VALID_PRODUCTS)   # already includes extended products
    segments = [p.strip() for p in raw_segments if p and p.strip() in _valid_set]
    
    # HQ location — validate hq_state against known list to reject hallucinations
    hq_city      = sanitize_string(extracted_data.get('hq_city', ''), 100)
    hq_state_raw = sanitize_string(extracted_data.get('hq_state', ''), 100)
    _state_lower = {s.lower(): s for s in ALL_INDIA_STATES}
    hq_state     = _state_lower.get(hq_state_raw.lower(), '')
    hq_loc       = f"{hq_city}, {hq_state}".strip(', ')
    
    # Website - use extracted or original from CSV
    website = sanitize_string(extracted_data.get('website', ''), 300)
    if not website:
        website = sanitize_string(csv_row.get('original_website', ''), 300)
    
    # Fix C: RBI registration reconciliation — validate + reconcile, not blind priority
    rbi_reg_csv    = sanitize_string(csv_row.get('registration_number', ''), 50)
    rbi_reg_gemini = sanitize_string(extracted_data.get('rbi_registration_number', ''), 50)
    _fmt_pat = re.compile(r'^[A-Z0-9][\w.\-]{5,24}$', re.IGNORECASE)
    rbi_reg_csv    = rbi_reg_csv    if _fmt_pat.match(rbi_reg_csv)    else ''
    rbi_reg_gemini = rbi_reg_gemini if _fmt_pat.match(rbi_reg_gemini) else ''
    # Mismatch detection: two different valid values → flag for review, prefer CSV
    if rbi_reg_csv and rbi_reg_gemini and rbi_reg_csv != rbi_reg_gemini:
        logging.warning(
            f"RBI reg mismatch for {csv_row.get('company_name','?')}: "
            f"CSV={rbi_reg_csv} vs Gemini={rbi_reg_gemini} — using CSV"
        )
    rbi_reg = rbi_reg_csv or rbi_reg_gemini
    
    # Contact info from extracted data
    phone = sanitize_string(extracted_data.get('phone', ''), 50)
    email = sanitize_string(extracted_data.get('email', ''), 100)
    
    return NBFCLender(
        # IDs and basic info
        id=int(csv_row.get('id', 0)),
        company_name=sanitize_string(csv_row.get('company_name', ''), 200),
        company_type='NBFC',  # All from this CSV are NBFCs
        website=website,
        
        # Financial intelligence
        aum_crores=safe_float(extracted_data.get('aum_crores')),
        # aum_category computed automatically in __post_init__
        last_year_revenue=safe_float(extracted_data.get('last_year_revenue')),
        is_listed=bool(extracted_data.get('is_listed', False)),
        stock_symbol=sanitize_string(extracted_data.get('stock_symbol', ''), 20),
        
        # Business intelligence
        primary_loan_segments=json.dumps(segments),
        primary_product=sanitize_string(extracted_data.get('primary_product', ''), 100),
        product_types=json.dumps(segments),
        
        # Geographic intelligence
        hq_location=hq_loc,
        hq_state=hq_state,
        operating_states=json.dumps(op_states),
        operating_intensity=intensity,
        pan_india=is_pan,
        
        # Regulatory & funding
        rbi_category=sanitize_string(extracted_data.get('rbi_category', ''), 50),
        rbi_registration_number=rbi_reg,
        recent_funding=sanitize_string(extracted_data.get('recent_funding', ''), 300),
        recent_funding_amount=safe_float(extracted_data.get('recent_funding_amount')),
        recent_funding_year=safe_int(extracted_data.get('recent_funding_year')),
        
        # Operational
        established_year=safe_int(extracted_data.get('established_year')),
        employee_count=safe_int(extracted_data.get('employee_count')),
        ticket_size_min=safe_float(extracted_data.get('ticket_size_min')),
        ticket_size_max=safe_float(extracted_data.get('ticket_size_max')),
        has_subsidiaries=bool(extracted_data.get('has_subsidiaries', False)),
        phone=phone,
        email=email,
        
        # Metadata
        data_source=data_source,
        extraction_status='success',
        error='',
        
        # Internal tracking (with underscore prefix, excluded from CSV)
        _existing_summary=sanitize_string(csv_row.get('business_summary', ''), 500),
        _primary_focus=sanitize_string(csv_row.get('primary_focus', ''), 100),
        _verified_fields=verified,
        _confidence_score=round(confidence, 2),
    )

# ══════════════════════════════════════════════════════════════
# FILE I/O WITH VALIDATION
# ══════════════════════════════════════════════════════════════

def load_nbfc_csv() -> List[Dict]:
    """Load and validate CSV input"""
    if not INPUT_CSV.exists():
        logging.error(f"File not found: {INPUT_CSV}")
        sys.exit(1)
    
    nbfcs = []
    required_fields = ['id', 'company_name']
    
    try:
        with open(INPUT_CSV, encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            
            # Validate headers
            if not all(field in reader.fieldnames for field in required_fields):
                logging.error(f"Missing required CSV fields: {required_fields}")
                sys.exit(1)
            
            for row_num, row in enumerate(reader, 2):
                # Validate required fields
                if not row.get('company_name', '').strip():
                    logging.warning(f"Row {row_num}: Missing company name, skipping")
                    continue
                
                nbfcs.append(row)
        
        logging.info(f"Loaded {len(nbfcs)} NBFCs from CSV")
        return nbfcs
        
    except Exception as e:
        logging.error(f"CSV load error: {e}")
        sys.exit(1)

def save_results(results: List[Dict], path: Path):
    """Save with atomic write and filter to table fields only"""
    if not results:
        logging.warning("No results to save")
        return
    
    # Define exact table column order
    # branch_count, quality_score, data_hash, field_confidence added by guardrails
    TABLE_FIELDS = [
        'id', 'created_at', 'last_updated', 'company_name', 'company_type', 'website',
        'aum_crores', 'aum_category', 'last_year_revenue', 'is_listed', 'stock_symbol',
        'primary_loan_segments', 'primary_product', 'product_types',
        'hq_location', 'hq_state', 'operating_states', 'operating_intensity', 'pan_india',
        'rbi_category', 'rbi_registration_number', 'recent_funding', 'recent_funding_amount', 'recent_funding_year',
        'established_year', 'employee_count', 'branch_count',
        'ticket_size_min', 'ticket_size_max', 'has_subsidiaries',
        'phone', 'email', 'rural_presence',
        'data_source', 'extraction_status', 'quality_score', 'data_hash', 'error',
        # Validator fields (migration 022)
        'rbi_status', 'rbi_verified_name', 'regulatory_tier',
        'phone_valid', 'email_valid', 'email_domain_matches',
        'segment_category_mismatch', 'pan_india_suspect',
        'financial_source', 'geo_source', 'contact_source', 'regulatory_source',
        # Onboarding tier (migration 023)
        'onboarding_tier',
    ]
    
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        # Deduplicate by data_hash (last write wins — new run is fresher).
        # This prevents double rows if pre-loaded + re-processed records overlap.
        seen_hashes: dict = {}
        for row in results:
            filtered_row = {k: v for k, v in row.items() if not k.startswith('_')}
            key = filtered_row.get('data_hash') or filtered_row.get('company_name', '')
            seen_hashes[key] = filtered_row
        filtered_results = list(seen_hashes.values())

        # Write to temp file first (atomic write)
        temp_path = path.with_suffix('.tmp')
        with open(temp_path, 'w', newline='', encoding='utf-8') as f:
            # Use exact table field order
            w = csv.DictWriter(f, fieldnames=TABLE_FIELDS, extrasaction='ignore')
            w.writeheader()
            w.writerows(filtered_results)

        # Atomic rename
        temp_path.replace(path)
        logging.debug(f"Saved {len(filtered_results)} records to {path}")
        
    except Exception as e:
        logging.error(f"Save error: {e}")

# ══════════════════════════════════════════════════════════════
# GUARDRAILS INPUT BUILDER
# ══════════════════════════════════════════════════════════════

def build_guardrails_input(csv_row: Dict, extracted: Dict,
                           scraped: dict, data_src: str) -> dict:
    """
    Build a flat dict accepted by Guardrails.run().
    Combines: CSV metadata + Gemini extracted fields + scraper fields.
    Guardrails will validate, normalize, deduplicate, and score this record.
    """
    conf = scraped.get('_confidence', {}) if scraped else {}
    return {
        # Identity
        'company_name':   csv_row.get('company_name', ''),
        'company_type':   'NBFC',
        'website':        extracted.get('website') or csv_row.get('original_website', ''),

        # Financial (Gemini owns these)
        'aum_crores':          extracted.get('aum_crores'),
        'last_year_revenue':   extracted.get('last_year_revenue'),
        'is_listed':           extracted.get('is_listed', False),
        'stock_symbol':        extracted.get('stock_symbol'),

        # Products (merged union from both sources)
        'loan_tags':              extracted.get('primary_loan_segments', []),
        'primary_loan_segments':  extracted.get('primary_loan_segments', []),
        'primary_product':        extracted.get('primary_product'),

        # Geography
        'hq_city':            extracted.get('hq_city'),
        'hq_state':           extracted.get('hq_state'),
        'operating_states':   extracted.get('operating_states', []),
        'potential_pan_india': scraped.get('potential_pan_india', False) if scraped else False,

        # Contact
        'phone': extracted.get('phone'),
        'email': extracted.get('email'),

        # Operational
        'established_year': extracted.get('established_year'),
        'employee_count':   extracted.get('employee_count'),
        'branch_count':     scraped.get('branch_count') if scraped else None,  # scraper-only field
        'rural_presence':   scraped.get('rural_presence', False) if scraped else False,

        # Regulatory — scraper reg takes priority (P3), Gemini fills gap
        'rbi_category':            extracted.get('rbi_category'),
        'rbi_registration':        scraped.get('rbi_registration', '') if scraped else '',
        'rbi_registration_number': (
            extracted.get('rbi_registration_number')
            or csv_row.get('registration_number', '')
        ),

        # Funding
        'recent_funding':        extracted.get('recent_funding'),
        'recent_funding_amount': extracted.get('recent_funding_amount'),
        'recent_funding_year':   extracted.get('recent_funding_year'),

        # Ticket size (Gemini-only)
        'ticket_size_min': extracted.get('ticket_size_min'),
        'ticket_size_max': extracted.get('ticket_size_max'),
        'has_subsidiaries': extracted.get('has_subsidiaries', False),

        # Metadata
        'data_source':  data_src,
        'is_non_lender': scraped.get('is_non_lender', False) if scraped else False,
        '_confidence':  conf,
    }


# ══════════════════════════════════════════════════════════════
# POST-EXTRACTION VALIDATION GATES (uniform, mandatory)
# ══════════════════════════════════════════════════════════════

# Fix D: Deterministic mandatory fields — not a % score threshold
# Only company_name is a hard reject. RBI reg + segments missing → needs_review.
_MANDATORY_FIELDS = [
    'company_name',   # only truly mandatory — no name = ungradeable record
]

# Fix H/J: Per-category RBI regulatory limits (only applied when registry-verified)
_RBI_CATEGORY_RULES: Dict = {
    'NBFC-P2P': {
        'max_aum':            50,   # RBI circular: ₹50 Cr aggregate lending cap
        'forbidden_segments': ['Home Loan', 'MSME Loan', 'Vehicle Loan',
                               'Gold Loan', 'Personal Loan'],
    },
    'NBFC-AA': {
        'max_aum':            None,
        'forbidden_segments': ['Personal Loan', 'MSME Loan', 'Home Loan',
                               'Business Loan', 'Vehicle Loan', 'Gold Loan',
                               'Education Loan', 'Micro Loan', 'Microfinance'],
    },
    'NBFC-IC': {
        'max_aum':            None,
        'forbidden_segments': ['Personal Loan', 'MSME Loan', 'Home Loan', 'Business Loan'],
    },
    'NBFC-MFI': {
        'max_aum':            None,
        'required_any':       ['Micro Loan', 'Microfinance'],
        'forbidden_segments': ['Home Loan', 'Vehicle Loan'],
    },
}


def _normalize_company_name(name: str) -> str:
    """
    Fix F: Normalize company name for deduplication.
    Removes legal suffixes, punctuation, and collapses whitespace.
    """
    s = name.lower().strip()
    # Remove legal entity suffixes
    for suffix in [
        r'\bprivate\b', r'\bpvt\b', r'\blimited\b', r'\bltd\b', r'\bcorporation\b',
        r'\bcorp\b', r'\bfinance\b', r'\bfinancial\b', r'\bnbfc\b', r'\bservices\b',
        r'\bsolutions\b', r'\benterprise\b', r'\bindia\b', r'\bco\b',
    ]:
        s = re.sub(suffix, '', s)
    s = re.sub(r'[^a-z0-9\s]', '', s)   # strip punctuation
    s = re.sub(r'\s+', ' ', s).strip()   # collapse whitespace
    return s


def _verify_website_entity_match(company_name: str, website: str) -> bool:
    """
    Fix E: Check that the website domain plausibly belongs to the company.
    Extracts meaningful domain words and checks overlap with company name tokens.
    Returns True if match is plausible (or website empty — unknown, not wrong).
    """
    if not website:
        return True   # absence of website is unknown, not wrong
    try:
        domain = re.sub(r'^https?://(www\.)?', '', website.lower()).split('/')[0]
        # Strip TLD and separators
        domain_core = re.sub(r'\.(com|co\.in|in|org|net|io|finance|capital)$', '', domain)
        domain_words = set(re.split(r'[\-_.]', domain_core)) - {'', 'www'}
        name_words   = set(_normalize_company_name(company_name).split()) - {''}
        # At least one domain word must appear in the company name tokens
        return bool(domain_words & name_words)
    except Exception:
        return True   # parse error → don't penalise


def _validate_regulatory_classification(
    rbi_category: str,
    aum_crores: Optional[float],
    segments: List[str],
) -> List[str]:
    """Returns list of hard violations (non-empty → reject). Fix C: only call when registry-verified."""
    rules = _RBI_CATEGORY_RULES.get(rbi_category)
    if not rules:
        return []
    violations: List[str] = []
    max_aum = rules.get('max_aum')
    if max_aum and aum_crores and aum_crores > max_aum:
        violations.append(
            f"{rbi_category} AUM cap ₹{max_aum}Cr exceeded: extracted ₹{aum_crores}Cr"
        )
    for seg in segments:
        if seg in rules.get('forbidden_segments', []):
            violations.append(f"{rbi_category} cannot offer '{seg}'")
    required_any = rules.get('required_any', [])
    if required_any and segments and not any(s in segments for s in required_any):
        violations.append(
            f"{rbi_category} must include one of {required_any}; found {segments}"
        )
    return violations


def _compute_onboarding_tier(
    rbi_status:     str,
    rbi_category:   str,
    phone_valid:    bool,
    email_valid:    bool,
    website_ok:     bool,
    quality_score:  float,
    segments:       List[str],
) -> str:
    """
    Fix J: Classify every record into a product-layer onboarding tier.

    verified_lender   — RBI registry matched, category confirmed, strong data quality
    provisional_lender — RBI reg number valid but not registry-matched; usable with caution
    pending_review    — passed hard gates but has data gaps; manual review required
    rejected_lender   — failed any hard gate (set externally; this fn never returns rejected)
    """
    if rbi_status == 'verified' and rbi_category and segments and quality_score >= 0.60:
        if (phone_valid or email_valid) and website_ok:
            return 'verified_lender'
        return 'pending_review'   # contact/website gap — needs review

    if rbi_status in ('verified', 'active', 'unverified'):
        # Has reg number (passed Fix A gate) but not fully confirmed
        if segments and quality_score >= 0.45:
            return 'provisional_lender'

    return 'pending_review'


def _apply_post_extraction_gates(
    rbi_status:   str,
    rbi_reg:      str,
    rbi_category: str,   # ONLY from registry (Fix C already enforced before calling)
    aum_crores:   Optional[float],
    segments:     List[str],
    mandatory_vals: Dict,
) -> Tuple[bool, bool, str]:
    """
    Uniform hard gates — identical across all code paths (Fix B).
    Fix D: uses mandatory field presence, not % score.
    Fix B: removed contact gate (B is a warning, not rejection — see _compute_onboarding_tier).
    Fix C: rbi_category here is registry-sourced only; Gemini value already discarded by caller.
    Fix E: cancelled/inactive rejected.
    Fix H: capability check only when registry has confirmed the category.
    Returns (is_rejected, is_needs_review, reason).
    Only company_name absence is a hard reject. All other data gaps → needs_review.
    """
    # Gate: no dead lenders — hard reject (Fix E)
    if rbi_status in ('cancelled', 'inactive'):
        return (True, False, f"RBI status '{rbi_status}' — lender is not operational")

    # Gate: mandatory fields — only company_name is a hard reject (Fix D)
    company_name = mandatory_vals.get('company_name', '')
    if not company_name:
        return (True, False, "Mandatory field missing: company_name")

    # Gate: RBI reg not found → needs_review (valid lenders often lack CoR in scraped text)
    if rbi_status == 'not_found':
        return (False, True, f"RBI registration not found or unverifiable (reg='{rbi_reg}')")

    # Gate: missing rbi_registration_number → needs_review
    rbi_reg_val = mandatory_vals.get('rbi_registration_number', '')
    if not rbi_reg_val:
        return (False, True, "RBI registration number missing — needs manual review")

    # Gate: missing loan segments → needs_review (tag inference may have failed)
    seg_val = mandatory_vals.get('primary_loan_segments', '[]')
    if not seg_val or seg_val in ('[]', '{}', ''):
        return (False, True, "primary_loan_segments empty — needs manual review")

    # Gate: capability check — ONLY when registry has confirmed the category (Fix C/H)
    if rbi_status == 'verified' and rbi_category and segments:
        violations = _validate_regulatory_classification(rbi_category, aum_crores, segments)
        if violations:
            return (True, False, f"Regulatory mismatch: {violations[0]}")

    return (False, False, '')


# ══════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ══════════════════════════════════════════════════════════════

def main(limit: int | None = None, retry_failed: bool = False):
    """Production main with comprehensive error handling.

    Args:
        limit:         If set, only extract the first N unprocessed NBFCs (pilot mode).
        retry_failed:  Re-run records that were previously hard-rejected due to transient
                       failures (e.g. Gemini API down).  Results are merged into the
                       existing CSV — existing successful rows are never overwritten.
    """
    # Setup
    log_file = setup_logging()
    logging.info("="*70)
    logging.info("NBFC EXTRACTION - PRODUCTION v2.0")
    logging.info("="*70)
    
    # Validate environment
    if not GEMINI_KEY:
        logging.error("GEMINI_API_KEY not found")
        print("\n" + "="*70)
        print("  ✗ ERROR: GEMINI_API_KEY not found!")
        print("="*70)
        print("\n  Create a .env file in your project root with:")
        print("  GEMINI_API_KEY=your_api_key_here")
        print("\n  Or set it as an environment variable:")
        print("  export GEMINI_API_KEY=your_api_key_here")
        print("\n  Get your API key from: https://aistudio.google.com/apikeys")
        print("="*70 + "\n")
        sys.exit(1)
    
    # Load data
    nbfcs = load_nbfc_csv()
    checkpoint   = CheckpointManager(CHECKPOINT_FILE)
    rate_limiter = RateLimiter()

    # ── Retry-failed mode ─────────────────────────────────────
    retry_ids: set = set()
    if retry_failed:
        retry_ids = checkpoint.remove_failed()
        if retry_ids:
            checkpoint.save()   # persist the cleared state before we start
            print(f"\n  Retry mode: {len(retry_ids)} previously-failed record(s) will be re-run")
            logging.info(f"Retry mode active — IDs to retry: {sorted(retry_ids)}")
        else:
            print("\n  Retry mode: no failed records found in checkpoint — nothing to retry")
            logging.info("Retry mode: checkpoint has no failed IDs")

    # ── Scraper initialisation (hybrid mode) ──────────────────
    scraper      = None
    hybrid_mode  = ENABLE_SCRAPER and _scraper_available and _SingleLenderScraper is not None
    if hybrid_mode:
        scraper = _SingleLenderScraper(use_stealth=False)
        logging.info("Hybrid mode ENABLED: scrapling → Gemini enrichment")
        print("  Mode: HYBRID (scraper + Gemini)")
    else:
        logging.info("Gemini-only mode (scraper unavailable or disabled)")
        print("  Mode: Gemini-only")

    # ── Guardrails initialisation ─────────────────────────────
    guardrails = None
    if _guardrails_available and _Guardrails is not None:
        guardrails = _Guardrails()
        logging.info("Guardrails ENABLED: validation + dedup + quality scoring")
        print("  Guardrails: ENABLED (full validation + dedup + quality score)")
    else:
        logging.warning("Guardrails unavailable — basic validation only")
        print("  Guardrails: DISABLED (basic validation only)")

    # ── NBFCValidator initialisation — MANDATORY (Fix A) ─────────────────────
    # RBI verification is a compliance requirement, not a degradable feature.
    # If the validator module fails to import, no extraction can proceed.
    if not _nbfc_validator_available or _NBFCValidator is None:
        logging.critical(
            "NBFCValidator failed to import. RBI verification is mandatory — "
            "cannot onboard lenders without it. Check backend/pipeline/nbfc_validator.py."
        )
        print("\n" + "=" * 70)
        print("  ✗ CRITICAL: NBFCValidator is required but could not be loaded.")
        print("  Check: backend/pipeline/nbfc_validator.py exists and is importable.")
        print("=" * 70 + "\n")
        sys.exit(2)

    _registry_path = ROOT / 'data' / 'input' / 'rbi_nbfc_registry.csv'
    nbfc_validator = _NBFCValidator(
        registry_path=_registry_path if _registry_path.exists() else None
    )
    _reg_label = "with RBI registry" if _registry_path.exists() else "registry file missing — status=unverified only"
    logging.info(f"NBFCValidator ENABLED ({_reg_label})")
    print(f"  NBFCValidator: ENABLED ({_reg_label})")

    # Load existing output hashes to detect cross-run duplicates
    existing_hashes: set = set()
    if OUTPUT.exists():
        try:
            with open(OUTPUT, encoding='utf-8') as _fh:
                for _existing_row in csv.DictReader(_fh):
                    _h = _existing_row.get('data_hash', '')
                    if _h:
                        existing_hashes.add(_h)
            logging.info(f"Loaded {len(existing_hashes)} existing hashes for dedup")
        except Exception as _he:
            logging.warning(f"Could not load existing hashes: {_he}")

    # Calculate work
    todo = [n for n in nbfcs if not checkpoint.is_processed(int(n.get('id', 0)))]
    if limit is not None:
        todo = todo[:limit]
        logging.info(f"PILOT MODE: limited to first {limit} NBFCs")

    print(f"\n  Total NBFCs: {len(nbfcs)}")
    print(f"  Already done: {len(checkpoint.processed_ids)}")
    if retry_ids:
        print(f"  Retrying (failed): {len(retry_ids)}")
    print(f"  To extract: {len(todo)}" + (f"  [PILOT: first {limit}]" if limit else ""))
    est_secs = len(todo) * (8 if hybrid_mode else 4)
    print(f"  Est. time: ~{round(est_secs / 60, 1)} min")
    print(f"  Output: {OUTPUT}")
    print(f"  Log: {log_file}")
    print(f"\n{'='*70}\n")

    # Pre-load records from a previous run so restart never loses data.
    # save_results() always writes the full results list — without pre-loading,
    # a restart would overwrite the file with only the new batch.
    # In retry mode, rows belonging to retry_ids are excluded so the fresh
    # results replace them (no stale rejected rows left in the output).
    results: list = []
    if OUTPUT.exists() and checkpoint.processed_ids:
        try:
            with open(OUTPUT, encoding='utf-8') as _prev:
                for _row in csv.DictReader(_prev):
                    _row_id = int(_row.get('id', 0) or 0)
                    if _row_id in retry_ids:
                        continue   # will be replaced by the fresh retry result
                    results.append(dict(_row))
            _skipped_msg = f" (excluded {len(retry_ids)} retry rows)" if retry_ids else ""
            logging.info(f"Pre-loaded {len(results)} records from previous run → {OUTPUT.name}{_skipped_msg}")
            print(f"  Restored : {len(results)} records from previous run{_skipped_msg}")
        except Exception as _pe:
            logging.warning(f"Could not pre-load previous output: {_pe} — starting fresh")
            results = []

    stats = Counter()

    try:
        for i, nbfc in enumerate(todo, 1):
            nbfc_id = int(nbfc.get('id', 0))
            name    = nbfc.get('company_name', '').strip()
            website = nbfc.get('original_website', '').strip()
            summary = nbfc.get('business_summary', '').strip()
            focus   = nbfc.get('primary_focus', '').strip()

            if not name:
                stats['skipped'] += 1
                continue

            # Fix F: Global dedup — normalized name + reg number, variant-resilient
            _norm_name  = _normalize_company_name(name)
            _reg_for_hash = nbfc.get('registration_number', '').strip().upper()
            _early_hash = hashlib.md5(
                f"{_norm_name}|{_reg_for_hash}".encode()
            ).hexdigest()[:16]
            if _early_hash in existing_hashes:
                logging.info(f"  → SKIPPED (duplicate hash): {name}")
                stats['skipped'] += 1
                checkpoint.mark_processed(nbfc_id, False)
                print(f"[{i}/{len(todo)}] {name[:60]} — SKIPPED (duplicate)\n")
                continue
            existing_hashes.add(_early_hash)

            print(f"[{i}/{len(todo)}] {name[:60]}")
            logging.info(f"Processing NBFC ID {nbfc_id}: {name}")

            # ── PHASE 1: Scrape ────────────────────────────────
            scraped     = {}
            data_src    = 'gemini_only'
            if scraper and website:
                try:
                    logging.info(f"  Phase 1 — scraping: {website}")
                    scraped = scraper.scrape(name, website)

                    # Non-lender early rejection
                    if scraped.get('is_non_lender'):
                        logging.info(f"  → Non-lender rejected: {name}")
                        stats['rejected'] += 1
                        lender = NBFCLender(
                            id=nbfc_id,
                            company_name=name,
                            company_type='NBFC',
                            website=website,
                            rbi_registration_number=nbfc.get('registration_number', ''),
                            extraction_status='rejected',
                            error='Non-lender: no lending signals found on website',
                            data_source='scraper',
                            _existing_summary=summary[:500],
                            _primary_focus=focus,
                        )
                        results.append(asdict(lender))
                        checkpoint.mark_processed(nbfc_id, False)
                        print(f"  ✗ REJECTED (non-lender)\n")
                        continue

                    data_src = 'scraper+gemini'
                    logging.info(
                        f"  Phase 1 done — phone={'✓' if scraped.get('phone') else '✗'} "
                        f"email={'✓' if scraped.get('email') else '✗'} "
                        f"state={scraped.get('hq_state') or '?'} "
                        f"tags={len(scraped.get('loan_tags', []))} "
                        f"rbi_reg={'✓' if scraped.get('rbi_registration') else '✗'}"
                    )
                except Exception as _se:
                    logging.warning(f"  Scraper error (falling back to Gemini-only): {_se}")
                    scraped  = {}
                    data_src = 'gemini_only'

            # ── PHASE 2: Gemini enrichment ─────────────────────
            logging.info(f"  Phase 2 — Gemini enrichment")
            extracted = extract_with_gemini(
                name, website, summary, focus, rate_limiter,
                debug=(i <= 5), scraped_context=scraped or None,
            )

            # ── PHASE 3: Merge ─────────────────────────────────
            if scraped and extracted:
                extracted = merge_scraper_data(scraped, extracted)
            elif scraped and not extracted:
                # Scraper succeeded but Gemini failed — build minimal record from scrape
                data_src  = 'scraper_only'
                extracted = {
                    'phone':              scraped.get('phone'),
                    'email':              scraped.get('email'),
                    'hq_city':            scraped.get('hq_city'),
                    'hq_state':           scraped.get('hq_state'),
                    'established_year':   scraped.get('established_year'),
                    'is_listed':          scraped.get('is_listed', False),
                    'stock_symbol':       scraped.get('stock_symbol'),
                    'employee_count':     scraped.get('employee_count'),
                    'primary_loan_segments': scraped.get('loan_tags', []),
                    'operating_states':   scraped.get('operating_states', []),
                    'rbi_registration_number': scraped.get('rbi_registration'),
                    'website':            scraped.get('website') or website,
                }

            if not extracted:
                # Transient failure (network timeout / Gemini unavailable).
                # Do NOT add to checkpoint — this record must be retried on restart.
                # Only permanent outcomes (success, guardrails reject, non-lender,
                # duplicate) get checkpointed.
                stats['failed'] += 1
                print(f"  ✗ FAILED (transient — will retry on restart)\n")
                logging.warning(f"Transient failure for {name!r} (id={nbfc_id}) — NOT checkpointed")
                continue

            # ── PHASE 4: Guardrails ─────────────────────────────
            if guardrails:
                gr_input = build_guardrails_input(nbfc, extracted, scraped, data_src)
                gr       = guardrails.run(gr_input)

                _log_level = {
                    'CRITICAL': logging.ERROR, 'ERROR': logging.WARNING,
                    'WARNING':  logging.INFO,  'INFO':  logging.DEBUG,
                }
                for issue in gr.issues:
                    logging.log(
                        _log_level.get(issue['severity'], logging.DEBUG),
                        f"  GUARDRAIL [{issue['severity']}] {issue['field']}: {issue['message']}"
                    )

                if not gr.is_valid:
                    reason = '; '.join(i['message'] for i in gr.critical_issues) or 'quality too low'
                    row = asdict(NBFCLender(
                        id=nbfc_id, company_name=name, company_type='NBFC',
                        website=website,
                        rbi_registration_number=nbfc.get('registration_number', ''),
                        extraction_status='rejected',
                        error=reason[:200],
                        data_source=data_src,
                    ))
                    row['quality_score'] = round(gr.quality_score, 2)
                    row['data_hash']     = gr.fingerprint
                    stats['rejected'] += 1
                    checkpoint.mark_processed(nbfc_id, False)
                    print(f"  ✗ GUARDRAIL REJECT (q={gr.quality_score:.0%}): {reason[:70]}\n")
                    results.append(row)
                    continue

                clean = dict(gr.clean_data)
                clean['id']              = nbfc_id
                clean['created_at']      = datetime.now().isoformat()
                clean['quality_score']   = round(gr.quality_score, 2)
                clean['ticket_size_min'] = safe_float(extracted.get('ticket_size_min'))
                clean['ticket_size_max'] = safe_float(extracted.get('ticket_size_max'))
                clean['has_subsidiaries']= bool(extracted.get('has_subsidiaries', False))
                clean['error']           = ''
                if 'created_at' not in clean:
                    clean['created_at']  = datetime.now().isoformat()
                _final_record  = clean
                _quality_score = gr.quality_score

            else:
                # Fix B: fallback path applies the same strict standards as guardrails
                verified_data, confidence, verified_fields = verify_extracted_data(
                    extracted, summary, focus
                )
                lender = build_nbfc_lender(
                    nbfc, verified_data, confidence, verified_fields,
                    data_source=data_src,
                )
                _final_record  = asdict(lender)
                _quality_score = round(confidence, 2)
                _final_record['quality_score'] = _quality_score

            # ── PHASE 4b: RBI validation — MANDATORY ────────────────────────
            _rbi_reg = (
                _final_record.get('rbi_registration_number', '')
                or nbfc.get('registration_number', '')
            )
            # nbfc_validator is guaranteed non-None here (Fix A: sys.exit(2) above if unavailable)
            vr = nbfc_validator.validate(
                name, _rbi_reg, extracted, scraped, data_src, existing_hashes
            )
            if vr.is_duplicate:
                _dup_reason = f"Duplicate of {vr.duplicate_of}"
                _final_record['extraction_status'] = 'rejected'
                _final_record['error']             = _dup_reason[:200]
                _final_record['onboarding_tier']   = 'rejected_lender'
                _final_record.update({
                    'rbi_status': vr.rbi_status, 'rbi_verified_name': vr.rbi_verified_name,
                    'regulatory_tier': vr.regulatory_tier,
                    'financial_source': vr.financial_source, 'geo_source': vr.geo_source,
                    'contact_source': vr.contact_source, 'regulatory_source': vr.regulatory_source,
                })
                stats['rejected'] += 1
                checkpoint.mark_processed(nbfc_id, False)
                print(f"  ✗ VALIDATOR REJECT (duplicate): {_dup_reason[:70]}\n")
                results.append(_final_record)
                continue

            _final_record.update({
                'rbi_status':                vr.rbi_status,
                'rbi_verified_name':         vr.rbi_verified_name,
                'regulatory_tier':           vr.regulatory_tier,
                'phone_valid':               vr.phone_valid,
                'email_valid':               vr.email_valid,
                'email_domain_matches':      vr.email_domain_matches,
                'segment_category_mismatch': vr.segment_category_mismatch,
                'pan_india_suspect':         vr.pan_india_suspect,
                'financial_source':          vr.financial_source,
                'geo_source':                vr.geo_source,
                'contact_source':            vr.contact_source,
                'regulatory_source':         vr.regulatory_source,
                'data_source':               vr.data_source_tag,
            })
            for _w in vr.warnings:
                logging.info(f"  VALIDATOR WARNING: {_w}")
            for _iss in vr.issues:
                logging.warning(f"  VALIDATOR ISSUE: {_iss}")
            if _final_record.get('data_hash'):
                existing_hashes.add(_final_record['data_hash'])

            # Fix C: discard Gemini-sourced rbi_category when not registry-verified.
            # Regulatory classification is RBI-registry data only — never LLM inference.
            _rbi_st = vr.rbi_status
            if _rbi_st not in ('verified', 'active'):
                _final_record['rbi_category'] = ''

            # Fix I: null out financial data when source is unverified.
            # Wrong AUM corrupts the lender ranking engine — silence is safer.
            if _final_record.get('financial_source', 'none') == 'none':
                _final_record['aum_crores']        = None
                _final_record['last_year_revenue'] = None
                _final_record['aum_category']      = ''

            # Fix E: website entity match warning (non-blocking — feeds onboarding_tier)
            _website_ok = _verify_website_entity_match(
                name, _final_record.get('website', '')
            )
            if not _website_ok:
                logging.warning(f"  Website domain may not belong to '{name}': "
                                f"{_final_record.get('website')}")

            # ── PHASE 4c: Uniform hard gates ────────────────────────────────
            _segs_gate: List[str] = []
            try:
                _segs_gate = json.loads(_final_record.get('primary_loan_segments', '[]') or '[]')
            except Exception:
                pass
            _mandatory_check = {
                'company_name':            _final_record.get('company_name', ''),
                'rbi_registration_number': _final_record.get('rbi_registration_number', ''),
                'primary_loan_segments':   _final_record.get('primary_loan_segments', '[]'),
            }
            _gate_rejected, _gate_review, _gate_reason = _apply_post_extraction_gates(
                rbi_status    = _rbi_st,
                rbi_reg       = _rbi_reg,
                rbi_category  = _final_record.get('rbi_category', ''),
                aum_crores    = _final_record.get('aum_crores'),
                segments      = _segs_gate,
                mandatory_vals = _mandatory_check,
            )
            if _gate_rejected:
                _final_record['extraction_status'] = 'rejected'
                _final_record['error']             = _gate_reason[:200]
                _final_record['onboarding_tier']   = 'rejected_lender'
                stats['rejected'] += 1
                checkpoint.mark_processed(nbfc_id, False)
                print(f"  ✗ GATE REJECT: {_gate_reason[:80]}\n")
                results.append(_final_record)
                continue
            if _gate_review:
                # Data gap — not a hard reject; route to needs_review for manual check
                _final_record['approval_status']   = 'needs_review'
                _final_record['onboarding_tier']   = 'pending_review'
                _final_record['extraction_status'] = 'success'
                _final_record['error']             = _gate_reason[:200]
                stats['success'] += 1
                checkpoint.mark_processed(nbfc_id, True)
                print(f"  ~ GATE REVIEW: {_gate_reason[:80]}\n")
                results.append(_final_record)
                if i % 5 == 0 or i == len(todo):
                    save_results(results, OUTPUT)
                    checkpoint.save()
                continue

            # ── Fix J: Classify onboarding tier ─────────────────────────────
            _tier = _compute_onboarding_tier(
                rbi_status    = _rbi_st,
                rbi_category  = _final_record.get('rbi_category', ''),
                phone_valid   = vr.phone_valid,
                email_valid   = vr.email_valid,
                website_ok    = _website_ok,
                quality_score = _quality_score,
                segments      = _segs_gate,
            )
            _final_record['onboarding_tier']   = _tier
            _final_record['extraction_status'] = 'success'

            # Fix B: contact gap is a warning → downgrade tier, not rejection
            if not vr.phone_valid and not vr.email_valid:
                logging.warning(f"  Contact gap (phone+email both invalid) — tier stays '{_tier}'")
                if _tier == 'verified_lender':
                    _final_record['onboarding_tier'] = 'pending_review'

            q = _quality_score
            if q >= 0.70:
                stats['high_conf'] += 1; q_emoji = 'HIGH'
            elif q >= 0.40:
                stats['med_conf']  += 1; q_emoji = 'MED'
            else:
                stats['low_conf']  += 1; q_emoji = 'LOW'

            aum = _final_record.get('aum_crores', 'N/A')
            print(f"  ✓ AUM: {aum} | Q: {q_emoji} {q:.0%} | "
                  f"RBI: {_rbi_st} | Tier: {_final_record['onboarding_tier']}\n")
            stats['success'] += 1
            checkpoint.mark_processed(nbfc_id, True)
            results.append(_final_record)
            
            # Save progress every 5 records (reduces re-work on power loss/kill)
            if i % 5 == 0 or i == len(todo):
                save_results(results, OUTPUT)
                checkpoint.save()
                pct = round(i / len(todo) * 100)
                print(f"💾 Progress: {i}/{len(todo)} ({pct}%) | ✓{stats['success']} ✗{stats['failed']}\n")
                logging.info(f"Checkpoint: {stats}")
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        logging.warning("Interrupted by user")
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        # Final save
        if results:
            save_results(results, OUTPUT)
            checkpoint.save()
        
        # Summary
        print(f"\n{'='*70}")
        print(f"  EXTRACTION COMPLETE  ({'HYBRID' if hybrid_mode else 'Gemini-only'})")
        print(f"  Success:          {stats['success']}")
        print(f"  Failed:           {stats['failed']}")
        print(f"  Rejected (non-lender): {stats['rejected']}")
        print(f"  High confidence:  {stats['high_conf']}")
        print(f"  Medium confidence:{stats['med_conf']}")
        print(f"  Low confidence:   {stats['low_conf']}")
        print(f"  Output: {OUTPUT}")
        print(f"  Log: {log_file}")
        print(f"{'='*70}\n")
        
        logging.info(f"Final stats: {dict(stats)}")

if __name__ == '__main__':
    import argparse as _ap
    _parser = _ap.ArgumentParser(description='NBFC extraction pipeline')
    _parser.add_argument(
        '--limit', type=int, default=None, metavar='N',
        help='Process only the first N unprocessed NBFCs (pilot mode). Omit for full run.',
    )
    _parser.add_argument(
        '--retry-failed', action='store_true',
        help=(
            'Re-run records that were previously hard-rejected due to transient failures '
            '(e.g. Gemini API down / network outage). Their stale rejected rows are removed '
            'from the output CSV and replaced with fresh results. '
            'Already-successful records are never touched.'
        ),
    )
    _args = _parser.parse_args()
    main(limit=_args.limit, retry_failed=_args.retry_failed)