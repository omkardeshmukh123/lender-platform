"""
lender_scraper.py  v3.4
========================
New additions from problem/solution review:

NEW 1 — RBI registration number extraction (P3)
  Regex patterns for all RBI registration formats:
    N.13.02437 / N-13-02437 / B-01.00001 / DNBS(PD)CC-14
  Extracted from full page text, not HTML structure.
  Added to ScrapedLender as rbi_registration field.

NEW 2 — Non-lending company detection signal (P7)
  If a website has no lending signals at all (no loan/finance/credit/borrow
  keywords in the full page text) → sets is_non_lender=True.
  Guardrails uses this to reject the record with a clear reason.
  This catches shell companies, consultancies, and holding companies
  that have websites but are not actually lending institutions.

All previous fixes retained:
  - Dynamic link discovery (no hardcoded path guessing)
  - JSON-LD + DOM + text extraction in priority order
  - Footer-first address parsing
  - Spaced phone format (+91 9314 121 121)
  - Employee count minimum threshold (1000)
  - Confidence tracking per field (HIGH/MEDIUM/LOW + source)
"""

import re
import json
import time
import logging
from typing  import Optional
from urllib.parse import urlparse, urljoin
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

HIGH   = 'HIGH'
MEDIUM = 'MEDIUM'
LOW    = 'LOW'


# ─────────────────────────────────────────────────────────────
# FIELD RESULT + SCRAPED LENDER
# ─────────────────────────────────────────────────────────────

@dataclass
class FieldResult:
    value:      object = None
    confidence: str    = LOW
    source:     str    = 'none'

    def is_empty(self) -> bool:
        if self.value is None:                                    return True
        if isinstance(self.value, str)  and not self.value.strip(): return True
        if isinstance(self.value, list) and not self.value:         return True
        return False


@dataclass
class ScrapedLender:
    lender_name:          str         = ''
    website:              FieldResult = field(default_factory=FieldResult)
    phone:                FieldResult = field(default_factory=FieldResult)
    email:                FieldResult = field(default_factory=FieldResult)
    hq_city:              FieldResult = field(default_factory=FieldResult)
    hq_state:             FieldResult = field(default_factory=FieldResult)
    established_year:     FieldResult = field(default_factory=FieldResult)
    is_listed:            FieldResult = field(default_factory=FieldResult)
    stock_symbol:         FieldResult = field(default_factory=FieldResult)
    employee_count:       FieldResult = field(default_factory=FieldResult)
    branch_count:         FieldResult = field(default_factory=FieldResult)
    loan_tags:            FieldResult = field(default_factory=FieldResult)
    operating_states:     FieldResult = field(default_factory=FieldResult)
    rural_presence:       FieldResult = field(default_factory=FieldResult)
    rbi_registration:     FieldResult = field(default_factory=FieldResult)   # NEW 1
    potential_pan_india:  bool        = False
    is_non_lender:        bool        = False                                # NEW 2

    def to_dict(self) -> dict:
        return {
            'lender_name':         self.lender_name,
            'website':             self.website.value or '',
            'phone':               self.phone.value or '',
            'email':               self.email.value or '',
            'hq_city':             self.hq_city.value or '',
            'hq_state':            self.hq_state.value or '',
            'established_year':    self.established_year.value,
            'is_listed':           self.is_listed.value or False,
            'stock_symbol':        self.stock_symbol.value or '',
            'employee_count':      self.employee_count.value,
            'branch_count':        self.branch_count.value,
            'loan_tags':           self.loan_tags.value or [],
            'operating_states':    self.operating_states.value or [],
            'rural_presence':      self.rural_presence.value or False,
            'rbi_registration':    self.rbi_registration.value or '',        # NEW 1
            'potential_pan_india': self.potential_pan_india,
            'is_non_lender':       self.is_non_lender,                      # NEW 2
        }

    def confidence_report(self) -> dict:
        fields = ['phone', 'email', 'hq_city', 'hq_state', 'established_year',
                  'is_listed', 'stock_symbol', 'employee_count', 'branch_count',
                  'loan_tags', 'operating_states', 'rbi_registration']
        return {f: {'confidence': getattr(self, f).confidence,
                    'source':     getattr(self, f).source}
                for f in fields}

    def set_field(self, name: str, fr: FieldResult):
        current: FieldResult = getattr(self, name)
        if current.is_empty():
            setattr(self, name, fr)
            return
        priority = {HIGH: 3, MEDIUM: 2, LOW: 1}
        if priority.get(fr.confidence, 0) > priority.get(current.confidence, 0):
            setattr(self, name, fr)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

MAX_PAGES             = 5
MIN_EMPLOYEE_COUNT    = 1000
SMALL_STATE_THRESHOLD = 5
MIN_DISCOVERY_TO_SKIP_FALLBACK = 3

# NEW 2: Keywords that indicate this is actually a lending company
# If NONE of these appear in the full page text → flag as non-lender
LENDING_SIGNAL_KEYWORDS = [
    'loan', 'lending', 'finance', 'credit', 'borrow', 'nbfc',
    'mortgage', 'interest rate', 'emi', 'disburs', 'repay',
    'lender', 'microloan', 'microfinance', 'factoring',
]

LINK_SCORES: dict[str, int] = {
    'loan': 10, 'product': 10, 'borrow': 9, 'finance': 8, 'lending': 8,
    'contact': 7, 'reach': 6,
    'about': 5, 'company': 5, 'overview': 4,
    'branch': 9, 'location': 8, 'network': 7, 'presence': 6, 'offices': 6,
    'investor': 4, 'annual': 3,
}

FALLBACK_PATHS = [
    'about-us', 'loans', 'products', 'contact-us',
    'branch-locator', 'network', 'investor-relations',
]

LOAN_KEYWORD_MAP: dict[str, list[str]] = {
    'MSME Loan':             ['msme', 'small business', 'sme loan', 'small enterprise'],
    'Personal Loan':         ['personal loan', 'individual loan', 'consumer loan'],
    'Home Loan':             ['home loan', 'housing loan', 'mortgage'],
    'Business Loan':         ['business loan', 'corporate loan', 'enterprise loan'],
    'Vehicle Loan':          ['vehicle loan', 'car loan', 'auto loan', 'four wheeler'],
    'Gold Loan':             ['gold loan', 'loan against gold'],
    'Education Loan':        ['education loan', 'student loan'],
    'Micro Loan':            ['micro loan', 'microloan', 'jlg', 'joint liability'],
    'Loan Against Property': ['loan against property', 'lap ', 'property loan'],
    'Working Capital':       ['working capital', 'cash credit', 'overdraft'],
    'Agriculture Loan':      ['agriculture loan', 'agri loan', 'kisan', 'crop loan'],
    'Credit Card':           ['credit card'],
    'Consumer Durable Loan': ['consumer durable', 'appliance loan'],
    'EV Loan':               ['ev loan', 'electric vehicle', 'electric bike'],
    'Two Wheeler Loan':      ['two wheeler', 'bike loan', '2 wheeler'],
    'Microfinance':          ['microfinance', 'micro finance', 'shg'],
    'Rural Loan':            ['rural loan', 'rural lending', 'gram', 'village loan'],
    'Supply Chain Finance':  ['supply chain', 'invoice discounting', 'factoring'],
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
STATE_LOWER = {s.lower(): s for s in ALL_INDIA_STATES}
STATE_SET   = set(ALL_INDIA_STATES)

CIN_STATE_CODES: dict[str, str] = {
    'MH': 'Maharashtra', 'GJ': 'Gujarat', 'DL': 'Delhi',
    'KA': 'Karnataka',   'TN': 'Tamil Nadu', 'TS': 'Telangana',
    'AP': 'Andhra Pradesh', 'KL': 'Kerala', 'RJ': 'Rajasthan',
    'UP': 'Uttar Pradesh', 'WB': 'West Bengal', 'PB': 'Punjab',
    'HR': 'Haryana', 'MP': 'Madhya Pradesh', 'OR': 'Odisha',
    'AS': 'Assam', 'BR': 'Bihar', 'JH': 'Jharkhand',
    'CH': 'Chandigarh', 'GA': 'Goa', 'UK': 'Uttarakhand',
    'HP': 'Himachal Pradesh', 'CG': 'Chhattisgarh',
}

CITY_STATE_MAP: dict[str, str] = {
    'mumbai': 'Maharashtra', 'pune': 'Maharashtra', 'nashik': 'Maharashtra',
    'nagpur': 'Maharashtra', 'thane': 'Maharashtra', 'aurangabad': 'Maharashtra',
    'bengaluru': 'Karnataka', 'bangalore': 'Karnataka', 'mysuru': 'Karnataka',
    'mysore': 'Karnataka', 'hubli': 'Karnataka', 'mangalore': 'Karnataka',
    'hyderabad': 'Telangana', 'secunderabad': 'Telangana',
    'vijayawada': 'Andhra Pradesh', 'visakhapatnam': 'Andhra Pradesh',
    'chennai': 'Tamil Nadu', 'coimbatore': 'Tamil Nadu', 'madurai': 'Tamil Nadu',
    'delhi': 'Delhi', 'new delhi': 'Delhi', 'noida': 'Uttar Pradesh',
    'gurugram': 'Haryana', 'gurgaon': 'Haryana', 'faridabad': 'Haryana',
    'ghaziabad': 'Uttar Pradesh',
    'ahmedabad': 'Gujarat', 'surat': 'Gujarat', 'vadodara': 'Gujarat',
    'rajkot': 'Gujarat', 'gandhinagar': 'Gujarat',
    'jaipur': 'Rajasthan', 'jodhpur': 'Rajasthan', 'udaipur': 'Rajasthan',
    'kota': 'Rajasthan', 'ajmer': 'Rajasthan',
    'lucknow': 'Uttar Pradesh', 'kanpur': 'Uttar Pradesh', 'agra': 'Uttar Pradesh',
    'varanasi': 'Uttar Pradesh', 'patna': 'Bihar', 'ranchi': 'Jharkhand',
    'kolkata': 'West Bengal', 'calcutta': 'West Bengal',
    'bhopal': 'Madhya Pradesh', 'indore': 'Madhya Pradesh',
    'bhubaneswar': 'Odisha', 'kochi': 'Kerala', 'thiruvananthapuram': 'Kerala',
    'guwahati': 'Assam', 'chandigarh': 'Chandigarh',
    'dehradun': 'Uttarakhand', 'shimla': 'Himachal Pradesh',
    'raipur': 'Chhattisgarh', 'panaji': 'Goa',
}

# ─────────────────────────────────────────────────────────────
# NEW 1: RBI REGISTRATION NUMBER PATTERNS
# ─────────────────────────────────────────────────────────────
# Covers formats seen in the wild:
#   N.13.02437         (NBFC standard)
#   N-13-02437         (dash variant)
#   B-01.00001         (bank format)
#   N-13.00001         (mixed)
#   DNBS(PD)CC.No./14  (RBI circular style — rare)
#   14.00123           (numeric only)
#   B-02.00049         (cooperative bank)

RBI_REG_PATTERNS = [
    # Standard NBFC: N.XX.XXXXX or N-XX-XXXXX
    r'\bN[\.\-]\d{2}[\.\-]\d{4,6}\b',
    # Bank format: B-XX.XXXXX
    r'\bB[\.\-]\d{2}[\.\-]\d{4,6}\b',
    # Preceded by label
    r'RBI\s+(?:registration|reg\.?|certificate)\s*(?:no\.?|number|#)?\s*[:\-]?\s*([A-Z0-9][\w\.\-]{4,20})',
    r'registration\s+no\.?\s*[:\-]\s*([A-Z0-9][\w\.\-]{4,20})',
    r'NBFC\s+(?:registration|reg\.?)\s*(?:no\.?|number|#)?\s*[:\-]?\s*([A-Z0-9][\w\.\-]{4,20})',
    # Certificate of Registration (CoR) format
    r'(?:certificate\s+of\s+registration|CoR)\s*(?:no\.?|number|#)?\s*[:\-]?\s*([A-Z0-9][\w\.\-]{4,20})',
    # Numeric-only registration: 14.XXXXX
    r'\b\d{2}\.\d{5}\b',
    # CIN number (also useful for identification)
    r'\b[LUu]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b',
]


# After extraction, candidates must match one of these real RBI formats.
# This prevents partial-word grabs like "ISTERED" (from REGISTERED) or
# "PORATE" (from CORPORATE) slipping through label-based patterns.
_VALID_RBI_COMPILED = [
    re.compile(r'^[NB][\.\-]\d{2}[\.\-]\d{4,6}$'),           # N.13.02437 / B-01.00001
    re.compile(r'^\d{2}\.\d{5}$'),                             # 14.00123
    re.compile(r'^[LUu]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$'),   # CIN: L65993MH1983PLC342502
    re.compile(r'^DNBS', re.IGNORECASE),                       # DNBS(PD)CC-14 style
]

def _is_valid_rbi_format(candidate: str) -> bool:
    return any(p.match(candidate) for p in _VALID_RBI_COMPILED)


def extract_rbi_registration(text: str) -> str:
    """
    NEW 1: Extract RBI registration number from full page text.
    Returns the registration number string or empty string.
    """
    for pattern in RBI_REG_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            raw = matches[0] if isinstance(matches[0], str) else matches[0]
            raw = raw.strip().upper()
            # Validate against known real RBI formats — reject partial words
            # like "ISTERED" (REGISTERED) or "PORATE" (CORPORATE)
            if _is_valid_rbi_format(raw):
                return raw
    return ''


# ─────────────────────────────────────────────────────────────
# NEW 2: NON-LENDING COMPANY DETECTOR
# ─────────────────────────────────────────────────────────────

def is_non_lending_company(full_page_text: str) -> bool:
    """
    NEW 2: Returns True if the website shows NO lending signals.

    Logic:
      Scan the full text for any lending-related keyword.
      If none are found, this is likely a shell company, consultancy,
      or holding company — not an active lender.

    Note: This is a LOW threshold check (any single signal passes).
    We only flag as non-lender if ZERO signals are present.
    False negatives (missing a real lender) are worse than false
    positives (passing a non-lender to guardrails for further checks).
    """
    text_lower = full_page_text.lower()
    return not any(kw in text_lower for kw in LENDING_SIGNAL_KEYWORDS)


# ─────────────────────────────────────────────────────────────
# PHONE NORMALIZATION (existing)
# ─────────────────────────────────────────────────────────────

def normalize_phone(raw: str) -> str:
    if raw.startswith('+'):
        return '+' + re.sub(r'[^\d]', '', raw)
    return re.sub(r'[^\d]', '', raw)


def is_valid_phone(normalized: str) -> bool:
    digits = re.sub(r'[^\d]', '', normalized)
    return 10 <= len(digits) <= 13


PHONE_PATTERNS: list[tuple[str, str, str]] = [
    (r'\b1800[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b',                    HIGH,   'toll_free'),
    (r'\+91[\s\-]?\d{4}[\s\-]?\d{3}[\s\-]?\d{3}\b',              MEDIUM, 'intl_4_3_3'),
    (r'\+91[\s\-]?\d{5}[\s\-]?\d{5}\b',                           MEDIUM, 'intl_5_5'),
    (r'\+91[6-9]\d{9}\b',                                           MEDIUM, 'intl_compact'),
    (r'\b91[\s\-][6-9]\d{9}\b',                                    MEDIUM, 'country_prefix'),
    (r'\b0\d{2,4}[\s\-]\d{6,8}\b',                                 MEDIUM, 'landline_std'),
    (r'\b[6-9]\d{9}\b',                                             LOW,    'mobile_plain'),
]


def find_phone_in_text(text: str, section_name: str = 'full') -> tuple[str, str, str]:
    for pattern, confidence, source in PHONE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw        = m.group().strip()
            normalized = normalize_phone(raw)
            if is_valid_phone(normalized):
                return normalized, confidence, f'{section_name}_{source}'
    return '', LOW, 'none'


# ─────────────────────────────────────────────────────────────
# ADDRESS EXTRACTION (existing)
# ─────────────────────────────────────────────────────────────

def extract_city_state(text: str) -> tuple[str, str]:
    if not text:
        return '', ''

    # CIN code
    cin = re.search(r'\b[LUu]\d{5}([A-Z]{2})\d{4}', text, re.IGNORECASE)
    if cin and cin.group(1).upper() in CIN_STATE_CODES:
        return '', CIN_STATE_CODES[cin.group(1).upper()]

    # "City, State" patterns
    addr_pat = re.compile(
        r'([A-Z][a-z]{2,20})\s*[,\-]\s*([A-Z][a-z]{2,20}(?:\s[A-Z][a-z]{2,20})?)',
        re.IGNORECASE
    )
    for m in addr_pat.finditer(text):
        city_raw  = m.group(1).strip().lower()
        state_raw = m.group(2).strip()
        canonical = STATE_LOWER.get(state_raw.lower())
        if canonical:
            return CITY_STATE_MAP.get(city_raw, city_raw.title()), canonical
        if city_raw in CITY_STATE_MAP:
            return city_raw.title(), CITY_STATE_MAP[city_raw]

    # "headquartered in X" / "based in X"
    for pat in [
        r'headquartered\s+in\s+([A-Za-z][a-z ]{2,25})',
        r'head\s*quarter[s]?\s*[:\-|]\s*([A-Za-z][a-z ]{2,25})',
        r'registered\s+office\s*[:\-]\s*([A-Za-z][a-z ]{2,25})',
        r'corporate\s+office\s*[:\-]\s*([A-Za-z][a-z ]{2,25})',
        r'based\s+(?:in|out\s+of)\s+([A-Za-z][a-z ]{2,25})',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            loc = m.group(1).strip().lower()
            if STATE_LOWER.get(loc):
                return '', STATE_LOWER[loc]
            if loc in CITY_STATE_MAP:
                return loc.title(), CITY_STATE_MAP[loc]

    # City name lookup
    text_lower = text.lower()
    for city, state in CITY_STATE_MAP.items():
        if re.search(r'\b' + re.escape(city) + r'\b', text_lower):
            return city.title(), state

    # State name lookup
    for state_lower_key, state in STATE_LOWER.items():
        if re.search(r'\b' + re.escape(state_lower_key) + r'\b', text_lower):
            return '', state

    return '', ''


def extract_address_blocks(response) -> list[str]:
    blocks = []
    selectors = [
        'footer', 'footer address', '[class*="footer"]', '[id*="footer"]',
        '[class*="contact"]', '[id*="contact"]', 'address',
        '[class*="address"]', '[class*="location"]',
    ]
    for selector in selectors:
        try:
            texts = response.css(f'{selector}::text').getall()
            block = ' '.join(t.strip() for t in texts if t.strip())
            if len(block) > 10:
                blocks.append(block)
        except Exception:
            continue
    return blocks


# ─────────────────────────────────────────────────────────────
# JSON-LD EXTRACTOR — HIGH confidence
# ─────────────────────────────────────────────────────────────

class JsonLdExtractor:
    def extract(self, response, result: ScrapedLender):
        try:
            scripts = response.css('script[type="application/ld+json"]::text').getall()
        except Exception:
            return
        for script in scripts:
            try:
                data  = json.loads(script.strip())
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict):
                        self._parse(item, result)
            except Exception:
                continue

    def _parse(self, item: dict, result: ScrapedLender):
        phone = item.get('telephone') or item.get('phone')
        if phone:
            norm = normalize_phone(str(phone).strip())
            if is_valid_phone(norm):
                result.set_field('phone', FieldResult(norm, HIGH, 'json_ld'))

        email = item.get('email')
        if email and '@' in str(email):
            result.set_field('email', FieldResult(str(email).strip().lower(), HIGH, 'json_ld'))

        addr = item.get('address') or item.get('location') or {}
        if isinstance(addr, dict):
            city  = addr.get('addressLocality') or addr.get('city', '')
            state = addr.get('addressRegion')   or addr.get('state', '')
            if city:
                result.set_field('hq_city',  FieldResult(str(city).strip().title(),  HIGH, 'json_ld'))
            if state:
                canonical = STATE_LOWER.get(str(state).strip().lower(), '')
                if canonical:
                    result.set_field('hq_state', FieldResult(canonical, HIGH, 'json_ld'))

        founding = item.get('foundingDate') or item.get('foundingYear')
        if founding:
            try:
                yr = int(str(founding)[:4])
                import datetime as _dt
                if 1850 <= yr <= _dt.datetime.now().year:
                    result.set_field('established_year', FieldResult(yr, HIGH, 'json_ld'))
            except Exception:
                pass

        emp = item.get('numberOfEmployees')
        if isinstance(emp, dict):
            emp = emp.get('value') or emp.get('minValue')
        if emp:
            try:
                n = int(float(str(emp).replace(',', '')))
                if n >= MIN_EMPLOYEE_COUNT:
                    result.set_field('employee_count', FieldResult(n, HIGH, 'json_ld'))
            except Exception:
                pass

        ticker = item.get('tickerSymbol') or item.get('ticker')
        if ticker:
            result.set_field('stock_symbol', FieldResult(str(ticker).strip().upper(), HIGH, 'json_ld'))


# ─────────────────────────────────────────────────────────────
# DOM EXTRACTOR — MEDIUM confidence
# ─────────────────────────────────────────────────────────────

class DomExtractor:
    def extract(self, response, result: ScrapedLender):
        self._phone_from_links(response, result)
        self._email_from_links(response, result)
        self._address_from_blocks(response, result)
        self._employees_from_elements(response, result)
        self._states_from_select(response, result)
        self._loan_tags_from_nav(response, result)

    def _phone_from_links(self, response, result: ScrapedLender):
        if not result.phone.is_empty() and result.phone.confidence == HIGH:
            return
        try:
            links = response.css('a[href^="tel"]::attr(href)').getall()
            for link in links:
                norm = normalize_phone(link.replace('tel:', '').strip())
                if is_valid_phone(norm):
                    result.set_field('phone', FieldResult(norm, MEDIUM, 'dom_tel_link'))
                    return
        except Exception:
            pass

    def _email_from_links(self, response, result: ScrapedLender):
        if not result.email.is_empty() and result.email.confidence == HIGH:
            return
        try:
            links = response.css('a[href^="mailto"]::attr(href)').getall()
            for link in links:
                email = link.replace('mailto:', '').strip().lower()
                if '@' in email and '.' in email.split('@')[-1]:
                    if not any(b in email for b in ['noreply', 'no-reply', 'example']):
                        result.set_field('email', FieldResult(email, MEDIUM, 'dom_mailto'))
                        return
        except Exception:
            pass

    def _address_from_blocks(self, response, result: ScrapedLender):
        for block in extract_address_blocks(response):
            city, state = extract_city_state(block)
            if state and result.hq_state.is_empty():
                result.set_field('hq_state', FieldResult(state, MEDIUM, 'dom_address_block'))
            if city and result.hq_city.is_empty():
                result.set_field('hq_city', FieldResult(city, MEDIUM, 'dom_address_block'))
            if not result.hq_state.is_empty():
                break

    def _employees_from_elements(self, response, result: ScrapedLender):
        if not result.employee_count.is_empty() and result.employee_count.confidence == HIGH:
            return
        try:
            for sel in ['[class*="employee"]', '[class*="team"]', '[class*="workforce"]']:
                try:
                    for t in response.css(f'{sel}::text').getall():
                        m = re.search(r'(\d[\d,]+)', t)
                        if m:
                            n = int(m.group(1).replace(',', ''))
                            if n >= MIN_EMPLOYEE_COUNT:
                                result.set_field('employee_count', FieldResult(n, MEDIUM, f'dom_{sel}'))
                                return
                except Exception:
                    continue
        except Exception:
            pass

    def _states_from_select(self, response, result: ScrapedLender):
        try:
            found: set[str] = set()
            for opt in response.css('select option::text').getall():
                opt_c = opt.strip().lower()
                if opt_c in STATE_LOWER:
                    found.add(STATE_LOWER[opt_c])
            if found:
                existing = set(result.operating_states.value or [])
                result.set_field('operating_states',
                                 FieldResult(sorted(existing | found), MEDIUM, 'dom_select'))
        except Exception:
            pass

    def _loan_tags_from_nav(self, response, result: ScrapedLender):
        try:
            nav_text = ' '.join(
                response.css('nav::text, [class*="nav"]::text, [class*="menu"]::text').getall()
            ).lower()
            if not nav_text.strip():
                return
            found = []
            for tag, keywords in LOAN_KEYWORD_MAP.items():
                if any(kw in nav_text for kw in keywords):
                    found.append(tag)
            if found:
                existing = list(result.loan_tags.value or [])
                result.set_field('loan_tags',
                                 FieldResult(sorted(set(existing + found)), MEDIUM, 'dom_nav'))
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# TEXT EXTRACTOR — LOW confidence + NEW 1 + NEW 2
# ─────────────────────────────────────────────────────────────

class TextExtractor:
    def extract(self, response, result: ScrapedLender):
        try:
            # P1/P2 FIX: Always use full page text — never rely on HTML structure
            raw  = response.get_all_text(ignore_tags=('script', 'style', 'noscript', 'head'))
            text = raw or ''
        except Exception:
            return

        self._phone(text, result)
        self._email(text.lower(), result)
        self._hq(text, result)
        self._year(text.lower(), result)
        self._listing(text.lower(), result)
        self._employees(text.lower(), result)
        self._branches(text.lower(), result)
        self._loan_tags(text.lower(), result)
        self._states(text.lower(), result)
        self._rural(text.lower(), result)
        self._rbi_registration(text, result)       # NEW 1
        self._non_lender_check(text, result)       # NEW 2

    def _phone(self, text: str, result: ScrapedLender):
        if not result.phone.is_empty():
            return
        # Section-first: contact/footer sections more reliable
        section_keywords = ['contact', 'footer', 'reach us', 'get in touch',
                            'call us', 'helpline', 'phone', 'telephone', 'toll free']
        text_lower = text.lower()
        for keyword in section_keywords:
            idx = text_lower.find(keyword)
            if idx == -1:
                continue
            section = text[max(0, idx - 30): idx + 300]
            phone, _, source = find_phone_in_text(section, f'section_{keyword}')
            if phone:
                result.set_field('phone', FieldResult(phone, MEDIUM, source))
                return
        # Full page fallback
        phone, conf, source = find_phone_in_text(text, 'full_page')
        if phone:
            result.set_field('phone', FieldResult(phone, LOW, source))

    def _email(self, text: str, result: ScrapedLender):
        if not result.email.is_empty():
            return
        BLOCKED = ['noreply', 'no-reply', 'example', 'test@', 'dummy@', 'donotreply', 'bounce']
        PREFERRED = ['contact@', 'info@', 'care@', 'support@', 'helpdesk@', 'enquiry@', 'query@']
        all_emails = re.findall(r'[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', text)
        valid = [e for e in all_emails if not any(b in e for b in BLOCKED)]
        if not valid:
            return
        # Prefer contact/info emails; fall back to first valid
        preferred = next((e for e in valid if any(p in e for p in PREFERRED)), None)
        result.set_field('email', FieldResult(preferred or valid[0], LOW, 'regex'))

    def _hq(self, text: str, result: ScrapedLender):
        if not result.hq_city.is_empty() and not result.hq_state.is_empty():
            return
        city, state = extract_city_state(text)
        if state and result.hq_state.is_empty():
            result.set_field('hq_state', FieldResult(state, LOW, 'text_parse'))
        if city and result.hq_city.is_empty():
            result.set_field('hq_city', FieldResult(city, LOW, 'text_parse'))

    def _year(self, text: str, result: ScrapedLender):
        if not result.established_year.is_empty():
            return
        for pat in [
            r'(?:established|founded|incorporated|since|est\.?)\s+(?:in\s+)?(\d{4})',
            r'(\d{4})\s*[-–]\s*(?:present|today|now)',
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    yr = int(m.group(1))
                    import datetime as _dt
                    if 1850 <= yr <= _dt.datetime.now().year:
                        result.set_field('established_year', FieldResult(yr, LOW, 'regex'))
                        return
                except ValueError:
                    pass

    def _listing(self, text: str, result: ScrapedLender):
        if any(s in text for s in ['nse:', 'bse:', 'listed on nse', 'listed on bse',
                                    'stock exchange', 'market cap']):
            result.set_field('is_listed', FieldResult(True, LOW, 'regex'))
            if result.stock_symbol.is_empty():
                for pat in [r'nse\s*[:|]\s*([a-z]{2,10})', r'symbol\s*[:|]\s*([a-z]{2,10})']:
                    m = re.search(pat, text, re.IGNORECASE)
                    if m:
                        result.set_field('stock_symbol',
                                         FieldResult(m.group(1).strip().upper(), LOW, 'regex'))
                        break

    def _employees(self, text: str, result: ScrapedLender):
        if not result.employee_count.is_empty():
            return
        for pat in [
            r'(\d[\d,]+)\s*\+?\s*(?:employees|team\s+members|workforce|staff)',
            r'(?:over|more\s+than)\s+(\d[\d,]+)\s+(?:employees|people)',
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    n = int(m.group(1).replace(',', ''))
                    if n >= MIN_EMPLOYEE_COUNT:
                        result.set_field('employee_count', FieldResult(n, LOW, 'regex'))
                        return
                except ValueError:
                    pass

    def _branches(self, text: str, result: ScrapedLender):
        if not result.branch_count.is_empty():
            return
        for pat in [
            r'(\d[\d,]+)\s*\+?\s*(?:branches|branch\s+offices|locations|touch\s+points)',
            r'(?:over|more\s+than)\s+(\d[\d,]+)\s*branches',
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    result.set_field('branch_count',
                                     FieldResult(int(m.group(1).replace(',', '')), LOW, 'regex'))
                    return
                except ValueError:
                    pass

    def _loan_tags(self, text: str, result: ScrapedLender):
        existing = set(result.loan_tags.value or [])
        found    = list(existing)
        for tag, keywords in LOAN_KEYWORD_MAP.items():
            if tag not in existing and any(kw in text for kw in keywords):
                found.append(tag)
        if found:
            result.set_field('loan_tags', FieldResult(sorted(set(found)), LOW, 'regex'))

    def _states(self, text: str, result: ScrapedLender):
        found = set(result.operating_states.value or [])
        for state_lower_key, state in STATE_LOWER.items():
            if re.search(r'\b' + re.escape(state_lower_key) + r'\b', text):
                found.add(state)
        if found:
            result.set_field('operating_states', FieldResult(sorted(found), LOW, 'regex'))

    def _rural(self, text: str, result: ScrapedLender):
        if any(s in text for s in ['rural', 'semi-urban', 'village', 'kisan',
                                    'tier 2', 'tier 3', 'taluk']):
            result.set_field('rural_presence', FieldResult(True, LOW, 'regex'))

    def _rbi_registration(self, text: str, result: ScrapedLender):
        """NEW 1: Extract RBI registration number from full page text."""
        if not result.rbi_registration.is_empty():
            return
        reg = extract_rbi_registration(text)
        if reg:
            result.set_field('rbi_registration', FieldResult(reg, MEDIUM, 'regex'))
            logger.debug(f'RBI registration found: {reg}')

    def _non_lender_check(self, text: str, result: ScrapedLender):
        """NEW 2: Flag if page shows no lending signals at all.

        Called once per scraped page. A SINGLE page with lending keywords
        is enough to clear the flag — avoids false positives on sites
        where the homepage is thin but product/loan pages have signals.
        """
        if is_non_lending_company(text):
            # Only set True if no earlier page already cleared it
            if not hasattr(result, '_lending_signal_found') or not result._lending_signal_found:
                result.is_non_lender = True
                logger.info(f'Non-lending page detected: {result.lender_name}')
        else:
            # This page has lending signals — company is definitely a lender
            result.is_non_lender        = False
            result._lending_signal_found = True
            logger.debug(f'Lending signals confirmed: {result.lender_name}')


# ─────────────────────────────────────────────────────────────
# LINK DISCOVERER
# ─────────────────────────────────────────────────────────────

class LinkDiscoverer:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.domain   = urlparse(base_url).netloc
        self._seen:   set[str] = set()

    def discover_from(self, response, max_links: int = MAX_PAGES) -> list[str]:
        try:
            raw_links = response.css('a::attr(href)').getall()
        except Exception:
            return self._fallback()

        scored: dict[str, int] = {}
        for href in raw_links:
            if not href:
                continue
            full   = urljoin(self.base_url, href.strip())
            parsed = urlparse(full)
            if parsed.netloc and parsed.netloc != self.domain: continue
            if href.startswith(('#', 'mailto:', 'tel:', 'javascript:')): continue
            if any(ext in full.lower() for ext in ['.pdf', '.jpg', '.png', '.zip']): continue

            clean = f'{parsed.scheme}://{parsed.netloc}{parsed.path}'.rstrip('/')
            if clean == self.base_url or clean in self._seen: continue
            if not clean.startswith(self.base_url): continue

            path_lower = parsed.path.lower()
            score      = max((pts for kw, pts in LINK_SCORES.items() if kw in path_lower), default=0)
            if score > 0:
                scored[clean] = max(scored.get(clean, 0), score)

        top = [url for url, _ in sorted(scored.items(), key=lambda x: x[1], reverse=True)[:max_links]]
        self._seen.update(top)

        if len(top) < MIN_DISCOVERY_TO_SKIP_FALLBACK:
            top += [u for u in self._fallback() if u not in self._seen]

        return top[:max_links]

    def _fallback(self) -> list[str]:
        urls = [f'{self.base_url}/{p}' for p in FALLBACK_PATHS
                if f'{self.base_url}/{p}' not in self._seen]
        self._seen.update(urls)
        return urls


# ─────────────────────────────────────────────────────────────
# RETRY FETCHER
# ─────────────────────────────────────────────────────────────

class RetryFetcher:
    def __init__(self, use_stealth: bool = False, max_retries: int = 3):
        self.max_retries = max_retries
        self.use_stealth = use_stealth
        self._fetcher    = None
        self.metrics     = {
            'requests_total': 0, 'requests_success': 0,
            'requests_failed': 0, 'retries_total': 0,
        }

    def _get_fetcher(self):
        if self._fetcher is None:
            try:
                if self.use_stealth:
                    from scrapling.fetchers import StealthyFetcher
                    self._fetcher = StealthyFetcher
                else:
                    from scrapling.fetchers import Fetcher
                    self._fetcher = Fetcher
            except ImportError as e:
                raise ImportError(f'Scrapling not installed: {e}')
        return self._fetcher

    def get(self, url: str):
        Fetcher = self._get_fetcher()
        self.metrics['requests_total'] += 1
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = (Fetcher.fetch(url, headless=True, timeout=30)
                        if self.use_stealth
                        else Fetcher.get(url, timeout=20, stealthy_headers=True))
                if resp and resp.status < 500:
                    self.metrics['requests_success'] += 1
                    return resp
                if attempt < self.max_retries:
                    self.metrics['retries_total'] += 1
                    time.sleep(2 ** attempt)
            except Exception as e:
                err = str(e).lower()
                if 'ssl' in err or 'certificate' in err:
                    logger.warning(f'SSL error for {url} — retrying without verification (insecure)')
                    try:
                        from scrapling.fetchers import Fetcher as F
                        resp = F.get(url, timeout=20, stealthy_headers=True, verify=False)
                        if resp:
                            self.metrics['requests_success'] += 1
                            return resp
                    except Exception:
                        pass
                if attempt < self.max_retries:
                    self.metrics['retries_total'] += 1
                    time.sleep(2 ** attempt)
        self.metrics['requests_failed'] += 1
        return None


# ─────────────────────────────────────────────────────────────
# MAIN SCRAPER
# ─────────────────────────────────────────────────────────────

class SingleLenderScraper:
    def __init__(self, use_stealth: bool = False):
        self.fetcher  = RetryFetcher(use_stealth=use_stealth)
        self.jld      = JsonLdExtractor()
        self.dom      = DomExtractor()
        self.text_ext = TextExtractor()

    @property
    def metrics(self) -> dict:
        return self.fetcher.metrics

    def scrape(self, lender_name: str, website: str) -> dict:
        result_obj = self._scrape_internal(lender_name, website)
        flat = result_obj.to_dict()
        flat['_confidence'] = result_obj.confidence_report()
        logger.info(
            f'SCRAPED [{lender_name}] | '
            f'tags={len(flat.get("loan_tags", []))} '
            f'states={len(flat.get("operating_states", []))} '
            f'phone={"✓" if flat.get("phone") else "✗"} '
            f'email={"✓" if flat.get("email") else "✗"} '
            f'state={flat.get("hq_state") or "?"} '
            f'rbi_reg={"✓" if flat.get("rbi_registration") else "✗"} '   # NEW 1
            f'non_lender={flat.get("is_non_lender")}'                     # NEW 2
        )
        return flat

    def _scrape_internal(self, lender_name: str, website: str) -> ScrapedLender:
        result = ScrapedLender(lender_name=lender_name)
        if not website or not website.strip():
            return result

        website = website.strip()
        if not website.startswith(('http://', 'https://')):
            website = 'https://' + website
        website = website.rstrip('/')

        candidates = [website]
        if '://www.' not in website:
            candidates.append(website.replace('://', '://www.'))
        else:
            candidates.append(website.replace('://www.', '://'))

        home_resp, base = None, None
        for c in candidates:
            r = self.fetcher.get(c)
            if r and r.status < 400:
                home_resp, base = r, c
                break

        if not base or home_resp is None:
            logger.warning(f'Could not reach {website}')
            return result

        result.website = FieldResult(base, HIGH, 'direct')
        self._extract_page(home_resp, result)

        discoverer    = LinkDiscoverer(base)
        pages         = discoverer.discover_from(home_resp, max_links=MAX_PAGES)
        pages_scraped = 0

        for url in pages:
            if pages_scraped >= MAX_PAGES:
                break
            resp = self.fetcher.get(url)
            if not resp or resp.status >= 400:
                continue
            self._extract_page(resp, result)
            pages_scraped += 1

            if any(kw in url.lower() for kw in ('loan', 'product', 'branch', 'location')):
                for sub_url in discoverer.discover_from(resp, max_links=2):
                    if pages_scraped >= MAX_PAGES:
                        break
                    sub_resp = self.fetcher.get(sub_url)
                    if sub_resp and sub_resp.status < 400:
                        self._extract_page(sub_resp, result)
                        pages_scraped += 1
                    time.sleep(0.8)

            time.sleep(1.0)

        state_count  = len(result.operating_states.value or [])
        branch_count = result.branch_count.value or 0
        emp_count    = result.employee_count.value or 0
        if state_count < SMALL_STATE_THRESHOLD and (branch_count > 200 or emp_count > 5000):
            result.potential_pan_india = True

        # Final non-lender override: concrete extracted data beats per-page text check.
        # If we found loan products or an RBI registration on any page, it's a lender.
        if result.is_non_lender:
            has_tags = bool(result.loan_tags.value)
            has_rbi  = bool(result.rbi_registration.value)
            has_states = bool(result.operating_states.value)
            if has_tags or has_rbi or has_states:
                result.is_non_lender = False
                logger.info(
                    f'Non-lender flag cleared for {result.lender_name} '
                    f'— concrete evidence found (tags={has_tags} rbi={has_rbi} states={has_states})'
                )

        return result

    def _extract_page(self, response, result: ScrapedLender):
        self.jld.extract(response, result)
        self.dom.extract(response, result)
        self.text_ext.extract(response, result)


# ─────────────────────────────────────────────────────────────
# QUICK TEST
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    for noisy in ('scrapling', 'curl_cffi', 'camoufox'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    print('\nScraping 121 Finance...\n')
    scraper = SingleLenderScraper(use_stealth=False)
    result  = scraper.scrape('121 Finance', 'https://121finance.com')

    print('\n' + '=' * 65)
    for k, v in result.items():
        if k != '_confidence':
            print(f'  {k:25}: {v}')

    print('\n' + '=' * 65)
    print('KEY CHECKS:')
    checks = {
        'phone':            bool(result.get('phone')),
        'email':            bool(result.get('email')),
        'hq_state':         bool(result.get('hq_state')),
        'loan_tags':        len(result.get('loan_tags', [])) > 0,
        'rbi_registration': bool(result.get('rbi_registration')),   # NEW 1
        'is_lender':        not result.get('is_non_lender'),        # NEW 2
    }
    for check, passed in checks.items():
        print(f'  {"✅" if passed else "❌"} {check}')