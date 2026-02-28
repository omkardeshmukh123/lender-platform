#!/usr/bin/env python3
"""
PATH A: Extract Clean NBFCs for Quick Launch
=============================================
Filters out 175 NBFCs with P0 issues (missing critical data)
Keeps 759 clean NBFCs ready to import

Strategy:
- Remove: NBFCs with missing operating_states (119)
- Remove: NBFCs with missing HQ state (56)
- Keep: 759 clean NBFCs (100% ready to go)

Result: 114 banks + 759 NBFCs = 873 total lenders
"""

import csv
import json
from datetime import datetime

# Configuration
INPUT_CSV = 'data/output/nbfc_final_224.csv'
OUTPUT_CLEAN = 'data/output/nbfc_clean_759.csv'
OUTPUT_PROBLEMATIC = 'data/output/nbfc_problematic_175.csv'
VALIDATION_REPORT = 'backend/validation_nbfc/nbfc_validation_issues.csv'

print("="*80)
print("PATH A: NBFC QUICK LAUNCH FILTER")
print("="*80)
print(f"Input:  {INPUT_CSV}")
print(f"Output: {OUTPUT_CLEAN} (clean NBFCs)")
print(f"        {OUTPUT_PROBLEMATIC} (to fix later)")
print("="*80)

# Load P0 issue IDs from validation report
p0_issue_ids = set()

try:
    with open(VALIDATION_REPORT, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Only P0 critical issues
            if row['category'] == 'P0_Missing_Data':
                p0_issue_ids.add(int(row['id']))
    
    print(f"\n✓ Loaded validation report")
    print(f"  Found {len(p0_issue_ids)} NBFCs with P0 issues")
except FileNotFoundError:
    print(f"\n⚠️  Validation report not found: {VALIDATION_REPORT}")
    print("  Will filter based on empty operating_states and hq_state instead")
    p0_issue_ids = None

# Process NBFCs
clean_nbfcs = []
problematic_nbfcs = []

with open(INPUT_CSV, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    
    for row in reader:
        nbfc_id = int(row['id'])
        
        # Check if problematic
        is_problematic = False
        
        if p0_issue_ids is not None:
            # Use validation report
            if nbfc_id in p0_issue_ids:
                is_problematic = True
        else:
            # Fallback: check fields directly
            operating_states = row.get('operating_states', '').strip()
            hq_state = row.get('hq_state', '').strip()
            
            # Parse operating states
            try:
                states = json.loads(operating_states) if operating_states else []
                if not isinstance(states, list):
                    states = []
            except:
                states = []
            
            # Missing critical data?
            if len(states) == 0 or not hq_state:
                is_problematic = True
        
        # Categorize
        if is_problematic:
            problematic_nbfcs.append(row)
        else:
            clean_nbfcs.append(row)

# Write clean NBFCs
with open(OUTPUT_CLEAN, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(clean_nbfcs)

# Write problematic NBFCs (for later fixing)
with open(OUTPUT_PROBLEMATIC, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(problematic_nbfcs)

print("\n" + "="*80)
print("FILTERING COMPLETE!")
print("="*80)
print(f"\nClean NBFCs:        {len(clean_nbfcs):4} → {OUTPUT_CLEAN}")
print(f"Problematic NBFCs:  {len(problematic_nbfcs):4} → {OUTPUT_PROBLEMATIC}")
print(f"Total:              {len(clean_nbfcs) + len(problematic_nbfcs):4}")

print("\n" + "="*80)
print("YOUR LAUNCH NUMBERS")
print("="*80)
print(f"RBI Banks:          114 (already in Supabase)")
print(f"Clean NBFCs:        {len(clean_nbfcs):4} (ready to import)")
print(f"{'─'*80}")
print(f"TOTAL LENDERS:      {114 + len(clean_nbfcs):4} 🎉")
print("="*80)

print("\n" + "="*80)
print("NEXT STEPS")
print("="*80)
print("1. Import nbfc_clean_759.csv to Supabase")
print("2. Apply 3 auto-fixes from nbfc_auto_fixes.sql")
print("3. Test your dashboard")
print("4. GO LIVE! 🚀")
print("")
print("Later (Week 2-4):")
print("5. Fix nbfc_problematic_175.csv incrementally")
print("6. Import in batches: +50, +50, +75")
print("7. Reach 1,048 total lenders!")
print("="*80)

# Generate import instructions
with open('data/output/IMPORT_INSTRUCTIONS.txt', 'w') as f:
    f.write("SUPABASE IMPORT INSTRUCTIONS\n")
    f.write("="*80 + "\n\n")
    
    f.write("STEP 1: Apply Auto-Fixes\n")
    f.write("-"*80 + "\n")
    f.write("Go to Supabase SQL Editor and run:\n\n")
    f.write("UPDATE lenders SET operating_states = operating_states || ARRAY['Delhi'] WHERE id = 354;\n")
    f.write("UPDATE lenders SET operating_states = operating_states || ARRAY['Delhi'] WHERE id = 736;\n")
    f.write("UPDATE lenders SET operating_states = operating_states || ARRAY['Delhi'] WHERE id = 1151;\n\n")
    
    f.write("STEP 2: Import Clean NBFCs\n")
    f.write("-"*80 + "\n")
    f.write("1. Go to Supabase → Table Editor → lenders\n")
    f.write("2. Click Insert → Import data from CSV\n")
    f.write("3. Upload: nbfc_clean_759.csv\n")
    f.write("4. Click Import\n\n")
    
    f.write("STEP 3: Verify\n")
    f.write("-"*80 + "\n")
    f.write("Run in SQL Editor:\n\n")
    f.write("SELECT COUNT(*) FROM lenders;\n")
    f.write(f"-- Should show: {114 + len(clean_nbfcs)} total lenders\n\n")
    f.write("SELECT company_type, COUNT(*) FROM lenders GROUP BY company_type;\n")
    f.write("-- Should show NBFC with ~759 records\n\n")
    
    f.write("STEP 4: GO LIVE!\n")
    f.write("-"*80 + "\n")
    f.write("Your platform is ready with 873 verified lenders!\n")
    f.write("Test all filters and share your link! 🚀\n")

print("\n📄 Import instructions saved to: data/output/IMPORT_INSTRUCTIONS.txt")
print("")