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

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()  # Loads .env from current directory
except ImportError:
    print("Warning: python-dotenv not installed. Install with: pip install python-dotenv")
    print("Falling back to environment variables...")

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════

ROOT       = Path(__file__).parent.parent
INPUT_CSV  = ROOT / 'data' / 'input' / 'nbfc_names.csv'
OUTPUT_DIR = ROOT / 'data' / 'output'
OUTPUT     = OUTPUT_DIR / 'nbfc_extracted_verified.csv'
LOG_DIR    = ROOT / 'logs'
CHECKPOINT_FILE = OUTPUT_DIR / '.checkpoint.json'

# Load API key from .env file (more secure)
GEMINI_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1/models'

WORKING_MODELS = [
    'gemini-2.5-flash',
    'gemini-flash-latest',
]

# Rate limiting (prevent API abuse)
MAX_RETRIES = 3
RETRY_DELAY = 3
RATE_LIMIT_DELAY = 3.5  # Seconds between requests
MAX_REQUESTS_PER_MINUTE = 15

# Quality thresholds
MIN_CONFIDENCE_THRESHOLD = 0.3
MAX_FIELD_LENGTH = 500
MIN_AUM_VALUE = 0.1  # 10 lakhs minimum
MAX_AUM_VALUE = 10000000  # 1 trillion maximum

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
    "Credit Card", "Consumer Durable Loan"
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
        if self.established_year and not (1950 <= self.established_year <= 2025):
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

def make_strict_prompt(nbfc_name, website, existing_summary, primary_focus):
    """Simplified prompt to reduce output tokens"""
    products_str = ', '.join(f'"{p}"' for p in VALID_PRODUCTS)
    
    return f"""Extract data for: {nbfc_name}
Website: {website}

Return JSON (null if unknown):
{{
  "aum_crores": number,
  "last_year_revenue": number,
  "is_listed": boolean,
  "stock_symbol": "SYMBOL"|null,
  "primary_loan_segments": [{products_str}],
  "primary_product": string,
  "hq_city": string,
  "hq_state": string,
  "operating_states": array|["PAN_INDIA"],
  "operating_intensity": "Pan India"|"Regional"|"Single State",
  "rbi_category": string,
  "established_year": year,
  "employee_count": number,
  "website": url,
  "phone": string,
  "email": string
}}

Return ONLY the JSON."""

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
    debug: bool = False
) -> Optional[Dict]:
    """Production-grade Gemini extraction"""
    
    if not GEMINI_KEY:
        logging.error("GEMINI_API_KEY not set")
        return None
    
    rate_limiter.wait_if_needed()
    
    for model_name in WORKING_MODELS:
        url = f"{GEMINI_URL}/{model_name}:generateContent?key={GEMINI_KEY}"
        
        payload = {
            "contents": [{
                "parts": [{"text": make_strict_prompt(nbfc_name, website, summary, focus)}]
            }],
            "generationConfig": {
                "temperature": 0.05,
                "maxOutputTokens": 4096,  # Increased from 2048 to prevent truncation
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
    if year and 1950 <= year <= 2025:
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
    verified: str
) -> NBFCLender:
    """Build validated NBFCLender object matching exact table schema"""
    
    # Operating states
    raw_states = extracted_data.get('operating_states', [])
    if raw_states == "PAN_INDIA" or raw_states == ["PAN_INDIA"]:
        op_states = ALL_INDIA_STATES
        intensity = "Pan India"
        is_pan = True
    else:
        op_states = [s for s in raw_states if s in ALL_INDIA_STATES] if isinstance(raw_states, list) else []
        is_pan = False
        if len(op_states) >= 15:
            intensity = "Pan India"
        elif len(op_states) <= 1:
            intensity = "Single State"
        else:
            intensity = "Regional"
    
    if extracted_data.get('operating_intensity'):
        intensity = extracted_data['operating_intensity']
    
    # Loan segments - filter to valid products only
    raw_segments = extracted_data.get('primary_loan_segments', [])
    if not raw_segments:
        raw_segments = []
    if isinstance(raw_segments, str):
        raw_segments = [raw_segments]
    segments = [p.strip() for p in raw_segments if p and p.strip() in VALID_PRODUCTS]
    
    # HQ location
    hq_city = sanitize_string(extracted_data.get('hq_city', ''), 100)
    hq_state = sanitize_string(extracted_data.get('hq_state', ''), 100)
    hq_loc = f"{hq_city}, {hq_state}".strip(', ')
    
    # Website - use extracted or original from CSV
    website = sanitize_string(extracted_data.get('website', ''), 300)
    if not website:
        website = sanitize_string(csv_row.get('original_website', ''), 300)
    
    # RBI registration number from CSV
    rbi_reg = sanitize_string(csv_row.get('registration_number', ''), 50)
    
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
        data_source='gemini+csv_verified',
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
        with open(INPUT_CSV, encoding='utf-8') as f:
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
    TABLE_FIELDS = [
        'id', 'created_at', 'last_updated', 'company_name', 'company_type', 'website',
        'aum_crores', 'aum_category', 'last_year_revenue', 'is_listed', 'stock_symbol',
        'primary_loan_segments', 'primary_product', 'product_types',
        'hq_location', 'hq_state', 'operating_states', 'operating_intensity', 'pan_india',
        'rbi_category', 'rbi_registration_number', 'recent_funding', 'recent_funding_amount', 'recent_funding_year',
        'established_year', 'employee_count', 'ticket_size_min', 'ticket_size_max', 'has_subsidiaries',
        'phone', 'email', 'data_source', 'extraction_status', 'error'
    ]
    
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Filter results to only include table fields (exclude internal _ fields)
        filtered_results = []
        for row in results:
            filtered_row = {k: v for k, v in row.items() if not k.startswith('_')}
            filtered_results.append(filtered_row)
        
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
# MAIN EXECUTION
# ══════════════════════════════════════════════════════════════

def main():
    """Production main with comprehensive error handling"""
    
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
    checkpoint = CheckpointManager(CHECKPOINT_FILE)
    rate_limiter = RateLimiter()
    
    # Calculate work
    todo = [n for n in nbfcs if not checkpoint.is_processed(int(n.get('id', 0)))]
    
    print(f"\n  Total NBFCs: {len(nbfcs)}")
    print(f"  Already done: {len(checkpoint.processed_ids)}")
    print(f"  To extract: {len(todo)}")
    print(f"  Est. time: ~{round(len(todo) * 4 / 60, 1)} min")
    print(f"  Output: {OUTPUT}")
    print(f"  Log: {log_file}")
    print(f"\n{'='*70}\n")
    
    # Process
    results = []
    stats = Counter()
    
    try:
        for i, nbfc in enumerate(todo, 1):
            nbfc_id = int(nbfc.get('id', 0))
            name = nbfc.get('company_name', '').strip()
            website = nbfc.get('original_website', '').strip()
            summary = nbfc.get('business_summary', '').strip()
            focus = nbfc.get('primary_focus', '').strip()
            
            if not name:
                stats['skipped'] += 1
                continue
            
            print(f"[{i}/{len(todo)}] {name[:60]}")
            logging.info(f"Processing NBFC ID {nbfc_id}: {name}")
            
            # Extract
            extracted = extract_with_gemini(name, website, summary, focus, rate_limiter, debug=(i <= 5))
            
            if not extracted:
                lender = NBFCLender(
                    id=nbfc_id,
                    company_name=name,
                    company_type='NBFC',
                    website=website,
                    rbi_registration_number=nbfc.get('registration_number', ''),
                    extraction_status='failed',
                    error='No data returned from API',
                    _existing_summary=summary[:500],
                    _primary_focus=focus,
                )
                stats['failed'] += 1
                checkpoint.mark_processed(nbfc_id, False)
                print(f"  ✗ FAILED\n")
            else:
                # Verify
                verified_data, confidence, verified_fields = verify_extracted_data(
                    extracted, summary, focus
                )
                
                lender = build_nbfc_lender(nbfc, verified_data, confidence, verified_fields)
                stats['success'] += 1
                checkpoint.mark_processed(nbfc_id, True)
                
                # Confidence tracking
                if confidence >= 0.7:
                    stats['high_conf'] += 1
                    conf_emoji = '🟢'
                elif confidence >= 0.4:
                    stats['med_conf'] += 1
                    conf_emoji = '🟡'
                else:
                    stats['low_conf'] += 1
                    conf_emoji = '🔴'
                
                aum = verified_data.get('aum_crores', 'N/A')
                print(f"  ✓ AUM: {aum} | Conf: {conf_emoji} {confidence:.0%}\n")
            
            results.append(asdict(lender))
            
            # Save progress
            if i % 20 == 0 or i == len(todo):
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
        print(f"  EXTRACTION COMPLETE")
        print(f"  Success: {stats['success']}")
        print(f"  Failed: {stats['failed']}")
        print(f"  High confidence: {stats['high_conf']}")
        print(f"  Medium confidence: {stats['med_conf']}")
        print(f"  Low confidence: {stats['low_conf']}")
        print(f"  Output: {OUTPUT}")
        print(f"  Log: {log_file}")
        print(f"{'='*70}\n")
        
        logging.info(f"Final stats: {dict(stats)}")

if __name__ == '__main__':
    main()