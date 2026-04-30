# Policy Data Architecture — Design Spec
**Date:** 2026-04-30  
**Status:** Approved for implementation  
**Scope:** Policy enrichment pipeline + schema restructure (no Gemini)

---

## Problem Statement

The `policies` table has 1,876 rows but critical financial fields are nearly empty:
- `interest_rate`: 38 / 1,876 populated (2%)
- `loan_amount`: 180 / 1,876 populated (10%)
- `credit_score`: 34 / 1,876 populated (2%)

Additionally, multiple enrichment scripts write directly to `policies`, creating write conflicts with no trust hierarchy or audit trail.

---

## Goals

1. Fill policy financial fields using three trusted, non-AI data sources
2. Prevent lower-trust data from overwriting higher-trust data
3. Store values as ranges (`value_min`, `value_max`) — eliminates point-estimate hallucinations
4. Validate all incoming data through a Bank Manager layer before it reaches the vault
5. Keep the match engine and API unchanged during migration

---

## Mental Model — Branch Banking

```
Enrichment Scripts            Bank Manager               Vault
(Teller Windows)              (Validation Engine)        (Verified Records)

BSE XBRL ──────┐
BankBazaar ────┤──► policy_enrichments_inbox ──► bank_manager.py ──► policy_enrichments (validated=true)
FPC PDF ───────┘                                        │
                                               Rejected → validated=false
                                               rejection_reason stored
                                                        │
                                               policies_enriched (materialized view)
                                               ← read by match engine + API
```

- **Teller windows** — enrichment scripts; submit data, no direct vault access
- **Bank Manager** — `backend/bank_manager.py`; four validation checks; sole write authority to vault
- **Vault** — `policy_enrichments WHERE validated=true`
- **Counter** — `policies_enriched` materialized view; same column shape as current `policies`; read by API and match engine

---

## Source Trust Hierarchy

| Source | Rank | Coverage | Confidence | Notes |
|---|---|---|---|---|
| BSE XBRL | 4 | 117 listed lenders | 0.95 | Audited XBRL filing, RBI-mandated FPC section |
| FPC PDF | 3 | ~400 unlisted NBFCs | 0.80 | Deterministic PDF parse with pdfplumber — no AI |
| BankBazaar | 2 | ~200 major lenders | 0.70 | Structured comparison pages; may lag 2–4 weeks |
| ~~Gemini~~ | — | — | — | Removed from enrichment pipeline entirely |

Higher rank always wins. Rank 1 never overwrites Rank 2+. Gaps remain null rather than AI-filled.

---

## Schema

### `policies` table — slimmed to identity only

Financial fields removed. All other existing columns are kept intact:
`id`, `lender_id`, `product_name`, `loan_type`, `employment_types`, `operating_states`, `approval_status`, `notes`, `processing_notes`, `created_at`, `updated_at`.

**Removed columns** (migrated to `policy_enrichments`):
`interest_rate_min`, `interest_rate_max`, `loan_amount_min`, `loan_amount_max`, `tenure_min`, `tenure_max`, `credit_score_min`, `processing_fee_min`, `processing_fee_max`.

### `source_rank_rules` table

```sql
CREATE TABLE source_rank_rules (
    source        TEXT PRIMARY KEY,
    rank          SMALLINT NOT NULL,
    display_name  TEXT
);
```

Seeded with `bse_xbrl` (4), `fpc_pdf` (3), `bankbazaar` (2), `legacy` (1).
`legacy` is the attribution for data backfilled from the old wide `policies` columns.

### `policy_enrichments` table — the vault

```sql
CREATE TABLE policy_enrichments (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id        UUID REFERENCES policies(id) ON DELETE CASCADE,
    field            TEXT NOT NULL,        -- 'interest_rate' | 'loan_amount' | 'tenure' | 'credit_score' | 'processing_fee'
    value_min        NUMERIC,
    value_max        NUMERIC,
    source           TEXT REFERENCES source_rank_rules(source),
    source_rank      SMALLINT NOT NULL,
    validated        BOOLEAN DEFAULT false,
    rejection_reason TEXT,                 -- null when validated=true
    raw_value        JSONB,                -- original source payload, never altered
    confidence       NUMERIC(4,3),         -- 0.000–1.000
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (policy_id, field, source)
);

CREATE INDEX idx_pe_policy_field ON policy_enrichments (policy_id, field);
CREATE INDEX idx_pe_validated    ON policy_enrichments (policy_id, field, source_rank DESC)
    WHERE validated = true;
```

Fields covered: `interest_rate`, `loan_amount`, `tenure`, `credit_score`, `processing_fee`.

### `policies_enriched` materialized view — the counter

Pivots `policy_enrichments` into the wide shape the current API and match engine expect. For each financial field, a `LATERAL` subquery picks the highest-rank validated row.

```sql
CREATE MATERIALIZED VIEW policies_enriched AS
SELECT
    p.*,
    ir.value_min  AS interest_rate_min,
    ir.value_max  AS interest_rate_max,
    ir.source     AS interest_rate_source,
    la.value_min  AS loan_amount_min,
    la.value_max  AS loan_amount_max,
    la.source     AS loan_amount_source,
    t.value_min   AS tenure_min,
    t.value_max   AS tenure_max,
    cs.value_min  AS credit_score_min,
    pf.value_min  AS processing_fee_min,
    pf.value_max  AS processing_fee_max
FROM policies p
LEFT JOIN LATERAL (
    SELECT * FROM policy_enrichments
    WHERE policy_id = p.id AND field = 'interest_rate' AND validated = true
    ORDER BY source_rank DESC LIMIT 1
) ir ON true
-- same LATERAL pattern for loan_amount, tenure, credit_score, processing_fee
WITH DATA;

CREATE UNIQUE INDEX ON policies_enriched (id);
```

Refresh command (runs after every enrichment batch):
```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY policies_enriched;
```

---

## Bank Manager Validation Engine

### File: `backend/bank_manager.py`

Four checks in sequence. All must pass for `validated=true`.

### Check 1 — RBI Guardrail Ranges

Hard floors and ceilings derived from RBI circulars:

| Field | Min | Max | Unit |
|---|---|---|---|
| interest_rate | 8.0 | 48.0 | % per annum |
| loan_amount | 0.5 | 50,000 | ₹ Lakhs |
| tenure | 1 | 360 | months |
| credit_score | 300 | 900 | score |
| processing_fee | 0.0 | 5.0 | % of loan amount |

Violations: rejected, `rejection_reason='guardrail_violation'`, not stored.

### Check 2 — Range Sanity

- `value_min` must be ≤ `value_max`
- `interest_rate` spread must be ≤ 20 percentage points
- `loan_amount` max must be ≤ 100× min

Violations: rejected, `rejection_reason='range_invalid'`, not stored.

### Check 3 — Source Rank Gate

```python
def rank_gate(policy_id, field, incoming_rank) -> bool:
    existing = get_highest_validated_rank(policy_id, field)
    if existing is None:         return True   # vault empty — accept
    if incoming_rank > existing: return True   # higher trust — supersedes
    if incoming_rank == existing: return True  # same source updating itself
    return False                               # lower rank — silent reject
```

Violations: rejected, `rejection_reason='lower_rank_exists'`, not stored.

### Check 4 — Statistical Outlier (BankBazaar only)

For BankBazaar values: compare midpoint against the distribution of already-validated values for the same `loan_type`. If z-score > 3.0 and the sample has ≥ 10 validated rows, flag as outlier.

Outliers: stored with `validated=false`, `rejection_reason='outlier:z=X.X'` — visible to admin for manual review.

---

## Enrichment Pipeline

### `EnrichmentPayload` dataclass (shared interface)

```python
@dataclass
class EnrichmentPayload:
    policy_id:  str
    field:      str
    value_min:  float
    value_max:  float
    source:     str
    confidence: float
    raw_value:  dict
```

### Source 1 — BSE XBRL (`backend/enrichers/bse_xbrl.py`)
- Look up CIN → BSE scrip code → fetch annual XBRL filing
- Parse Fair Practice Code section (structured XBRL element)
- Confidence: 0.95 fixed
- Fills: `interest_rate`, `loan_amount`

### Source 2 — FPC PDF (`backend/enrichers/fpc_pdf.py`)
- Download annual report PDF from lender IR page
- Parse with `pdfplumber` — extract FPC/Interest Rate Policy table deterministically
- No AI. If table structure is unrecognised, skip the lender.
- Confidence: 0.80 fixed
- Fills: `interest_rate`, `loan_amount`, `tenure`, `processing_fee`

### Source 3 — BankBazaar (`backend/enrichers/bankbazaar.py`)
- Refactored from existing `enrich_policies_db.py`
- Emits `EnrichmentPayload` objects, routes through `bank_manager.py`
- No longer writes directly to `policies`
- Confidence: 0.70 fixed
- Fills: `interest_rate`, `loan_amount`, `tenure`, `credit_score`

### Airflow DAG — `policy_enrichment_dag.py` (Saturdays 3am)

```
Task 1: bse_xbrl_enricher      — 117 listed lenders (parallel, 10 at a time)
Task 2: fpc_pdf_enricher        — ~400 unlisted NBFCs (after task 1)
Task 3: bankbazaar_enricher     — ~200 major lenders (parallel with task 2)
Task 4: bank_manager.validate() — process all pending enrichments
Task 5: REFRESH MATERIALIZED VIEW CONCURRENTLY policies_enriched
```

Gemini is not a task. Gaps remain null.

---

## Migration Plan

### Phase 1 — Add infrastructure (zero downtime)

| Migration | What |
|---|---|
| 037_source_rank_rules.sql | Create `source_rank_rules`, seed 3 sources |
| 038_policy_enrichments.sql | Create `policy_enrichments` table + indexes |
| 039_backfill_enrichments.sql | Migrate existing `policies` financial data → `policy_enrichments` with `source='legacy'` (rank 1) |
| 040_policies_enriched_view.sql | Create `policies_enriched` materialized view |

### Phase 2 — Switch API reads

Update `backend/api/routers/policies.py`, `loans.py`, and `match_lenders()` SQL function to read from `policies_enriched` instead of `policies`. Column names are identical — no other code changes.

Run enrichment pipeline once to populate the vault.

### Phase 3 — Drop legacy columns (2-week soak)

```sql
-- Migration 041 (after 2-week soak)
ALTER TABLE policies
    DROP COLUMN interest_rate_min,
    DROP COLUMN interest_rate_max,
    DROP COLUMN loan_amount_min,
    DROP COLUMN loan_amount_max,
    DROP COLUMN tenure_min,
    DROP COLUMN tenure_max,
    DROP COLUMN credit_score_min,
    DROP COLUMN credit_score_max,
    DROP COLUMN processing_fee;
```

### Rollout Risk Table

| Risk | Mitigation |
|---|---|
| Match engine breaks | View has identical column names — no SQL changes needed |
| Bad data enters vault | Bank Manager rejects before insert; view only shows `validated=true` |
| View goes stale | `REFRESH CONCURRENTLY` < 2s for 1,876 policies; runs after every batch |
| Phase 3 drops needed column | 2-week soak with both tables live; rollback = restore column from backup |

---

## Files to Create / Modify

### New files
```
backend/bank_manager.py
backend/enrichers/__init__.py
backend/enrichers/bse_xbrl.py
backend/enrichers/fpc_pdf.py
backend/enrichers/bankbazaar.py        ← refactored from enrich_policies_db.py
backend/migrations/037_source_rank_rules.sql
backend/migrations/038_policy_enrichments.sql
backend/migrations/039_backfill_enrichments.sql
backend/migrations/040_policies_enriched_view.sql
airflow/dags/policy_enrichment_dag.py
```

### Modified files
```
backend/api/routers/policies.py        ← read from policies_enriched
backend/api/routers/loans.py           ← read from policies_enriched
backend/api/models/lender.py           ← add interest_rate_source, loan_amount_source fields
```

### Deferred (Phase 3 — after soak)
```
backend/migrations/041_drop_legacy_policy_columns.sql
```

---

## Success Criteria

| Metric | Target |
|---|---|
| `interest_rate` coverage | ≥ 400 / 1,876 policies (from 38 today) |
| `loan_amount` coverage | ≥ 600 / 1,876 policies (from 180 today) |
| Bank Manager rejection rate | Tracked in Grafana; alert if > 20% of incoming values rejected |
| View refresh time | < 5 seconds |
| Match engine query time | No regression vs current `policies` table |
| Zero Gemini calls | Enrichment pipeline makes 0 calls to Gemini API |
