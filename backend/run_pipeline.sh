#!/bin/bash
LOG="D:/Lender-Platform2/lender-platform/backend"

echo "=== Waiting for enrich_lenders.py to complete ==="
while ! grep -q "ENRICHMENT COMPLETE" "$LOG/enrich_run.log" 2>/dev/null; do
  sleep 30
done

echo "=== enrich_lenders done. Starting compute_derived_fields.py ==="
cd "D:/Lender-Platform2/lender-platform"
python backend/compute_derived_fields.py 2>&1 | tee "$LOG/derived_run.log"
echo "EXIT_DERIVED=$?"

echo "=== Starting audit_hallucinations.py ==="
python backend/audit_hallucinations.py 2>&1 | tee "$LOG/audit_run.log"
echo "EXIT_AUDIT=$?"

echo "=== PIPELINE DONE ==="
