"""
merge_nbfc_csvs.py - Merge Missing + Verified NBFC Files
=========================================================
Combines nbfc_extracted_verified_MISSING.csv + nbfc_extracted_verified.csv
"""

import csv
import sys
from pathlib import Path

# File paths
missing_file = Path('data/output/nbfc_extracted_verified_MISSING.csv')
verified_file = Path('data/output/nbfc_extracted_verified.csv')
output_file = Path('data/output/nbfc_extracted_COMPLETE.csv')

print("="*70)
print("  NBFC CSV MERGER")
print("="*70)

# Check files exist
if not missing_file.exists():
    print(f"\n❌ Missing file not found: {missing_file}")
    sys.exit(1)

if not verified_file.exists():
    print(f"\n❌ Verified file not found: {verified_file}")
    sys.exit(1)

# Read missing data (99-318)
print(f"\n📖 Reading: {missing_file.name}")
missing_data = []
with open(missing_file, encoding='utf-8') as f:
    reader = csv.DictReader(f)
    missing_data = list(reader)
    missing_ids = [int(row['id']) for row in missing_data]
    print(f"   Records: {len(missing_data)}")
    print(f"   ID range: {min(missing_ids)} to {max(missing_ids)}")

# Read verified data (319+)
print(f"\n📖 Reading: {verified_file.name}")
verified_data = []
with open(verified_file, encoding='utf-8') as f:
    reader = csv.DictReader(f)
    verified_data = list(reader)
    verified_ids = [int(row['id']) for row in verified_data]
    print(f"   Records: {len(verified_data)}")
    print(f"   ID range: {min(verified_ids)} to {max(verified_ids)}")

# Combine
print(f"\n🔗 Merging files...")
all_data = missing_data + verified_data

# Sort by ID
all_data.sort(key=lambda x: int(x['id']))
all_ids = [int(row['id']) for row in all_data]

# Statistics
min_id = min(all_ids)
max_id = max(all_ids)
expected_count = max_id - min_id + 1
actual_count = len(all_data)
missing_count = expected_count - actual_count

print(f"\n📊 Combined Dataset:")
print(f"   Total records: {actual_count}")
print(f"   ID range: {min_id} to {max_id}")
print(f"   Expected IDs: {expected_count}")
print(f"   Actual IDs: {actual_count}")

if missing_count > 0:
    print(f"   ⚠️  Still missing: {missing_count} records")
    
    # Find which IDs are missing
    full_range = set(range(min_id, max_id + 1))
    actual_ids = set(all_ids)
    still_missing = sorted(full_range - actual_ids)
    
    print(f"\n   Missing ID ranges:")
    # Group consecutive IDs
    ranges = []
    start = still_missing[0]
    end = still_missing[0]
    
    for i in range(1, len(still_missing)):
        if still_missing[i] == end + 1:
            end = still_missing[i]
        else:
            ranges.append((start, end))
            start = still_missing[i]
            end = still_missing[i]
    ranges.append((start, end))
    
    for start, end in ranges:
        if start == end:
            print(f"     - ID {start}")
        else:
            print(f"     - IDs {start}-{end} ({end - start + 1} records)")
else:
    print(f"   ✅ No gaps - Complete dataset!")

# Check for duplicates
from collections import Counter
id_counts = Counter(all_ids)
duplicates = [id for id, count in id_counts.items() if count > 1]

if duplicates:
    print(f"\n   ⚠️  Duplicate IDs found: {duplicates}")
    print(f"   Removing duplicates...")
    
    # Keep first occurrence of each ID
    seen = set()
    unique_data = []
    for row in all_data:
        row_id = int(row['id'])
        if row_id not in seen:
            seen.add(row_id)
            unique_data.append(row)
    
    all_data = unique_data
    print(f"   After dedup: {len(all_data)} records")

# Write combined file
print(f"\n💾 Writing to: {output_file}")
with open(output_file, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=all_data[0].keys())
    writer.writeheader()
    writer.writerows(all_data)

print(f"\n✅ SUCCESS!")
print(f"   Combined file: {output_file}")
print(f"   Total records: {len(all_data)}")
print("="*70)

if missing_count > 0:
    print(f"\n⚠️  Note: {missing_count} NBFCs still missing")
    print("   You may want to extract these separately")
else:
    print(f"\n🎉 Complete dataset ready for Supabase import!")

print("\nNext step: Import to Supabase")
print("  1. Go to Supabase Table Editor")
print("  2. Open 'lenders' table")
print("  3. Click 'Insert' → 'Import CSV'")
print(f"  4. Upload: {output_file}")