"""
reset_checkpoint.py - Reset extraction checkpoint to start fresh
"""

from pathlib import Path
import json

ROOT = Path(__file__).parent.parent
CHECKPOINT_FILE = ROOT / 'data' / 'output' / '.checkpoint.json'

if CHECKPOINT_FILE.exists():
    print(f"Found checkpoint: {CHECKPOINT_FILE}")
    
    # Read current state
    with open(CHECKPOINT_FILE, 'r') as f:
        data = json.load(f)
    
    print(f"  Processed: {len(data.get('processed_ids', []))}")
    print(f"  Failed: {len(data.get('failed_ids', []))}")
    
    # Confirm
    response = input("\nReset checkpoint? (yes/no): ")
    
    if response.lower() == 'yes':
        CHECKPOINT_FILE.unlink()
        print("✓ Checkpoint deleted - will start from beginning")
    else:
        print("Cancelled")
else:
    print("No checkpoint found - already starting fresh")