"""
reset_pilot_checkpoint.py
=========================
Clears extraction checkpoints so the pilot can re-run from scratch
with updated guardrails logic.

Usage:
    python backend/reset_pilot_checkpoint.py          # preview only
    python backend/reset_pilot_checkpoint.py --confirm  # actually reset
"""

import json, sys, argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent

CHECKPOINTS = [
    ROOT / 'data' / 'output' / '.checkpoint.json',
    ROOT / 'data' / 'output' / '.rbi_checkpoint.json',
    ROOT / 'data' / 'output' / '.policy_checkpoint.json',
    ROOT / 'backend' / '.mca21_checkpoint.json',
    ROOT / 'backend' / '.enrich_policies_checkpoint.json',
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--confirm', action='store_true',
                        help='Actually reset checkpoints (dry run by default)')
    args = parser.parse_args()

    print("\n  Checkpoint Reset Tool")
    print("  " + "=" * 40)

    for path in CHECKPOINTS:
        try:
            data = json.loads(path.read_text())
            count = len(data.get('processed_ids', data.get('processed', [])))
            print(f"  {'RESET' if args.confirm else 'WOULD RESET'}: {path.name}  ({count} entries)")
            if args.confirm:
                path.unlink(missing_ok=True)
                print(f"    ✓ Deleted")
        except FileNotFoundError:
            print(f"  SKIP (not found): {path.name}")
        except Exception as e:
            print(f"  ERROR reading {path.name}: {e}")

    if not args.confirm:
        print("\n  Dry run — no files changed.")
        print("  Run with --confirm to actually reset.\n")
    else:
        print("\n  ✓ Checkpoints cleared. Re-run the extraction scripts.\n")

if __name__ == '__main__':
    main()
