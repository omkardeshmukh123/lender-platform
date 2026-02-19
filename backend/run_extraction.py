"""
Lender Discovery Platform - Extraction Pipeline v2
Supports: NBFC, PSU Bank, Private Bank, Cooperative Bank, Corporate Bank
"""

import os
import csv
import json
import time
import glob
import requests
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, asdict

# ============================================================
# CONFIG
# ============================================================

INPUT_DIR  = Path(__file__).parent.parent / 'data' / 'input'
OUTPUT_FILE = Path(__file__).parent.parent / 'data' / 'output' / 'extracted_lenders.csv'

GEMINI_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent'

# Valid company types (must match DB CHECK constraint)
VALID_COMPANY_TYPES = {
    'NBFC', 'PSU Bank', 'Private Bank', 'Cooperative Bank', 'Corporate Bank'
}

# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class Lender:
    row_number: int
    company_name: str
    company_type: str = "NBFC"
    website: str = ""
    aum_crores: float = None
    product_types: str = ""        # JSON array string → maps to DB product_types
    primary_product: str = ""      # FIX: was missing from v1
    hq_location: str = ""          # FIX: combined city+state for display
    hq_state: str = ""
    operating_states: str = ""     # FIX: was missing from v1 DB schema
    established_year: int = None
    employee_count: int = None
    ticket_size_min: float = None  # in Lakhs
    ticket_size_max: float = None  # in Lakhs
    has_subsidiaries: bool = False  # FIX: was missing from v1 extraction
    phone: str = ""
    email: str = ""
    data_source: str = "gemini"
    extraction_status: str = "success"
    error: str = ""

# ============================================================
# VALIDATION
# ============================================================

# Financial terms that indicate a valid lending institution
FINANCIAL_TERMS = {
    'nbfc', 'loan', 'finance', 'credit', 'lending', 'capital',
    'rbi', 'reserve bank', 'financial services', 'microfinance',
    'housing finance', 'vehicle finance', 'gold loan', 'personal loan',
    'bank', 'banking', 'cooperative', 'sahakari', 'gramin',
    'investment', 'asset', 'fund', 'leasing', 'insurance'
}

# Non-financial industries to reject
REJECT_TERMS = {
    'architecture', 'construction', 'real estate developer', 'steel',
    'manufacturing', 'oil', 'gas', 'retail store', 'university', 'college',
    'hospital', 'school', 'restaurant', 'hotel', 'pharma', 'textile'
}

def quick_validate(company_name: str, url: str, company_type: str = "NBFC") -> tuple:
    """
    Validate if URL belongs to a legitimate financial institution.
    Banks/PSUs are pre-validated — skip score check for them.
    Returns: (is_valid: bool, score: int, reason: str)
    """
    # PSU/Private/Cooperative Banks are from authoritative lists → auto-valid
    if company_type in ('PSU Bank', 'Private Bank', 'Cooperative Bank', 'Corporate Bank'):
        return (True, 100, f"Auto-validated: {company_type}")

    score = 0
    reasons = []

    # Check 1: Domain contains company name fragment
    name_clean = company_name.lower().replace(' ', '').replace('.', '')[:15]
    domain = url.lower()
    if name_clean in domain:
        score += 40
        reasons.append("Domain matches company name")

    # Check 2: Financial keywords in domain
    if any(term in domain for term in ['finance', 'capital', 'loan', 'nbfc', 'credit', 'bank']):
        score += 30
        reasons.append("Financial terms in URL")

    # Check 3: Financial keyword in company name itself
    name_lower = company_name.lower()
    if any(term in name_lower for term in ['finance', 'capital', 'loan', 'credit', 'leasing', 'bank']):
        score += 30
        reasons.append("Financial terms in company name")

    is_valid = score >= 50
    reason = " | ".join(reasons) if reasons else "Insufficient evidence"
    return (is_valid, score, reason)

# ============================================================
# GEMINI EXTRACTION PROMPT
# ============================================================

EXTRACTION_PROMPT = """You are a financial data extraction expert. Extract factual information about this Indian lending institution.

Company: {company_name}
Type: {company_type}
Website: {website}

Extract these fields (use null if not found, do NOT guess):

1. aum_crores: Total Assets Under Management in Indian Crores (number only, e.g. 5000)
2. product_types: All loan/financial products as JSON array (e.g. ["Home Loan", "MSME Loan", "Gold Loan"])
3. primary_product: Single most important/dominant product this company is known for
4. hq_city: Headquarters city name only
5. hq_state: Headquarters state - full name (e.g. "Maharashtra" not "MH")
6. operating_states: All Indian states where they actively lend - JSON array of full names
7. established_year: Year the company was founded (4-digit number)
8. employee_count: Total number of employees (number only)
9. ticket_size_min: Minimum loan amount in Lakhs (e.g. 5 means ₹5 Lakh)
10. ticket_size_max: Maximum loan amount in Lakhs (e.g. 500 means ₹500 Lakh = ₹5 Crore)
11. has_subsidiaries: true if company has subsidiaries/group companies, false otherwise
12. phone: Primary contact phone number
13. email: Primary contact email address

IMPORTANT RULES:
- Use null for any field you cannot find with certainty
- product_types and operating_states MUST be JSON arrays
- has_subsidiaries is boolean (true/false)
- All amounts in Indian system (Crores for AUM, Lakhs for ticket size)

Return ONLY valid JSON (no markdown, no explanation):
{{
  "aum_crores": null,
  "product_types": [],
  "primary_product": null,
  "hq_city": null,
  "hq_state": null,
  "operating_states": [],
  "established_year": null,
  "employee_count": null,
  "ticket_size_min": null,
  "ticket_size_max": null,
  "has_subsidiaries": false,
  "phone": null,
  "email": null
}}
"""

def extract_with_gemini(company_name: str, website: str, company_type: str) -> Optional[Dict]:
    """Extract all fields using Gemini Flash with Google Search grounding"""
    if not GEMINI_KEY:
        print("    ⚠ No GEMINI_API_KEY set")
        return None

    try:
        prompt = EXTRACTION_PROMPT.format(
            company_name=company_name,
            website=website,
            company_type=company_type
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1500,
            },
            "tools": [{"googleSearchRetrieval": {}}]  # Real-time web search
        }

        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_KEY}",
            json=payload,
            timeout=45
        )

        if resp.status_code != 200:
            print(f"    ✗ Gemini HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        data = resp.json()
        text = data['candidates'][0]['content']['parts'][0]['text'].strip()

        # Strip markdown code fences if present
        if text.startswith('```'):
            text = '\n'.join(text.split('\n')[1:])
        if text.endswith('```'):
            text = '\n'.join(text.split('\n')[:-1])
        text = text.strip()

        return json.loads(text)

    except json.JSONDecodeError as e:
        print(f"    ✗ JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"    ✗ Gemini error: {e}")
        return None

# ============================================================
# ROW PROCESSOR
# ============================================================

def process_row(row: Dict) -> Lender:
    """Process a single institution: validate → extract → return Lender"""

    row_num      = int(row.get('row_number', 0))
    company_name = row.get('company_name', '').strip()
    url          = row.get('validated_url') or row.get('website') or row.get('raw_url', '')
    company_type = row.get('company_type', 'NBFC').strip()
    outcome      = row.get('outcome', '')

    # Normalise company_type
    if company_type not in VALID_COMPANY_TYPES:
        company_type = 'NBFC'

    print(f"\n[{row_num}] {company_name} ({company_type})")
    print(f"  URL: {url}")

    if not url:
        return Lender(
            row_number=row_num,
            company_name=company_name,
            company_type=company_type,
            extraction_status="failed",
            error="No URL provided"
        )

    # Validate
    is_valid, score, reason = quick_validate(company_name, url, company_type)
    print(f"  Validation: {score}/100 - {reason}")

    if not is_valid and outcome != 'OFFICIAL_WEBSITE_FOUND':
        return Lender(
            row_number=row_num,
            company_name=company_name,
            company_type=company_type,
            website=url,
            extraction_status="failed",
            error=f"Validation failed: {reason}"
        )

    # Extract with Gemini
    print(f"  → Extracting with Gemini...")
    extracted = extract_with_gemini(company_name, url, company_type)

    if not extracted:
        return Lender(
            row_number=row_num,
            company_name=company_name,
            company_type=company_type,
            website=url,
            extraction_status="failed",
            error="Gemini extraction returned no data"
        )

    print(f"  ✓ Extracted {sum(1 for v in extracted.values() if v is not None)} fields")

    # Build hq_location from city + state
    hq_city  = extracted.get('hq_city') or ''
    hq_state = extracted.get('hq_state') or ''
    hq_location = f"{hq_city}, {hq_state}".strip(', ') if hq_city or hq_state else ''

    # Serialise JSON arrays
    product_types    = extracted.get('product_types') or []
    operating_states = extracted.get('operating_states') or []

    return Lender(
        row_number       = row_num,
        company_name     = company_name,
        company_type     = company_type,
        website          = url,
        aum_crores       = extracted.get('aum_crores'),
        product_types    = json.dumps(product_types) if isinstance(product_types, list) else "[]",
        primary_product  = extracted.get('primary_product') or '',
        hq_location      = hq_location,
        hq_state         = hq_state,
        operating_states = json.dumps(operating_states) if isinstance(operating_states, list) else "[]",
        established_year = extracted.get('established_year'),
        employee_count   = extracted.get('employee_count'),
        ticket_size_min  = extracted.get('ticket_size_min'),
        ticket_size_max  = extracted.get('ticket_size_max'),
        has_subsidiaries = bool(extracted.get('has_subsidiaries', False)),
        phone            = extracted.get('phone') or '',
        email            = extracted.get('email') or '',
        data_source      = 'gemini',
        extraction_status = 'success',
    )

# ============================================================
# MAIN PIPELINE
# ============================================================

def main():
    print("=" * 70)
    print("LENDER EXTRACTION PIPELINE v2")
    print("Supports: NBFC, PSU Bank, Private Bank, Cooperative Bank")
    print("=" * 70)

    # Load all batch CSVs from input dir
    batch_files = sorted(glob.glob(str(INPUT_DIR / '*.csv')))
    print(f"\nFound {len(batch_files)} batch files in {INPUT_DIR}")

    all_rows = []
    for batch_file in batch_files:
        with open(batch_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                outcome = row.get('outcome', '')
                # Accept rows with valid outcomes OR no outcome field (manual lists)
                if not outcome or outcome in ('OFFICIAL_WEBSITE_FOUND', 'REVIEW_REQUIRED'):
                    all_rows.append(row)

    total = len(all_rows)
    print(f"Total to process: {total} institutions")
    print(f"Output: {OUTPUT_FILE}\n")
    print("=" * 70)

    results   = []
    success   = 0
    failed    = 0

    for idx, row in enumerate(all_rows, 1):
        print(f"\nProgress: {idx}/{total}")

        try:
            lender = process_row(row)
            results.append(asdict(lender))

            if lender.extraction_status == "success":
                success += 1
            else:
                failed += 1

            # Save incrementally every 10 rows
            if idx % 10 == 0 or idx == total:
                OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as out:
                    if results:
                        writer = csv.DictWriter(out, fieldnames=results[0].keys())
                        writer.writeheader()
                        writer.writerows(results)
                print(f"\n  💾 Saved {len(results)} rows (✓{success} ✗{failed})")

            # Rate limit: ~30 req/min for Gemini Flash
            time.sleep(2)

        except KeyboardInterrupt:
            print("\n\n⚠ Interrupted by user — partial results saved")
            break
        except Exception as e:
            print(f"  ✗ Unexpected error: {e}")
            failed += 1
            continue

    print("\n" + "=" * 70)
    print("EXTRACTION COMPLETE")
    print("=" * 70)
    print(f"Total processed : {len(results)}")
    print(f"✓ Success       : {success}")
    print(f"✗ Failed        : {failed}")
    print(f"📄 Output        : {OUTPUT_FILE}")
    print("=" * 70)


if __name__ == '__main__':
    main()