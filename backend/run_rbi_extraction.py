"""
run_rbi_extraction_PRODUCTION.py — RBI Banks Extraction (Production Ready)
============================================================================
Extracts all 19 fields from RBI banks using Gemini API with Google Search
"""

import os, csv, json, time, sys, re
import requests
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, asdict

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════

ROOT       = Path(__file__).parent.parent
RBI_DIR    = ROOT / 'data' / 'input' / 'rbi_banks_output'
OUTPUT_DIR = ROOT / 'data' / 'output'
OUTPUT     = OUTPUT_DIR / 'rbi_banks_extracted_v8.csv'

GEMINI_KEY  = os.getenv('GEMINI_API_KEY', '')

# Use v1 endpoint (not v1beta) and specific working models
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1/models'

# Models that actually work (from your find_model.py)
WORKING_MODELS = [
    'gemini-2.5-flash',
    'gemini-flash-latest',
    'gemini-pro-latest',
]

MAX_RETRIES     = 3
RETRY_DELAY     = 5
RATE_LIMIT_WAIT = 60

# ══════════════════════════════════════════════════════════════
# DATA STRUCTURES
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

CATEGORY_MAP = {
    'Private Sector Banks – Indian Banks': 'Private Bank',
    'Private Sector Banks – Foreign Banks': 'Foreign Bank',
    'Private Sector Banks - Indian Banks': 'Private Bank',
    'Private Sector Banks - Foreign Banks': 'Foreign Bank',
    'Foreign Banks Having Presence in India': 'Foreign Bank',
    'Nationalised Banks': 'PSU Bank',
    'State Bank of India (SBI)': 'PSU Bank',
    'State Co-operative Banks': 'Cooperative Bank',
    'Financial Institutions': 'PSU Bank',
    'Local Area Banks (LABs)': 'Private Bank',
    'Regional Rural Banks (RRBs)': 'SKIP',
    'Payments Banks (PBs)': 'SKIP',
}

VALID_PRODUCTS = [
    "MSME Loan", "Personal Loan", "Home Loan", "Business Loan",
    "Vehicle Loan", "Gold Loan", "Education Loan", "Micro Loan",
    "Loan Against Property", "Working Capital", "Agriculture Loan",
    "Credit Card", "Consumer Durable Loan"
]

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
    recent_funding:          str   = ""
    recent_funding_amount:   float = None
    recent_funding_year:     int   = None
    established_year:        int   = None
    employee_count:          int   = None
    ticket_size_min:         float = None
    ticket_size_max:         float = None
    has_subsidiaries:        bool  = False
    phone:                   str   = ""
    email:                   str   = ""
    data_source:             str   = "gemini_rbi"
    extraction_status:       str   = "success"
    error:                   str   = ""

# ══════════════════════════════════════════════════════════════
# GEMINI API FUNCTIONS
# ══════════════════════════════════════════════════════════════

def make_prompt(bank_name, bank_type):
    """Create extraction prompt"""
    products_str = ', '.join(f'"{p}"' for p in VALID_PRODUCTS)
    return (
        f"Extract data about {bank_name} ({bank_type}) from India.\n\n"
        "Return JSON with these fields (use null if unknown):\n\n"
        "{\n"
        '  "aum_crores": number (Total Advances/Loan Book in Crores),\n'
        '  "last_year_revenue": number (total income in Crores),\n'
        '  "is_listed": true/false,\n'
        '  "stock_symbol": "NSE/BSE symbol" or null,\n'
        f'  "primary_loan_segments": array from [{products_str}],\n'
        '  "primary_product": string,\n'
        '  "hq_city": string,\n'
        '  "hq_state": full state name,\n'
        '  "operating_states": ["PAN_INDIA"] or array of states,\n'
        '  "operating_intensity": "Pan India" or "Regional" or "Single State",\n'
        '  "established_year": year,\n'
        '  "employee_count": number,\n'
        '  "website": "https://..."\n'
        "}\n\n"
        "Return ONLY the JSON object, no markdown."
    )

def extract_json(text):
    """Robust JSON extraction from Gemini response"""
    if not text:
        return None
    
    # Try direct parse
    try:
        return json.loads(text.strip())
    except:
        pass
    
    # Remove markdown fences
    if '```' in text:
        text = re.sub(r'```json\n?', '', text)
        text = re.sub(r'```\n?', '', text)
        try:
            return json.loads(text.strip())
        except:
            pass
    
    # Find JSON object by braces
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end+1])
        except:
            pass
    
    return None

def extract_with_gemini(bank_name, bank_type, debug=False):
    """
    Extract data using Gemini API with fallback models
    Returns: Dict or None
    """
    if not GEMINI_KEY:
        return None
    
    for model_name in WORKING_MODELS:
        # Construct URL - try v1 endpoint
        url = f"{GEMINI_URL}/{model_name}:generateContent?key={GEMINI_KEY}"
        
        payload = {
            "contents": [{
                "parts": [{"text": make_prompt(bank_name, bank_type)}]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 2048,
            }
        }
        
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(url, json=payload, timeout=45)
                
                # Model not available
                if resp.status_code == 404:
                    if debug:
                        print(f"    ! {model_name} → 404, trying next...")
                    break
                
                # Rate limit
                if resp.status_code == 429:
                    print(f"    ! Rate limit, waiting {RATE_LIMIT_WAIT}s...")
                    time.sleep(RATE_LIMIT_WAIT)
                    continue
                
                # Server error
                if resp.status_code in (500, 503):
                    if debug:
                        print(f"    ! Server error, retry {attempt}/{MAX_RETRIES}")
                    time.sleep(RETRY_DELAY)
                    continue
                
                # Other error
                if resp.status_code != 200:
                    if debug:
                        print(f"    ! HTTP {resp.status_code}: {resp.text[:100]}")
                    time.sleep(RETRY_DELAY)
                    continue
                
                # Parse response
                try:
                    body = resp.json()
                except:
                    if debug:
                        print(f"    ! Invalid JSON response")
                    time.sleep(RETRY_DELAY)
                    continue
                
                candidates = body.get('candidates', [])
                if not candidates:
                    if debug:
                        print(f"    ! Empty candidates")
                    time.sleep(RETRY_DELAY)
                    continue
                
                # Extract text
                parts = candidates[0].get('content', {}).get('parts', [])
                raw_text = ''
                for part in parts:
                    if part.get('text'):
                        raw_text = part['text']
                        break
                
                if not raw_text:
                    time.sleep(RETRY_DELAY)
                    continue
                
                if debug:
                    print(f"\n    Response ({len(raw_text)} chars): {raw_text[:200]}...")
                
                # Parse JSON from response
                result = extract_json(raw_text)
                if result:
                    if debug:
                        print(f"    ✓ Success with {model_name}")
                    return result
                
                if debug:
                    print(f"    ! JSON parse failed, retry {attempt}/{MAX_RETRIES}")
                time.sleep(RETRY_DELAY)
                
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if debug:
                    print(f"    ! Exception: {str(e)[:100]}")
                time.sleep(RETRY_DELAY)
        
        # Model failed, try next
        if debug:
            print(f"    ✗ {model_name} failed after {MAX_RETRIES} attempts")
    
    return None

# ══════════════════════════════════════════════════════════════
# DATA PROCESSING FUNCTIONS
# ══════════════════════════════════════════════════════════════

def safe_float(v):
    """Parse numbers from various formats"""
    if v is None or v == '':
        return None
    try:
        s = str(v).lower().replace(',', '').replace('₹', '').replace('$', '')
        
        # Handle text units
        if 'lakh crore' in s:
            return float(re.sub(r'[^\d.]', '', s)) * 100000
        if 'crore' in s:
            return float(re.sub(r'[^\d.]', '', s))
        if 'lakh' in s:
            return float(re.sub(r'[^\d.]', '', s)) / 100
        
        return float(re.sub(r'[^\d.]', '', s))
    except:
        return None

def safe_int(v):
    """Parse integers"""
    try:
        return int(re.sub(r'[^\d]', '', str(v))) if v else None
    except:
        return None

def build_lender(name, ctype, data):
    """Build Lender from extracted data"""
    hq_city = str(data.get('hq_city') or '').strip()
    hq_state = str(data.get('hq_state') or '').strip()
    hq_loc = f"{hq_city}, {hq_state}".strip(', ')
    
    # Operating states
    raw_states = data.get('operating_states', [])
    if raw_states == "PAN_INDIA" or raw_states == ["PAN_INDIA"] or ctype in {'Private Bank', 'PSU Bank', 'Foreign Bank'}:
        op_states = ALL_INDIA_STATES
        intensity = "Pan India"
        is_pan = True
    else:
        op_states = raw_states if isinstance(raw_states, list) else []
        is_pan = len(op_states) >= 20
        
        if len(op_states) >= 15:
            intensity = "Pan India"
        elif len(op_states) <= 1:
            intensity = "Single State"
        else:
            intensity = "Regional"
    
    # Override if explicitly provided
    if data.get('operating_intensity'):
        intensity = data['operating_intensity']
    
    # Loan segments
    raw_segments = data.get('primary_loan_segments', [])
    if not raw_segments:
        raw_segments = []
    if isinstance(raw_segments, str):
        raw_segments = [raw_segments]
    segments = [p.strip() for p in raw_segments if p and p.strip() in VALID_PRODUCTS]
    
    return Lender(
        company_name=name,
        company_type=ctype,
        website=str(data.get('website') or '').strip(),
        aum_crores=safe_float(data.get('aum_crores')),
        last_year_revenue=safe_float(data.get('last_year_revenue')),
        is_listed=bool(data.get('is_listed', False)),
        stock_symbol=str(data.get('stock_symbol') or '').strip(),
        primary_loan_segments=json.dumps(segments),
        primary_product=str(data.get('primary_product') or '').strip(),
        product_types=json.dumps(segments),
        hq_location=hq_loc,
        hq_state=hq_state,
        operating_states=json.dumps(op_states),
        operating_intensity=intensity,
        pan_india=is_pan,
        established_year=safe_int(data.get('established_year')),
        employee_count=safe_int(data.get('employee_count')),
        data_source='gemini_rbi',
        extraction_status='success',
    )

def save(results, path):
    """Save to CSV"""
    if not results:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys())
        w.writeheader()
        w.writerows(results)

def load_rbi_banks():
    """Load banks from RBI CSV"""
    master = RBI_DIR / 'master_banks.csv'
    if not master.exists():
        print(f"\n✗ File not found: {master}\n")
        sys.exit(1)
    
    banks = []
    seen = set()
    
    with open(master, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            raw_name = row.get('Bank_Name', '').strip().strip('"')
            cat = row.get('Category', '').strip()
            ctype = CATEGORY_MAP.get(cat, 'SKIP')
            
            if ctype == 'SKIP' or not raw_name or len(raw_name) < 3:
                continue
            
            key = raw_name.lower()[:30]
            if key in seen:
                continue
            
            seen.add(key)
            banks.append({'company_name': raw_name, 'company_type': ctype})
    
    # Add SBI if missing
    if not any(b['company_name'] == 'State Bank of India' for b in banks):
        banks.insert(0, {'company_name': 'State Bank of India', 'company_type': 'PSU Bank'})
    
    return banks

# ══════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ══════════════════════════════════════════════════════════════

def main():
    if not GEMINI_KEY:
        print("\n✗ GEMINI_API_KEY not set\n")
        print("Set it with:")
        print("  Windows: set GEMINI_API_KEY=your_key")
        print("  Mac/Linux: export GEMINI_API_KEY=your_key\n")
        sys.exit(1)
    
    print(f"\n{'='*60}")
    print("  RBI BANKS EXTRACTION (Production)")
    print(f"{'='*60}")
    
    banks = load_rbi_banks()
    print(f"\n  Banks loaded: {len(banks)}")
    print(f"  Est. time: ~{round(len(banks) * 4 / 60, 1)} min")
    print(f"  Output: {OUTPUT}")
    print(f"  Models: {' → '.join(WORKING_MODELS)}")
    print(f"\n{'='*60}\n")
    
    results = []
    ok = fail = 0
    
    try:
        for i, bank in enumerate(banks, 1):
            name = bank['company_name']
            ctype = bank['company_type']
            
            print(f"[{i}/{len(banks)}] {name} ({ctype})")
            
            data = extract_with_gemini(name, ctype, debug=(i <= 3))
            
            if not data:
                results.append(asdict(Lender(
                    company_name=name,
                    company_type=ctype,
                    extraction_status='failed',
                    error='All models failed'
                )))
                fail += 1
                print(f"  ✗ FAILED\n")
            else:
                lender = build_lender(name, ctype, data)
                results.append(asdict(lender))
                ok += 1
                
                aum = data.get('aum_crores', 'N/A')
                print(f"  ✓ AUM: {aum} | {lender.operating_intensity}\n")
            
            # Save progress
            if i % 10 == 0 or i == len(banks):
                save(results, OUTPUT)
                print(f"💾 Progress: {i}/{len(banks)} ({round(i/len(banks)*100)}%) | ✓{ok} ✗{fail}\n")
            
            time.sleep(3)
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        if results:
            save(results, OUTPUT)
            print(f"💾 Saved {len(results)} banks to {OUTPUT}")
        sys.exit(0)
    
    print(f"\n{'='*60}")
    print(f"  ✅ COMPLETE!")
    print(f"  Success: {ok} | Failed: {fail}")
    print(f"  File: {OUTPUT}")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    main()