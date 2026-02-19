"""
Lender Discovery Platform - Extraction Pipeline
Process 3,181 NBFCs from 19 batch files with validation + Gemini extraction
"""

import os
import csv
import json
import time
import glob
import requests
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

INPUT_DIR = Path(__file__).parent.parent / 'data' / 'input'
OUTPUT_FILE = Path(__file__).parent.parent / 'data' / 'output' / 'extracted_lenders.csv'

GEMINI_KEY = os.getenv('GEMINI_API_KEY', '')
SERP_KEY = os.getenv('SERPAPI_KEY', '')

GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent'

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
    products: str = ""  # JSON array as string
    hq_city: str = ""
    hq_state: str = ""
    established_year: int = None
    employee_count: int = None
    ticket_size_min_lakhs: float = None
    ticket_size_max_lakhs: float = None
    operating_states: str = ""  # JSON array as string
    phone: str = ""
    email: str = ""
    status: str = ""  # SUCCESS / VALIDATION_FAILED / EXTRACTION_FAILED
    validation_score: int = 0
    error: str = ""

# ============================================================
# VALIDATION (Lightweight - reuse key checks)
# ============================================================

FINANCIAL_TERMS = {
    'nbfc', 'loan', 'finance', 'credit', 'lending', 'capital',
    'rbi', 'reserve bank', 'financial services', 'microfinance',
    'housing finance', 'vehicle finance', 'gold loan', 'personal loan'
}

REJECT_TERMS = {
    'architecture', 'construction', 'real estate developer', 'steel',
    'manufacturing', 'oil', 'gas', 'retail', 'university', 'college',
    'hospital', 'school', 'restaurant', 'hotel'
}

def quick_validate(company_name: str, url: str, content_snippet: str = "") -> tuple:
    """
    Quick validation - check if URL likely belongs to an NBFC.
    Returns: (is_valid: bool, score: int, reason: str)
    """
    score = 0
    reasons = []
    
    # Check 1: Domain match
    name_clean = company_name.lower().replace(' ', '').replace('.', '')[:15]
    domain = url.lower()
    if name_clean in domain:
        score += 40
        reasons.append("Domain matches company name")
    
    # Check 2: Financial keywords in URL
    if any(term in domain for term in ['finance', 'capital', 'loan', 'nbfc']):
        score += 30
        reasons.append("Financial terms in URL")
    
    # Check 3: Content check (if provided)
    content_lower = content_snippet.lower()
    if content_lower:
        financial_found = sum(1 for term in FINANCIAL_TERMS if term in content_lower)
        if financial_found >= 2:
            score += 30
            reasons.append(f"Financial terms in content ({financial_found} found)")
        
        # Reject if wrong industry
        if any(term in content_lower for term in REJECT_TERMS):
            return (False, 0, "Wrong industry detected")
    
    is_valid = score >= 60
    reason = " | ".join(reasons) if reasons else "Insufficient evidence"
    
    return (is_valid, score, reason)

# ============================================================
# GEMINI EXTRACTION
# ============================================================

EXTRACTION_PROMPT = """Extract factual information about this NBFC from web search.

Company: {company_name}
Website: {website}

Extract these fields (use null if not found):

1. aum_crores: Assets Under Management in crores (number only)
2. products: Loan products as array (e.g. ["MSME Loan", "Working Capital", "Invoice Discounting"])
3. hq_city: Headquarters city
4. hq_state: Headquarters state (use full name: Maharashtra not MH)
5. established_year: Year founded
6. employee_count: Number of employees
7. ticket_size_min_lakhs: Minimum loan amount in lakhs
8. ticket_size_max_lakhs: Maximum loan amount in lakhs  
9. operating_states: States where they lend (array of full names)
10. phone: Contact phone
11. email: Contact email

Return ONLY JSON (no markdown, no extra text):
{{
  "aum_crores": 5000,
  "products": ["MSME Loan", "Working Capital"],
  "hq_city": "Mumbai",
  "hq_state": "Maharashtra",
  "established_year": 2015,
  "employee_count": 500,
  "ticket_size_min_lakhs": 5,
  "ticket_size_max_lakhs": 200,
  "operating_states": ["Maharashtra", "Gujarat"],
  "phone": "+91-22-12345678",
  "email": "contact@company.com"
}}
"""

def extract_with_gemini(company_name: str, website: str) -> Optional[Dict]:
    """Extract 11 fields using Gemini Flash with Google Search grounding"""
    if not GEMINI_KEY:
        print("    ⚠ No GEMINI_API_KEY set")
        return None
    
    try:
        prompt = EXTRACTION_PROMPT.format(
            company_name=company_name,
            website=website
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1500,
            },
            "tools": [{"googleSearchRetrieval": {}}]  # Real-time search
        }
        
        url = f"{GEMINI_URL}?key={GEMINI_KEY}"
        resp = requests.post(url, json=payload, timeout=45)
        
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        text = data['candidates'][0]['content']['parts'][0]['text'].strip()
        
        # Clean markdown
        if text.startswith('```'):
            text = text.split('\n', 1)[1]
        if text.endswith('```'):
            text = text.rsplit('\n', 1)[0]
        text = text.strip()
        
        return json.loads(text)
        
    except Exception as e:
        print(f"    ✗ Gemini error: {e}")
        return None

# ============================================================
# MAIN PIPELINE
# ============================================================

def process_row(row: Dict) -> Lender:
    """Process single NBFC: validate → extract → return Lender"""
    
    row_num = int(row['row_number'])
    company_name = row['company_name']
    url = row.get('validated_url') or row.get('raw_url', '')
    outcome = row.get('outcome', '')
    
    print(f"\n[{row_num}] {company_name}")
    print(f"  URL: {url}")
    print(f"  Outcome: {outcome}")
    
    if not url:
        return Lender(
            row_number=row_num,
            company_name=company_name,
            status="VALIDATION_FAILED",
            error="No URL provided"
        )
    
    # Quick validation
    is_valid, score, reason = quick_validate(company_name, url)
    print(f"  Validation: {score}/100 - {reason}")
    
    if not is_valid and outcome != 'OFFICIAL_WEBSITE_FOUND':
        return Lender(
            row_number=row_num,
            company_name=company_name,
            website=url,
            status="VALIDATION_FAILED",
            validation_score=score,
            error=reason
        )
    
    # Extract with Gemini
    print(f"  → Extracting with Gemini...")
    extracted = extract_with_gemini(company_name, url)
    
    if not extracted:
        return Lender(
            row_number=row_num,
            company_name=company_name,
            website=url,
            status="EXTRACTION_FAILED",
            validation_score=score,
            error="Gemini extraction failed"
        )
    
    print(f"  ✓ Extracted {len(extracted)} fields")
    
    # Build result
    products = extracted.get('products', [])
    operating_states = extracted.get('operating_states', [])
    
    return Lender(
        row_number=row_num,
        company_name=company_name,
        website=url,
        aum_crores=extracted.get('aum_crores'),
        products=json.dumps(products) if isinstance(products, list) else "",
        hq_city=extracted.get('hq_city', ''),
        hq_state=extracted.get('hq_state', ''),
        established_year=extracted.get('established_year'),
        employee_count=extracted.get('employee_count'),
        ticket_size_min_lakhs=extracted.get('ticket_size_min_lakhs'),
        ticket_size_max_lakhs=extracted.get('ticket_size_max_lakhs'),
        operating_states=json.dumps(operating_states) if isinstance(operating_states, list) else "",
        phone=extracted.get('phone', ''),
        email=extracted.get('email', ''),
        status="SUCCESS",
        validation_score=score,
    )

def main():
    """Process all batches and save to output CSV"""
    
    print("="*70)
    print("LENDER EXTRACTION PIPELINE")
    print("="*70)
    
    # Read all batch CSVs
    batch_files = sorted(glob.glob(str(INPUT_DIR / '*.csv')))
    print(f"\nFound {len(batch_files)} batch files")
    
    all_rows = []
    for batch_file in batch_files:
        with open(batch_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                outcome = row.get('outcome', '')
                if outcome in ('OFFICIAL_WEBSITE_FOUND', 'REVIEW_REQUIRED'):
                    all_rows.append(row)
    
    total = len(all_rows)
    print(f"Total to process: {total} NBFCs")
    print(f"Output: {OUTPUT_FILE}\n")
    print("="*70)
    
    results = []
    success = 0
    failed = 0
    
    for idx, row in enumerate(all_rows, 1):
        print(f"\nProgress: {idx}/{total}")
        
        try:
            lender = process_row(row)
            results.append(asdict(lender))
            
            if lender.status == "SUCCESS":
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
            
            # Rate limit: 30/min for Gemini
            time.sleep(2)
            
        except KeyboardInterrupt:
            print("\n\n⚠ Interrupted by user")
            break
        except Exception as e:
            print(f"  ✗ Error: {e}")
            failed += 1
            continue
    
    print("\n" + "="*70)
    print("EXTRACTION COMPLETE")
    print("="*70)
    print(f"Total processed: {len(results)}")
    print(f"✓ Success:       {success}")
    print(f"✗ Failed:        {failed}")
    print(f"📄 Output:        {OUTPUT_FILE}")
    print("="*70)

if __name__ == '__main__':
    main()
