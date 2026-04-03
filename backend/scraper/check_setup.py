"""
check_setup.py
==============
Run this FIRST before pipeline.py to verify everything is configured.
Shows exactly why Gemini / Supabase might not be working.

Usage:
    python check_setup.py
"""

import os
import sys
import json
import requests
from pathlib import Path

# Load .env from project root (3 levels up from backend/scraper/)
ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = ROOT / '.env'

print('\n' + '=' * 60)
print('  SETUP DIAGNOSTIC')
print('=' * 60 + '\n')

# ── Step 1: Find and load .env ─────────────────────────────
print('1. Checking .env file...')
if ENV_FILE.exists():
    print(f'   ✅ Found: {ENV_FILE}')
    # Manually parse .env (in case python-dotenv not installed)
    with open(ENV_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and val and key not in os.environ:
                    os.environ[key] = val
    print(f'   ✅ Loaded environment variables from .env')
else:
    print(f'   ❌ NOT FOUND: {ENV_FILE}')
    print(f'   Create a file called .env in: {ROOT}')
    print(f'   With these contents:')
    print(f'     GEMINI_API_KEY=your_key_here')
    print(f'     NEXT_PUBLIC_SUPABASE_URL=your_url_here')
    print(f'     SUPABASE_SERVICE_ROLE_KEY=your_key_here')

# Also try python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
    print(f'   ✅ python-dotenv also loaded')
except ImportError:
    print(f'   ⚠️  python-dotenv not installed (not required, but recommended)')
    print(f'      Install: pip install python-dotenv')

print()

# ── Step 2: Check Gemini API key ───────────────────────────
print('2. Checking GEMINI_API_KEY...')
gemini_key = os.getenv('GEMINI_API_KEY', '').strip()
if not gemini_key:
    print('   ❌ GEMINI_API_KEY is empty or not set')
    print('   Fix: Add to your .env file:')
    print('     GEMINI_API_KEY=your_key_here')
    print('   Get key from: https://aistudio.google.com/apikeys')
else:
    masked = gemini_key[:6] + '...' + gemini_key[-4:]
    print(f'   ✅ Found: {masked}')

    # Test Gemini API call
    print('   Testing Gemini API call...')
    try:
        url = f'https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={gemini_key}'
        payload = {
            'contents': [{'parts': [{'text': 'Reply with only the word: working'}]}],
            'generationConfig': {'temperature': 0, 'maxOutputTokens': 10},
        }
        resp = requests.post(url, json=payload, timeout=20)

        if resp.status_code == 200:
            body = resp.json()
            text = (body.get('candidates', [{}])[0]
                       .get('content', {})
                       .get('parts', [{}])[0]
                       .get('text', ''))
            print(f'   ✅ Gemini API works! Response: "{text.strip()}"')
        elif resp.status_code == 400:
            print(f'   ❌ Bad request (400) — API key format may be wrong')
            print(f'   Response: {resp.text[:200]}')
        elif resp.status_code == 403:
            print(f'   ❌ Forbidden (403) — API key invalid or quota exceeded')
            print(f'   Get a new key from: https://aistudio.google.com/apikeys')
        elif resp.status_code == 429:
            print(f'   ⚠️  Rate limited (429) — key works but quota exceeded')
            print(f'   Wait a minute and try again')
        else:
            print(f'   ❌ HTTP {resp.status_code}: {resp.text[:200]}')
    except requests.exceptions.ConnectionError:
        print('   ❌ No internet connection or Gemini blocked')
    except Exception as e:
        print(f'   ❌ Error: {e}')

print()

# ── Step 3: Check Supabase ─────────────────────────────────
print('3. Checking Supabase...')
supa_url = os.getenv('NEXT_PUBLIC_SUPABASE_URL', '').strip()
supa_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()

if not supa_url:
    print('   ❌ NEXT_PUBLIC_SUPABASE_URL is not set')
else:
    print(f'   ✅ URL found: {supa_url[:40]}...')

if not supa_key:
    print('   ❌ SUPABASE_SERVICE_ROLE_KEY is not set')
    print('   Get it from: Supabase Dashboard → Project Settings → API → service_role')
else:
    masked = supa_key[:8] + '...' + supa_key[-4:]
    print(f'   ✅ Service role key found: {masked}')

    if supa_url:
        print('   Testing Supabase connection...')
        try:
            resp = requests.get(
                f'{supa_url}/rest/v1/lenders?select=id&limit=1',
                headers={
                    'apikey': supa_key,
                    'Authorization': f'Bearer {supa_key}',
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f'   ✅ Supabase connected! Table has data: {len(data)} row(s) returned')
            elif resp.status_code == 401:
                print(f'   ❌ Unauthorized — service role key is wrong')
            elif resp.status_code == 404:
                print(f'   ❌ Table "lenders" not found — check table name in Supabase')
            else:
                print(f'   ❌ HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            print(f'   ❌ Connection error: {e}')

print()

# ── Step 4: Check input CSV ────────────────────────────────
print('4. Checking input CSV...')
input_csv = ROOT / 'data' / 'input' / 'nbfc_names.csv'
if input_csv.exists():
    import csv
    with open(input_csv, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    print(f'   ✅ Found: {input_csv.name} ({len(rows)} lenders)')
    if rows:
        first = rows[0]
        print(f'   First row columns: {list(first.keys())}')
        print(f'   First lender: {first.get("company_name", "?")}')
        website_col = first.get('website') or first.get('original_website') or ''
        print(f'   Website column: {website_col or "EMPTY — check column name"}')
else:
    print(f'   ❌ NOT FOUND: {input_csv}')
    print(f'   Expected location: {input_csv}')

print()

# ── Step 5: Check Python packages ─────────────────────────
print('5. Checking Python packages...')
packages = {
    'scrapling': 'pip install scrapling[fetchers] && scrapling install',
    'requests':  'pip install requests',
    'dotenv':    'pip install python-dotenv',
}
for pkg, install_cmd in packages.items():
    try:
        __import__(pkg.replace('-', '_'))
        print(f'   ✅ {pkg}')
    except ImportError:
        print(f'   ❌ {pkg} not installed — run: {install_cmd}')

print()

# ── Summary ────────────────────────────────────────────────
print('=' * 60)
print('  SUMMARY')
print('=' * 60)

issues = []
if not gemini_key:
    issues.append('GEMINI_API_KEY missing — enrichment will not run')
if not supa_url:
    issues.append('NEXT_PUBLIC_SUPABASE_URL missing — upload will not work')
if not supa_key:
    issues.append('SUPABASE_SERVICE_ROLE_KEY missing — upload will not work')
if not input_csv.exists():
    issues.append('Input CSV not found')

if issues:
    print('\n  Issues to fix:')
    for issue in issues:
        print(f'    ❌ {issue}')
    print('\n  Fix these first, then run: python pipeline.py\n')
else:
    print('\n  ✅ Everything looks good!')
    print('  Run: python pipeline.py\n')