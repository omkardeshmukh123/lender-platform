"""
Run this on YOUR machine to find which Gemini models work with your key.
Copy-paste this file and run: python find_model.py
"""
import requests

KEY = "AIzaSyDFLxg54NpQt6b4pEWHnRb1F8bTQHHDVzc"

# Step 1: List all available models
print("Fetching available models for your API key...\n")
resp = requests.get(
    f"https://generativelanguage.googleapis.com/v1beta/models?key={KEY}",
    timeout=15
)
print(f"HTTP {resp.status_code}")
if resp.status_code == 200:
    models = resp.json().get('models', [])
    print(f"Found {len(models)} models:\n")
    for m in models:
        name         = m.get('name', '')
        display_name = m.get('displayName', '')
        methods      = m.get('supportedGenerationMethods', [])
        if 'generateContent' in methods:
            print(f"  ✓ {name}  ({display_name})")
        else:
            print(f"  - {name}  (no generateContent)")
else:
    print(resp.text[:300])

# Step 2: Quick test on most likely working models
print("\n\nTesting models with a simple prompt...\n")
candidates = [
    "models/gemini-2.0-flash",
    "models/gemini-2.0-flash-001",
    "models/gemini-1.5-flash",
    "models/gemini-1.5-flash-001",
    "models/gemini-1.5-flash-latest",
    "models/gemini-1.5-pro",
    "models/gemini-1.5-pro-001",
    "models/gemini-pro",
]
for model in candidates:
    url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={KEY}"
    r   = requests.post(url, json={
        "contents": [{"parts": [{"text": "say the word OK"}]}],
        "generationConfig": {"maxOutputTokens": 5}
    }, timeout=15)
    if r.status_code == 200:
        print(f"  ✓ WORKS: {model}")
    else:
        print(f"  ✗ {r.status_code}: {model}")