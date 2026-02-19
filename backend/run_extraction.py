"""
run_extraction.py  — Lender Discovery Platform v4
=================================================
Two modes:
  1. python run_extraction.py banks  → Top 50 banks (WITH validation)
  2. python run_extraction.py nbfcs  → Your verified NBFC CSV (NO validation)
  3. python run_extraction.py all    → Both

Validation rules:
  - Banks:  validated — checks URL is a real bank domain before extracting
  - NBFCs:  NO validation — you provide genuine verified list only
  - State filter: uses operating_states ONLY (not HQ state)
  - Pan-India banks: shown for every state filter
"""

import os, csv, json, time, glob, sys
import requests
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, asdict

# ── Paths ─────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
INPUT_DIR  = ROOT / 'data' / 'input'
OUTPUT_DIR = ROOT / 'data' / 'output'
BANKS_OUT  = OUTPUT_DIR / 'banks_extracted.csv'
NBFCS_OUT  = OUTPUT_DIR / 'nbfcs_extracted.csv'

# ── Gemini ────────────────────────────────────────────────────
GEMINI_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_URL = ('https://generativelanguage.googleapis.com'
              '/v1beta/models/gemini-2.0-flash-exp:generateContent')

# ── All 36 states + UTs ───────────────────────────────────────
ALL_INDIA_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi",
    "Jammu & Kashmir", "Ladakh", "Puducherry", "Chandigarh",
    "Dadra and Nagar Haveli", "Lakshadweep", "Andaman and Nicobar Islands"
]

# ── Bank validation ───────────────────────────────────────────

# Known legitimate Indian bank domains
KNOWN_BANK_DOMAINS = {
    'hdfcbank.com', 'icicibank.com', 'axisbank.com', 'kotak.com',
    'yesbank.in', 'indusind.com', 'idfcfirstbank.com', 'bandhanbank.com',
    'rblbank.com', 'federalbank.co.in', 'southindianbank.com',
    'karnatakabank.com', 'dcbbank.com', 'csb.co.in', 'cityunionbank.com',
    'tmbank.in', 'kvb.co.in', 'dhanbank.com', 'nainitalbank.co.in',
    'aubank.in', 'ujjivansfb.in', 'equitasbank.com', 'janabank.in',
    'esafbank.com', 'suryodaybank.com', 'utkarsh.bank', 'capitalbank.co.in',
    'fincarebank.com', 'nesfb.com', 'idbibank.in', 'jkbank.com',
    'saraswatbank.com', 'hsbc.co.in', 'online.citibank.co.in', 'sc.com',
    'dbs.com', 'db.com', 'barclays.com', 'bofa.com', 'jpmorgan.com',
    'mashreqbank.com', 'emiratesnbd.com', 'onlinesbi.sbi', 'pnbindia.in',
    'bankofbaroda.in', 'canarabank.com', 'unionbankofindia.co.in',
    'lvbank.com', 'psbindia.com',
}

# Banking keywords that confirm a financial institution URL
BANK_KEYWORDS = {
    'bank', 'banking', 'finance', 'financial', 'credit', 'lending',
    'loan', 'nbfc', 'capital', 'invest', 'sbi', 'hdfc', 'icici',
    'kotak', 'axis', 'indusind', 'federal', 'karnataka', 'saraswat'
}

# Hard reject — wrong industry detected
BANK_REJECT_TERMS = {
    'shop', 'store', 'hotel', 'restaurant', 'hospital', 'school',
    'college', 'university', 'pharma', 'steel', 'textile', 'realty',
    'construction', 'architect', 'travel', 'tourism'
}


def validate_bank(name: str, url: str) -> tuple:
    """
    Validate a bank URL before extraction.
    Returns: (is_valid: bool, score: int, reason: str)

    Scoring:
      +60  Known bank domain (whitelist match)
      +30  Company initials found in domain
      +25  Significant name word found in domain
      +20  Banking keyword in URL
      +15  Banking keyword in company name
    Threshold: score >= 50 to pass
    """
    score   = 0
    reasons = []
    domain  = url.lower()
    name_lc = name.lower()

    # Hard reject — wrong industry
    if any(t in domain for t in BANK_REJECT_TERMS):
        return False, 0, "non-banking domain detected"

    # Check 1: Domain in known bank whitelist (+60)
    domain_root = (domain
                   .replace('https://', '').replace('http://', '')
                   .replace('www.', '').split('/')[0])
    if any(known in domain_root for known in KNOWN_BANK_DOMAINS):
        score += 60
        reasons.append("known bank domain")

    # Check 2: Company initials in domain (+30)
    initials = "".join(w[0] for w in name_lc.split() if w).lower()
    if len(initials) >= 2 and initials in domain:
        score += 30
        reasons.append("initials match domain")

    # Check 3: Significant word from name in domain (+25)
    significant = [w for w in name_lc.split()
                   if len(w) > 4 and w not in
                   {'bank', 'small', 'finance', 'india', 'limited', 'private'}]
    if significant and any(w in domain for w in significant):
        score += 25
        reasons.append("name word in domain")

    # Check 4: Banking keyword in URL (+20)
    if any(k in domain for k in BANK_KEYWORDS):
        score += 20
        reasons.append("banking keyword in URL")

    # Check 5: Banking keyword in company name (+15)
    if any(k in name_lc for k in BANK_KEYWORDS):
        score += 15
        reasons.append("banking keyword in name")

    is_valid = score >= 50
    reason   = " | ".join(reasons) if reasons else "no banking signals found"
    return is_valid, score, reason


# ── Data model ────────────────────────────────────────────────
@dataclass
class Lender:
    company_name:      str
    company_type:      str
    website:           str
    aum_crores:        float = None
    product_types:     str   = "[]"
    primary_product:   str   = ""
    hq_location:       str   = ""
    hq_state:          str   = ""
    operating_states:  str   = "[]"
    pan_india:         bool  = False
    established_year:  int   = None
    employee_count:    int   = None
    ticket_size_min:   float = None
    ticket_size_max:   float = None
    has_subsidiaries:  bool  = False
    phone:             str   = ""
    email:             str   = ""
    data_source:       str   = "gemini"
    extraction_status: str   = "success"
    error:             str   = ""


# ── Gemini extraction ─────────────────────────────────────────
PROMPT = """You are a financial data researcher. Extract accurate data about this Indian lending institution.

Company: {company_name}
Type: {company_type}
Website: {website}

Extract these fields. Use null if not found. Do NOT guess.

1. aum_crores       → Total AUM in Indian Crores (number, e.g. 250000)
2. product_types    → ALL loan products as JSON array.
                      Use only: "Home Loan", "Personal Loan", "Business Loan",
                      "MSME Loan", "Vehicle Loan", "Gold Loan", "Education Loan",
                      "Micro Loan", "Loan Against Property", "Working Capital",
                      "Agriculture Loan", "Credit Card"
3. primary_product  → Most important single product this company is known for
4. hq_city          → Headquarters city
5. hq_state         → Full state name (e.g. "Maharashtra" not "MH")
6. operating_states → JSON array of Indian states where they actively give loans.
                      If truly pan-India (all states), return ["PAN_INDIA"]
7. established_year → 4-digit founding year
8. employee_count   → Total number of employees
9. ticket_size_min  → Minimum loan amount in Lakhs (1 = Rs 1 Lakh)
10. ticket_size_max → Maximum loan amount in Lakhs
11. has_subsidiaries → true if company has subsidiaries, false otherwise
12. phone           → Primary contact phone number
13. email           → Primary contact email

Return ONLY valid JSON, no markdown, no explanation:
{{"aum_crores":null,"product_types":[],"primary_product":null,"hq_city":null,
"hq_state":null,"operating_states":[],"established_year":null,"employee_count":null,
"ticket_size_min":null,"ticket_size_max":null,"has_subsidiaries":false,
"phone":null,"email":null}}"""


def extract_with_gemini(name: str, website: str, ctype: str) -> Optional[Dict]:
    if not GEMINI_KEY:
        print("    ✗ GEMINI_API_KEY not set")
        return None
    try:
        payload = {
            "contents": [{"parts": [{"text": PROMPT.format(
                company_name=name, company_type=ctype, website=website
            )}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1500},
            "tools": [{"googleSearchRetrieval": {}}]
        }
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_KEY}",
            json=payload, timeout=45
        )
        if resp.status_code != 200:
            print(f"    ✗ HTTP {resp.status_code}: {resp.text[:100]}")
            return None

        text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        if '```' in text:
            text = '\n'.join(
                l for l in text.split('\n')
                if not l.strip().startswith('```')
            )
        return json.loads(text.strip())

    except json.JSONDecodeError as e:
        print(f"    ✗ JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"    ✗ Gemini error: {e}")
        return None


# ── Build Lender from Gemini output ──────────────────────────
def build_lender(name: str, ctype: str, website: str,
                 pan_india_flag: bool, data: Dict) -> Lender:

    hq_city  = data.get('hq_city') or ''
    hq_state = data.get('hq_state') or ''
    hq_loc   = f"{hq_city}, {hq_state}".strip(', ')

    raw_states = data.get('operating_states') or []
    if (pan_india_flag
            or raw_states == "PAN_INDIA"
            or raw_states == ["PAN_INDIA"]):
        op_states = ALL_INDIA_STATES
        is_pan    = True
    else:
        op_states = raw_states if isinstance(raw_states, list) else []
        is_pan    = False

    return Lender(
        company_name     = name,
        company_type     = ctype,
        website          = website,
        aum_crores       = data.get('aum_crores'),
        product_types    = json.dumps(data.get('product_types') or []),
        primary_product  = data.get('primary_product') or '',
        hq_location      = hq_loc,
        hq_state         = hq_state,
        operating_states = json.dumps(op_states),
        pan_india        = is_pan,
        established_year = data.get('established_year'),
        employee_count   = data.get('employee_count'),
        ticket_size_min  = data.get('ticket_size_min'),
        ticket_size_max  = data.get('ticket_size_max'),
        has_subsidiaries = bool(data.get('has_subsidiaries', False)),
        phone            = data.get('phone') or '',
        email            = data.get('email') or '',
        data_source      = 'gemini',
        extraction_status= 'success',
    )


def save(results: list, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys())
        w.writeheader()
        w.writerows(results)


# ─────────────────────────────────────────────────────────────
# MODE 1 — BANKS  (validation ON)
# ─────────────────────────────────────────────────────────────
def run_banks():
    from banks_list import TOP_50_PRIVATE_BANKS
    total   = len(TOP_50_PRIVATE_BANKS)
    results = []
    ok = fail = skipped = 0

    print(f"\n{'='*60}")
    print(f"BANKS EXTRACTION  ({total} institutions)")
    print(f"Validation: ON — verifying each bank URL before extraction")
    print(f"{'='*60}")

    for i, bank in enumerate(TOP_50_PRIVATE_BANKS, 1):
        name    = bank['company_name']
        website = bank['website']
        pan     = bank.get('pan_india', False)

        # Determine company type
        if 'Small Finance' in name:
            ctype = 'Small Finance Bank'
        elif any(k in name for k in ['HSBC', 'Citi', 'Standard Chartered',
                                      'DBS', 'Deutsche', 'Barclays',
                                      'Bank of America', 'JP Morgan',
                                      'Mashreq', 'Emirates']):
            ctype = 'Foreign Bank'
        elif any(k in name for k in ['State Bank', 'Punjab National',
                                      'Bank of Baroda', 'Canara', 'Union Bank']):
            ctype = 'PSU Bank'
        else:
            ctype = 'Private Bank'

        print(f"\n[{i}/{total}] {name}  ({ctype})")
        print(f"  URL: {website}")

        # ── VALIDATION ───────────────────────────────────────
        is_valid, score, reason = validate_bank(name, website)
        print(f"  Validation: {score}/100 — {reason}")

        if not is_valid:
            print(f"  ✗ REJECTED — not a legitimate bank URL")
            results.append(asdict(Lender(
                company_name=name, company_type=ctype, website=website,
                pan_india=pan, extraction_status='failed',
                error=f'Bank validation failed ({score}/100): {reason}'
            )))
            skipped += 1
            continue

        # ── EXTRACTION ───────────────────────────────────────
        print(f"  → Extracting with Gemini...")
        data = extract_with_gemini(name, website, ctype)

        if not data:
            results.append(asdict(Lender(
                company_name=name, company_type=ctype, website=website,
                pan_india=pan, extraction_status='failed',
                error='Gemini returned no data'
            )))
            fail += 1
        else:
            results.append(asdict(build_lender(name, ctype, website, pan, data)))
            ok += 1
            fields = sum(1 for v in data.values() if v is not None and v != [] and v != '')
            print(f"  ✓ {fields}/13 fields extracted")

        # Crash-safe save every 5 rows
        if i % 5 == 0 or i == total:
            save(results, BANKS_OUT)
            print(f"\n  💾 {len(results)} rows saved  ✓{ok} ✗{fail} ⊘{skipped}")

        time.sleep(2)   # 30 req/min rate limit

    print(f"\n{'='*60}")
    print(f"BANKS DONE  ✓{ok} extracted  ✗{fail} failed  ⊘{skipped} rejected")
    print(f"Output → {BANKS_OUT}")
    print(f"{'='*60}")


# ─────────────────────────────────────────────────────────────
# MODE 2 — NBFCs  (validation OFF — trust your verified list)
# ─────────────────────────────────────────────────────────────
def run_nbfcs():
    csv_files = sorted(glob.glob(str(INPUT_DIR / '*.csv')))

    if not csv_files:
        print(f"\n✗ No CSV files found in: {INPUT_DIR}")
        print("  Add your NBFC CSV with columns: company_name, website")
        return

    all_rows = []
    for f in csv_files:
        with open(f, encoding='utf-8') as fh:
            all_rows.extend(list(csv.DictReader(fh)))

    total   = len(all_rows)
    results = []
    ok = fail = skip = 0

    print(f"\n{'='*60}")
    print(f"NBFC EXTRACTION  ({total} from {len(csv_files)} file(s))")
    print(f"Validation: OFF — using your verified list directly")
    print(f"{'='*60}")

    for i, row in enumerate(all_rows, 1):
        name    = row.get('company_name', '').strip()
        website = (
            row.get('website') or
            row.get('validated_url') or
            row.get('raw_url', '')
        ).strip()

        print(f"\n[{i}/{total}] {name}")
        print(f"  URL: {website}")

        if not name:
            print("  ✗ No company name — skipped")
            skip += 1
            continue

        if not website:
            print("  ✗ No URL — skipped")
            skip += 1
            continue

        # No validation — you give us genuine NBFCs
        print(f"  → Extracting with Gemini...")
        data = extract_with_gemini(name, website, 'NBFC')

        if not data:
            results.append(asdict(Lender(
                company_name=name, company_type='NBFC', website=website,
                extraction_status='failed',
                error='Gemini returned no data'
            )))
            fail += 1
            continue

        results.append(asdict(build_lender(name, 'NBFC', website, False, data)))
        ok += 1
        fields = sum(1 for v in data.values() if v is not None and v != [] and v != '')
        print(f"  ✓ {fields}/13 fields extracted")

        # Crash-safe save every 10 rows
        if i % 10 == 0 or i == total:
            save(results, NBFCS_OUT)
            print(f"\n  💾 {len(results)} rows saved  ✓{ok} ✗{fail} ⊘{skip}")

        time.sleep(2)

    print(f"\n{'='*60}")
    print(f"NBFC DONE  ✓{ok} extracted  ✗{fail} failed  ⊘{skip} skipped")
    print(f"Output → {NBFCS_OUT}")
    print(f"{'='*60}")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if not GEMINI_KEY:
        print("\n✗ GEMINI_API_KEY not set. Run:")
        print("  Mac/Linux:  export GEMINI_API_KEY='your_key_here'")
        print("  Windows:    set GEMINI_API_KEY=your_key_here\n")
        sys.exit(1)

    mode = sys.argv[1] if len(sys.argv) > 1 else 'banks'

    if   mode == 'banks': run_banks()
    elif mode == 'nbfcs': run_nbfcs()
    elif mode == 'all':   run_banks(); run_nbfcs()
    else:
        print("\nUsage:")
        print("  python run_extraction.py banks   # Extract top 50 banks")
        print("  python run_extraction.py nbfcs   # Extract your NBFC CSV")
        print("  python run_extraction.py all     # Both")
        sys.exit(1)