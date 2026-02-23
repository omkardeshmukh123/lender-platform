"""
debug_raw.py v2 — tests with thinkingBudget=0 to fix truncation
Run: python backend/debug_raw.py
"""
import os, json, requests

KEY = os.getenv('GEMINI_API_KEY', '')
url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={KEY}"

PROMPT = (
    "You are a financial data researcher for Indian banks.\n"
    "Extract accurate information about UCO Bank (PSU Bank).\n\n"
    "Return a JSON object with EXACTLY these 14 fields.\n"
    "Use null for unknown. product_types MUST only use: "
    '"Home Loan","Personal Loan","Business Loan","MSME Loan","Vehicle Loan",'
    '"Gold Loan","Education Loan","Micro Loan","Loan Against Property",'
    '"Working Capital","Agriculture Loan","Credit Card"\n\n'
    '{"website": null, "aum_crores": null, "product_types": [], "primary_product": null,\n'
    ' "hq_city": null, "hq_state": null, "operating_states": [], "established_year": null,\n'
    ' "employee_count": null, "ticket_size_min": null, "ticket_size_max": null,\n'
    ' "has_subsidiaries": false, "phone": null, "email": null}\n\n'
    "Output ONLY the JSON object. No markdown. No explanation."
)

print("=" * 50)
print("TEST 1: Without thinkingBudget (current behavior)")
print("=" * 50)
payload1 = {
    "contents": [{"parts": [{"text": PROMPT}]}],
    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1500}
}
r1 = requests.post(url, json=payload1, timeout=45)
text1 = r1.json()['candidates'][0]['content']['parts'][0]['text']
print(f"Response length: {len(text1)} chars")
print(f"Last 80 chars: {repr(text1[-80:])}")
try:
    json.loads(text1)
    print("Parse: OK")
except Exception as e:
    print(f"Parse: FAILED - {e}")

print()
print("=" * 50)
print("TEST 2: With thinkingBudget=0 (fix)")
print("=" * 50)
payload2 = {
    "contents": [{"parts": [{"text": PROMPT}]}],
    "generationConfig": {
        "temperature": 0.1,
        "maxOutputTokens": 4096,
        "thinkingConfig": {"thinkingBudget": 0}
    }
}
r2 = requests.post(url, json=payload2, timeout=45)
text2 = r2.json()['candidates'][0]['content']['parts'][0]['text']
print(f"Response length: {len(text2)} chars")
print(f"Full response:\n{text2}")
print()
try:
    result = json.loads(text2.strip())
    print(f"Parse: OK")
    print(f"website: {result.get('website')}")
    print(f"product_types: {result.get('product_types')}")
except Exception as e:
    print(f"Parse: FAILED - {e}")
    # Try brace stack
    depth = 0; start = -1
    for i, ch in enumerate(text2):
        if ch == '{':
            if depth == 0: start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    r = json.loads(text2[start:i+1])
                    print(f"Brace-stack parse: OK - website={r.get('website')}")
                except Exception as e2:
                    print(f"Brace-stack also failed: {e2}")
                break