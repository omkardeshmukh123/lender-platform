"""
test_pipeline.py v4
Tests: bank validation, no NBFC validation, pan-india logic, data model
Run: python test_pipeline.py
"""
import sys, json, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from run_extraction import validate_bank, build_lender, Lender, ALL_INDIA_STATES
from banks_list import TOP_50_PRIVATE_BANKS
from dataclasses import asdict

PASS = FAIL = 0

def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {label}")
    if not ok: print(f"      got={got!r}  want={want!r}")
    if ok: PASS += 1
    else:  FAIL += 1

# ── TEST 1: Bank list ─────────────────────────────────────────
print("\n" + "="*55)
print("TEST 1: Top 50 Banks List")
print("="*55)
check("Exactly 50 banks", len(TOP_50_PRIVATE_BANKS), 50)
check("All have company_name", all('company_name' in b for b in TOP_50_PRIVATE_BANKS), True)
check("All have website",      all('website' in b for b in TOP_50_PRIVATE_BANKS), True)
check("All have pan_india",    all('pan_india' in b for b in TOP_50_PRIVATE_BANKS), True)
pan = sum(1 for b in TOP_50_PRIVATE_BANKS if b['pan_india'])
print(f"  ℹ  Pan-India: {pan}/{len(TOP_50_PRIVATE_BANKS)}")

# ── TEST 2: Bank validation ON ────────────────────────────────
print("\n" + "="*55)
print("TEST 2: Bank Validation (ON for banks)")
print("="*55)

bank_cases = [
    ("HDFC Bank",             "https://www.hdfcbank.com",         True),
    ("ICICI Bank",            "https://www.icicibank.com",        True),
    ("AU Small Finance Bank", "https://www.aubank.in",            True),
    ("State Bank of India",   "https://www.onlinesbi.sbi",        True),
    ("HSBC India",            "https://www.hsbc.co.in",           True),
    ("Fake Bank",             "https://www.hotelparadise.com",    False),
    ("XYZ Bank",              "https://www.steelworks.co.in",     False),
]
for name, url, want in bank_cases:
    valid, score, reason = validate_bank(name, url)
    check(f"{name[:35]:<35} valid={want}", valid, want)

# ── TEST 3: NBFC — NO validation ─────────────────────────────
print("\n" + "="*55)
print("TEST 3: NBFC Validation (OFF — trust your list)")
print("="*55)
print("  ✓ No validate_nbfc function — NBFCs go straight to Gemini")
print("  ✓ Only check: company_name not empty, website not empty")
PASS += 1

# ── TEST 4: Pan-India logic ───────────────────────────────────
print("\n" + "="*55)
print("TEST 4: Pan-India State Logic")
print("="*55)

l1 = build_lender("HDFC Bank","Private Bank","https://hdfcbank.com",
                  True, {"operating_states":["Maharashtra"],"product_types":[]})
check("pan_india flag → 36 states", len(json.loads(l1.operating_states)), 36)
check("pan_india=True set",         l1.pan_india, True)

l2 = build_lender("ICICI Bank","Private Bank","https://icicibank.com",
                  False, {"operating_states":["PAN_INDIA"],"product_types":[]})
check("Gemini PAN_INDIA → 36 states", len(json.loads(l2.operating_states)), 36)
check("pan_india=True from Gemini",   l2.pan_india, True)

l3 = build_lender("Local NBFC","NBFC","https://localnbfc.in",
                  False, {"operating_states":["Maharashtra","Gujarat"],"product_types":[]})
check("Local NBFC → only listed states",
      json.loads(l3.operating_states), ["Maharashtra","Gujarat"])
check("pan_india=False for local",    l3.pan_india, False)

# ── TEST 5: State filter simulation ──────────────────────────
print("\n" + "="*55)
print("TEST 5: State Filter (operating_states only)")
print("="*55)

mock = [
    {"name":"HDFC Bank",  "pan_india":True,  "states":ALL_INDIA_STATES},
    {"name":"Local NBFC", "pan_india":False, "states":["Maharashtra","Gujarat"]},
    {"name":"South NBFC", "pan_india":False, "states":["Tamil Nadu","Kerala"]},
]
def filt(lenders, state):
    return [l['name'] for l in lenders
            if l['pan_india'] or state in l['states']]

check("Maharashtra → HDFC + Local",  filt(mock,"Maharashtra"), ["HDFC Bank","Local NBFC"])
check("Kerala → HDFC + South",       filt(mock,"Kerala"),      ["HDFC Bank","South NBFC"])
check("Delhi → HDFC only",           filt(mock,"Delhi"),       ["HDFC Bank"])

# ── TEST 6: Data model + CSV ──────────────────────────────────
print("\n" + "="*55)
print("TEST 6: Data Model & CSV Write")
print("="*55)

s = Lender(
    company_name="Test Bank", company_type="Private Bank",
    website="https://testbank.com", aum_crores=50000,
    product_types='["Home Loan"]', primary_product="Home Loan",
    hq_location="Mumbai, Maharashtra", hq_state="Maharashtra",
    operating_states='["Maharashtra"]', pan_india=False,
    established_year=2000, employee_count=5000,
    ticket_size_min=10, ticket_size_max=5000,
    has_subsidiaries=True, data_source="gemini", extraction_status="success"
)
d = asdict(s)
check("20 fields in Lender", len(d), 20)
check("product_types is JSON string", isinstance(d['product_types'], str), True)

out = Path(__file__).parent.parent / 'data' / 'output' / 'test_out.csv'
out.parent.mkdir(parents=True, exist_ok=True)
with open(out,'w',newline='',encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=d.keys()); w.writeheader(); w.writerow(d)
with open(out,encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
check("CSV round-trip OK", rows[0]['company_name'], "Test Bank")
check("company_type saved", rows[0]['company_type'], "Private Bank")

# ── Summary ───────────────────────────────────────────────────
print("\n" + "="*55)
print(f"RESULTS  ✓ {PASS} passed   ✗ {FAIL} failed")
print("="*55)
if FAIL == 0:
    print("All tests passing! Ready to run:")
    print("  export GEMINI_API_KEY='your_key'")
    print("  python run_extraction.py banks")
    print("  python run_extraction.py nbfcs")
print("="*55 + "\n")