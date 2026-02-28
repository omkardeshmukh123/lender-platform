"""
extract_missing_nbfcs.py - Extract Missing NBFCs (99-318)
==========================================================
Extracts the 220 NBFCs that are missing from the CSV file
"""

import json

# Update checkpoint to mark 99-318 as NOT processed
checkpoint_path = 'data/output/.checkpoint.json'

with open(checkpoint_path, 'r') as f:
    checkpoint = json.load(f)

# Remove IDs 99-318 from processed list
original_count = len(checkpoint['processed_ids'])
checkpoint['processed_ids'] = [
    id for id in checkpoint['processed_ids'] 
    if not (99 <= id <= 318)
]
new_count = len(checkpoint['processed_ids'])

# Update stats
checkpoint['stats']['success'] = checkpoint['stats']['success'] - (original_count - new_count)

print(f"Checkpoint updated:")
print(f"  Removed: {original_count - new_count} IDs")
print(f"  Remaining: {new_count} IDs")
print(f"  Will extract: 99-318")

with open(checkpoint_path, 'w') as f:
    json.dump(checkpoint, f, indent=2)

print("\n✅ Checkpoint updated!")
print("Now run: python backend/run_nbfc_extraction_PRODUCTION_v2.py")
print("It will extract only IDs 99-318")