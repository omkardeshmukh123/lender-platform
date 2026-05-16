# Script Review

> Reviewed 2026-04-25 | Reviewer: senior engineer + banking/RBI compliance expert
> .env file read for secrets context.

---

## CRITICAL — Security: .env Secrets Committed to Repo

**Affects all scripts (via .env)**

The `.env` file contains live production credentials that are tracked in the repository:

- `GEMINI_API_KEY=[REDACTED — key rotated]` — live Google AI key
- `SUPABASE_SERVICE_ROLE_KEY=eyJhbGci...` — full service-role JWT (bypasses all RLS policies)
- `DATABASE_URL=postgresql://postgres.rhyzqmujazmwwsweaddh:yEprBXGVjDcNljCq@...` — plaintext DB password
- `SUPABASE_JWT_SECRET=JnNd68dR...` — can forge any user token
- `GRAFANA_PG_PASSWORD=root`, `GRAFANA_ADMIN_PASSWORD=admin123`, `AIRFLOW_ADMIN_PASSWORD=Omkar@123` — weak/default passwords
- `SERPER_API_KEY`, `FIRECRAWL_API_KEY` — additional live keys

**Recommended fix:** Rotate all keys immediately. Add `.env` to `.gitignore`. Use environment-level secrets management (Vercel env vars, GitHub Secrets, Doppler, etc.). Never store credentials in source files.

---

## enrich_pilot_lenders.py

### Issues

- **[HIGH]** Duplicate `log.info('Approved %d lenders', updated)` call at lines 822–823. The approved count is logged twice on every run, which is a minor correctness issue but more importantly indicates a copy-paste error that could mask the real log line being duplicated for a mutation call in a future refactor.
  - **Fix:** Delete line 823.

- **[HIGH]** `approve_pilots()` receives `conn` as the first argument but never uses it — it opens its own fresh connection at line 811 (`fresh = get_db()`). The `conn` parameter is misleading and will cause confusion: callers believe they can control the connection lifecycle, but the function always opens a fresh connection regardless. More importantly, `conn` passed from `main()` at line 969 is already used for `fetch_db_records` earlier and is closed at line 971 (`conn.close()`), but `approve_pilots` internally creates and closes its own — this is inconsistent with the pattern used in `upsert_record`.
  - **Fix:** Remove the `conn` parameter from `approve_pilots`; it does not use it.

- **[MEDIUM]** `_is_empty()` at line 152 handles `list` but not `dict`. Gemini sometimes returns `{}` for JSON fields; this would be treated as non-empty and passed to the DB as an empty dict, potentially corrupting JSONB fields.
  - **Fix:** Add `if isinstance(val, dict) and len(val) == 0: return True`.

- **[MEDIUM]** `merge_enrichment()` at line 605 checks `if not _is_empty(raw_states)` after `fill('operating_states', raw_states)` has already been called. If `raw_states` is an empty list, `fill` silently does nothing (correct), but then the `pan_india` logic below attempts `'PAN_INDIA' in raw_states` on an empty list — harmless but wasteful. More critically, if `raw_states = ['PAN_INDIA']`, it sets `operating_states` to the literal string `'PAN_INDIA'` in the DB rather than the full state list, unlike the Gemini path at line 652 which correctly expands it.
  - **Fix:** In the scraper block (lines 599–607), add the same `['PAN_INDIA']` → `sorted(ALL_INDIA_STATES)` expansion that the Gemini block has.

- **[MEDIUM]** `upsert_record()` inner function `_execute()` at line 783 contains the dead code `row = fresh.cursor().fetchone() if False else cur.fetchone()`. The `if False` branch is unreachable. This was likely a debugging artifact. It opens a second cursor object unnecessarily on every upsert.
  - **Fix:** Replace with `row = cur.fetchone()`.

- **[MEDIUM]** `upsert_record()` at line 797 catches the exception string `'rbi_reg' in str(exc).lower()`. The actual PostgreSQL unique-constraint violation message contains the constraint name (e.g., `"lenders_rbi_registration_number_key"`), not the fragment `rbi_reg`. This catch will silently miss real unique-key conflicts and re-raise them as unhandled errors.
  - **Fix:** Check for `'23505'` (PostgreSQL unique violation SQLSTATE) or use `psycopg2.errors.UniqueViolation`.

- **[MEDIUM]** AUM category thresholds in `merge_enrichment()` at lines 675–684: `Micro < 500 Cr`, `Small 500–5000 Cr`, `Mid 5000–50000 Cr`, `Large > 50000 Cr`. These are internally consistent but conflict with the thresholds used in `compute_derived_fields.py` (same four buckets, same numbers) — no problem there. However, the thresholds are duplicated in at least 5 places in the codebase. Any future regulatory-driven threshold change will require updating all 5 sites simultaneously.
  - **Fix:** Define `_AUM_CATEGORY_THRESHOLDS` once in a shared constants module and import it everywhere.

- **[LOW]** At line 862, `null_fields[:8]` truncates the debug log output if more than 8 fields are null. During initial data load this is common and the truncated log is misleading.
  - **Fix:** Remove the `[:8]` slice or use `…` suffix to indicate truncation.

---

## compute_derived_fields.py

### Issues

- **[HIGH]** `_operating_intensity()` at line 82: a lender with `pan_india=False` and 1 state returns `'Single State'`, but a lender with 0 states returns `None`. A lender with `n=1` that should be "Single State" only gets that label; a lender with `n=2-4` gets "Regional". This is correct per the docstring, but the boundary at `n >= 5 → Regional` and `n >= 20 → Pan India` means a lender operating in exactly 19 states is classified "Regional" even though they cover more than half of India's states and UTs. This is a data quality/compliance issue: `operating_intensity='Regional'` in the DB becomes a filter signal for the borrower-matching engine. The threshold should align with the `pan_india` threshold used everywhere else (20 states).
  - **Fix:** The current logic is consistent with the rest of the codebase. Document the 5/20 thresholds explicitly as constants.

- **[HIGH]** `_should_be_pan_india()` at line 176 ignores `--force` mode. If `pan_india=True` in DB and `states` is empty (data corruption or partial update), this function returns `True` unconditionally, perpetuating the wrong value. On `--force` runs, you want to recompute from states.
  - **Fix:** In the `pan_india` block at line 275, also re-evaluate when `args.force` is set: `if (not pan or args.force) and _should_be_pan_india(states, pan):`.

- **[MEDIUM]** `_extract_hq_state()` at line 160 calls `part.title()` to canonicalize state names. `"MAHARASHTRA".title()` → `"Maharashtra"` — correct. But `"jammu & kashmir".title()` → `"Jammu & Kashmir"` — correct. However `"andaman and nicobar islands".title()` → `"Andaman And Nicobar Islands"` (capital `And`) which does NOT match the canonical value `"Andaman and Nicobar Islands"`. This produces a non-matching hq_state that will break any downstream state-level filtering.
  - **Fix:** Build a lookup dict `{s.lower(): s for s in _INDIA_STATES_CANONICAL}` and use that instead of `.title()`.

- **[MEDIUM]** Supabase `upsert` at line 290 uses `on_conflict='id'`. If the `id` column is not set on any row (e.g., it was `None`), this silently inserts a new row rather than updating. The code always sets `{'id': lid, ...}` so this is low probability, but the `id` coercion from Supabase response is unvalidated.
  - **Fix:** Assert `lid is not None` before appending to `updates`.

- **[LOW]** `_TOTAL_INDIAN_STATES = 36` at line 80 is defined but never used in the logic. The "Pan India" threshold uses hardcoded `20` (line 89).
  - **Fix:** Replace the hardcoded `20` with `_TOTAL_INDIAN_STATES // 2` or define a `PAN_INDIA_MIN_STATES` constant.

---

## audit_hallucinations.py

### Issues

- **[CRITICAL]** `PolicyStats` and `LenderStats` classes define `nulled: Dict[str, int] = {}` and `total = 0`, `flagged = 0` as **class-level attributes** at lines 151–153 and 293–295. In Python, mutable class-level attributes are shared across all instances. If `audit_policies` and `audit_lenders` are both called in the same process (the default when neither `--policies` nor `--lenders` is passed), the second call's stats accumulate into the same dict as the first call. The `report()` method will print combined/corrupted counts.
  - **Fix:** Move all mutable fields to `__init__`: `def __init__(self): self.total = 0; self.flagged = 0; self.nulled = {}`.

- **[HIGH]** `audit_policies()` at line 212: after nulling `ir_min` with `flag('interest_rate_min', ...)`, the inversion check at line 212 uses the local variable `ir_min` (now `None`) but `flag()` sets `nulls['interest_rate_min'] = None` in the update dict. The per-loan-type rate checks at line 220 still use the original `ir_min` before the range-flag nulled it, because `flag()` does not update the local `ir_min` variable. This means a rate that was nulled by the range check can still be used in the per-loan-type check that follows, leading to redundant/incorrect flags.
  - **Fix:** After each `flag('interest_rate_min', ...)` call, immediately set `ir_min = None` (same local variable), as done at line 213.

- **[HIGH]** Microfinance (NBFC-MFI) rate ceiling: `LOAN_TYPE_RATE` at line 105 sets `'Microfinance': (18.0, 36.0)`. The RBI MFI pricing guidelines (updated 2022) cap the margin spread, not a hard 36% ceiling. More importantly, audit uses a 20% tolerance ceiling: `lt_hi * 1.20 = 43.2%`. But Credit card ceiling is 48% × 1.20 = 57.6%, which is close to the absolute cap of 60%. These tolerance bands need explicit review — a 20% tolerance on an already-high ceiling is loose enough to let significant hallucinations through.
  - This is a compliance advisory, not a code bug, but worth flagging for the team.

- **[MEDIUM]** `AMOUNT_MAX = 1_000_000` Lakhs = ₹10,000 Crore, described in comments as "no single retail policy exceeds this." However, large PSU bank MSME loan programs and corporate working capital lines can legitimately exceed ₹10,000 Cr. This threshold will incorrectly null valid large-ticket B2B/corporate lending policies from banks in the DB.
  - **Fix:** Separate the cap by `loan_type` or raise `AMOUNT_MAX` to `10_000_000` (₹1 lakh Cr) for wholesale/corporate types and keep ₹10,000 Cr for retail.

- **[MEDIUM]** `processing_fee` check at line 261 uses `_out_of_range(val, 0, FEE_MAX)`. `FEE_MAX = 10.0`. RBI's Fair Lending Practice directions (2023) do not cap processing fees at 10% — that is an internal guardrail, not an RBI rule. Many lenders legitimately charge 1–3% with GST, plus other charges that when summed could approach 5–6%. Nulling anything above 10% is appropriate, but calling it "RBI guideline cap" in the comment at line 87 is misleading and could cause compliance questions.
  - **Fix:** Change comment to "internal guardrail — conservative ceiling" and document the business rationale.

- **[LOW]** The gemini-only low-confidence null block at line 273 checks `'bankbazaar' not in source and 'paisabazaar' not in source`. If the `data_source` column is updated in the future to use different source names, these string literals will silently stop matching. Consider defining these as constants.

---

## enrich_policies_db.py

### Issues

- **[HIGH]** `_completeness()` at line 124 does NOT include `credit_score_max` or `min_age`/`max_age` in the score even though they are in the DB schema. More critically, `processing_fee` and `prepayment_allowed` are included, but they are rarely present on NBFC websites and their absence disproportionately lowers completeness scores, causing policies to be incorrectly routed to `needs_review`. This is a calibration issue that affects real borrower matching.
  - **Fix:** Weight completeness by field importance, or at minimum document which fields are expected to be sparse.

- **[HIGH]** `_policy_to_db_row()` at line 150 always sets `approval_status = 'pending'`, overwriting any previously approved policy on upsert. A policy that was manually approved by an admin will be reset to `pending` on every re-enrichment run, even if the data hasn't changed.
  - **Fix:** Only set `approval_status = 'pending'` on INSERT, not on UPDATE. Use the `on_conflict` upsert to exclude `approval_status` from the update columns, similar to how `upload_lenders.py` handles this for lenders.

- **[MEDIUM]** `fetch_approved_lenders()` at line 177: the query `not_.is_('website', 'null')` uses Supabase PostgREST syntax. If a website is stored as an empty string `''` rather than SQL `NULL`, this filter passes through lenders with no usable website. The subsequent code at line 295 skips them with `if not website:` and checkpoints them — correct behavior, but they should be filtered at the DB level to avoid unnecessary DB round-trips.
  - **Fix:** Add `.neq('website', '')` to the query.

- **[MEDIUM]** `upsert_policies()` at line 242 uses `on_conflict='lender_id,product_name,loan_type'`. The `product_name` column can differ cosmetically between scraper runs (e.g., `"MSME Loan"` vs `"MSME Term Loan"`), causing duplicate policy rows per lender/loan_type combination. The `run_policy_extraction.py` uses `product_name_normalized` as the conflict key, which is the correct field.
  - **Fix:** Change `on_conflict='lender_id,product_name,loan_type'` to `on_conflict='lender_id,product_name_normalized,loan_type'` (matching `run_policy_extraction.py`).

- **[LOW]** `BATCH_SIZE = 50` at line 73 and `RATE_DELAY = 2.0` at line 74 are module-level constants. If this script is imported into a test or another script (e.g., scheduler), changing these values in tests requires monkey-patching module globals.

---

## run_policy_extraction.py

### Issues

- **[HIGH]** Pre-flight check at lines 70–81 calls `sys.exit(1)` at module import time if `GEMINI_KEY` is not set or input CSV is missing. This makes the module impossible to unit test or import in any context where those files don't exist. The scheduler imports from this module via `run_nbfc_extraction.py`; if the NBFC extraction module's pre-flight fails, it brings down the scheduler.
  - **Fix:** Move the pre-flight checks inside `main()`, not at module level.

- **[HIGH]** `_safe_float()` at line 648: `float(re.sub(r'[^\d.]', '', s)) or None`. If the cleaned string is `'0'` or `'0.0'`, `float('0.0') or None` evaluates to `None` because `0.0` is falsy in Python. A legitimate `0%` processing fee (zero-cost EMI products) will be discarded. This is a data quality bug.
  - **Fix:** Use `x = float(re.sub(r'[^\d.]', '', s)); return x if x else None` only after explicitly checking `if s` (already done), or use `return float(...) if s else None` without the `or None`.

- **[HIGH]** MFI ticket size cap check at line 691: `if p.loan_amount_max and p.loan_amount_max > MFI_MAX_TICKET_LAKHS`. `MFI_MAX_TICKET_LAKHS = 3.0` Lakhs = ₹3 Lakh. Per RBI's revised MFI guidelines (March 2022), the household income cap changed and per-borrower loan caps were revised. The current cap in the code (₹3L) is the pre-2022 limit. Post-2022, the aggregate indebtedness cap is ₹3 lakh per household (across all MFIs), and individual loan size can be up to that limit per RBI's scale-based regulation. The code logic correctly implements a per-loan cap at ₹3L max, but this should be reviewed against the current RBI master direction.
  - **Advisory:** Cross-check against the latest RBI Master Direction on NBFC-MFI (updated 2022) to confirm this threshold is still correct.

- **[MEDIUM]** `Checkpoint.mark_failed()` at line 1051 adds the lender ID to `self.done` as well as `self.failed`. On a normal restart (without `--retry-failed`), failed lenders are skipped because they are in `done`. This is correct per the design. However, the `stats['lenders_failed']` counter is incremented here but the `stats` is a `Counter` initialized in `__init__` — if `mark_failed()` is called for the same lender twice (e.g., due to a bug in the calling code), `lenders_failed` double-counts.
  - **Fix:** In `mark_failed()`, check `if lid not in self.failed` before incrementing the counter.

- **[MEDIUM]** `validate_policy_logic()` at line 575 rejects any `interest_rate_min < 5.0%` for non-Consumer Durable loans. Agriculture loan minimum per RBI Kisan Credit Card scheme is 4% (subsidized). Government/priority sector agriculture loans legitimately sit below 5%. The hard rejection here will discard all agriculture loan policies from PSU banks that participate in interest subvention schemes.
  - **Fix:** Add `'Agriculture Loan'` to the exception list alongside `'Consumer Durable Loan'`, or lower the floor to `4.0%` with a per-loan-type check using `LOAN_TYPE_RATE_RANGES`.

- **[LOW]** `upload_policies_to_supabase()` at line 149 uses `on_conflict="lender_id,product_name_normalized,loan_type"` (with `product_name_normalized`) but `_to_db_row()` at line 122 includes `product_name_normalized` in `_DB_POLICY_COLS`, so the field will be present in the row — this is correct. However, there is no guarantee that `product_name_normalized` is populated before upload: if `build_policies()` returns early for a heuristic path, `p.product_name_normalized` may be empty string, causing the conflict key to be `(lender_id, '', loan_type)` — technically unique but semantically wrong.

---

## run_rbi_extraction.py

### Issues

- **[HIGH]** Pre-flight at lines 56–65: `sys.exit(1)` is called at module import time if `RBI_DIR` does not exist or `GEMINI_KEY` is not set. Same issue as `run_policy_extraction.py` — makes this module unimportable in any context (tests, scheduler) where those conditions are not met.
  - **Fix:** Move pre-flight checks inside `main()`.

- **[HIGH]** `build_lender()` at line 795: `intensity` for non-pan-India lenders with 0 verified states falls to `intensity = 'Single State'` (line 795). A bank with no scraped/Gemini states (e.g., a foreign bank with limited branches) gets labeled "Single State" even though it may operate in multiple states — the absence of data should produce `None`, not a positive assertion.
  - **Fix:** Change the `len(op_states) <= 1` branch to `len(op_states) == 0 → None, len(op_states) == 1 → 'Single State'`.

- **[HIGH]** `_ALWAYS_PAN_INDIA = {'PSU Bank'}` at line 151: when a PSU Bank matches this condition, `op_states = ALL_INDIA_STATES` (all 36 states/UTs) is set at line 785. This is then stored as a JSON array of 36 strings in the CSV and subsequently upserted to the DB. The `operating_states` column in Supabase is a TEXT[] column. 36 state names per bank across hundreds of PSU banks creates significant storage and query overhead. More importantly, it's logically correct (PSU banks have pan-India license), but the scraper+Gemini pipeline for NBFCs does NOT do this automatic expansion — creating inconsistency in how pan_india is represented across bank types.
  - **Advisory:** This is by design per comments, but should be documented in the DB column comment.

- **[MEDIUM]** `assign_rbi_status()` at line 1016 uses `fragment in name_lower` substring matching. The fragment `'yes bank'` would match any company whose name contains "yes bank" (e.g., a hypothetical "New Yes Bank Fintech"). False positive matches can incorrectly mark active lenders as `restricted`.
  - **Fix:** Use word-boundary matching: `re.search(r'\b' + re.escape(fragment) + r'\b', name_lower)`.

- **[MEDIUM]** `_validate_bank_capability()` at line 1083: the rules only cover `Foreign Bank` and `Cooperative Bank`. `PSU Bank` and `Private Bank` have no capability rules. While most products are valid for these types, Education Loan is not commonly offered by Cooperative Banks (correctly restricted), but Cooperative Banks in the DB may still have Education Loan in their segments from the Gemini extraction before the rules were applied.
  - **Advisory:** Consider adding explicit allowed-list rules for all bank types for completeness.

- **[MEDIUM]** `safe_float()` at line 461: `'lakh crore' in s` converts to `× 100000`. "1 lakh crore" = ₹1,00,000 Crore, which is `float('1') * 100000 = 100000.0` — correct. But if a value like "2.5 lakh crore" appears, `re.sub(r'[^\d.]', '', '2.5 lakh crore') = '2.5'`, then `2.5 * 100000 = 250000` — also correct. Edge case: if `s` contains both "lakh" and "crore" independently (e.g., "raised 50 lakh, AUM 200 crore"), the first matching branch (`lakh crore`) wins only if both words are adjacent. This could produce a wrong multiplier. This is a pre-existing parsing ambiguity.

- **[LOW]** `_bank_key()` at line 378 truncates to 60 chars. Bank names longer than 60 chars (uncommon but possible for cooperative banks) could produce checkpoint key collisions.

---

## run_nbfc_extraction.py

### Issues

- **[HIGH]** Pre-flight at lines 61–71: same module-level `sys.exit(1)` on missing input file or API key. The scheduler at line 318 does `from run_nbfc_extraction import (extract_with_gemini, ...)`. If the NBFC CSV doesn't exist at scheduler startup, the import fails and the scheduler crashes.
  - **Fix:** Guard pre-flight checks inside `if __name__ == '__main__':` block or inside `main()`.

- **[HIGH]** `verify_extracted_data()` at line 903 adds `confidence += 0.20` if AUM is "reasonable" (passes `validate_aum()`). `validate_aum()` at line 262 accepts any value between `MIN_AUM_VALUE=10` and `MAX_AUM_VALUE=10000000`. This means a hallucinated AUM of ₹5,000,000 Crore (within range) gets the same confidence boost as an accurate AUM. The confidence score from this function feeds `_compute_onboarding_tier()` at line 1392. An LLM-hallucinated AUM inflates the confidence score and can promote a lender to `provisional_lender` tier incorrectly.
  - **Fix:** AUM confidence contribution should only be awarded when `financial_source` is `scraper_high` or `scraper_med` (non-LLM source), not when AUM comes from Gemini. This is correctly handled in `build_lender()` in `run_rbi_extraction.py` (Fix 7) but not in `verify_extracted_data()` here.

- **[HIGH]** `_RBI_CATEGORY_RULES['NBFC-AA']` at line 1313: `forbidden_segments` includes `'Personal Loan', 'MSME Loan', 'Home Loan'`. NBFC-AAs (Account Aggregators) are licensed by RBI to aggregate financial data, not to lend. Lending is structurally prohibited — but the forbidden_segments list only covers a subset of loan types. An NBFC-AA with `'Gold Loan'` or `'Vehicle Loan'` in segments would pass this check. Since AAs cannot lend at all, the rule should be: if `rbi_category == 'NBFC-AA'` and `any(segments)`, reject.
  - **Fix:** Add a `'forbidden_all': True` flag to the NBFC-AA rule dict and check it in `_validate_regulatory_classification()`.

- **[MEDIUM]** `_normalize_company_name()` at line 1328 removes `'india'` as a stop word. Company names like "Indiabulls" become "bulls", and "IndiaFirst Life" becomes "first life". The normalization is too aggressive for deduplication purposes and can cause false-positive or false-negative matches.
  - **Fix:** Only remove `'india'` as a standalone word: use `r'\bindia\b'` with word boundaries. The current regex `r'\bindia\b'` is already word-bounded — verify it's correct for "Indiabulls" (it should not match since "india" is not a standalone token). Double check: `re.sub(r'\bindia\b', '', 'Indiabulls')` → `'bulls'` because `\b` matches at character boundaries, not word boundaries in the sense of requiring spaces. Actually `\b` in `'Indiabulls'` does not match between 'a' and 'b' — the regex IS correct. Disregard this sub-point.

- **[MEDIUM]** `build_guardrails_input()` at line 1227: passes `rbi_registration_number` as either the Gemini-extracted value OR the CSV `registration_number`. But if the CSV has a placeholder like `"ROW-123"` (a known issue documented in `sync_nbfc_csv.py`), this placeholder will be passed to the validator as a real registration number, potentially causing the lender to be incorrectly assigned `rbi_status='not_found'` instead of `rbi_status='unverified'`.
  - **Fix:** Apply `is_real_cor()` from `sync_nbfc_csv.py` to filter placeholders before passing to the validator.

- **[LOW]** `save_results()` at line 1169 uses `encoding='utf-8'` for output but `csv_to_rows()` in `upload_lenders.py` uses `encoding='utf-8-sig'`. If any company name contains a BOM-sensitive parser, this mismatch can cause issues when the CSV is opened in Excel and then re-fed to `upload_lenders.py`.

---

## sync_nbfc_csv.py

### Issues

- **[HIGH]** `Source B` loop at line 739 (`for i, csv_row in enumerate(csv_rows, 1):`) runs unconditionally even when `--skip-nbfc-csv` is passed. The `if not args.skip_nbfc_csv:` check at line 736 only wraps the log statement, not the loop. This means Source B always runs regardless of the flag.
  - **Fix:** Move the `for i, csv_row in enumerate(csv_rows, 1):` loop inside the `if not args.skip_nbfc_csv:` block (indent it).

- **[HIGH]** `parse_cin()` at line 179: `established_year` validity check uses hardcoded `2026` at line 204 (`if 1850 <= yr <= 2026`). When run in 2027+, any company incorporated in 2027 will have its year rejected. This is a latent bug that will silently discard correct data in the future.
  - **Fix:** Use `datetime.now().year` (already imported) instead of the hardcoded `2026`.

- **[HIGH]** `apply_update()` at line 458: `mca21_enriched_at` is expected to be passed as a sentinel string `'NOW()'` in the `updates` dict. However, `mca21_enriched_at` is popped from `updates` at line 714 (`mca21_at = updates.pop('mca21_enriched_at', None)`) BEFORE calling `apply_update()`. The function itself also does not handle the `'NOW()'` sentinel string — it would pass it as a literal string to `psycopg2` if it were not popped. The two-step pop-then-separate-update pattern is correct in the calling code but fragile: if `mca21_enriched_at` is forgotten to be popped in any future call site, `'NOW()'` gets inserted as a literal string into a timestamp column.
  - **Fix:** Either handle `'NOW()'` sentinel explicitly in `apply_update()`, or use `psycopg2`'s `AsIs('NOW()')` wrapper.

- **[MEDIUM]** `bulk_approve()` at line 507: approves lenders where `array_length(primary_loan_segments, 1) > 0`. If `primary_loan_segments` is stored as an empty JSON array `'[]'` (text) rather than a SQL NULL, `array_length()` on a text column will throw a SQL error. The column type in the DB must be `TEXT[]` for this to work correctly. If the column is `JSONB` or `TEXT`, this query will fail silently or raise an error.
  - **Fix:** Verify the column type. If it's TEXT[], use the standard `array_length()`. If JSONB, use `jsonb_array_length(primary_loan_segments) > 0`.

- **[MEDIUM]** Fuzzy name matching at line 688 (Source A): for each RBI Excel row, the code iterates over ALL `db_lenders` to find the best match (`O(n×m)` where n=9188 RBI rows, m=DB lender count). At scale this is slow but acceptable for a batch script. More critically: `best_score` is reset to `0.0` for each `rbi_row`, but `best_db` is set to the first DB record that exceeds `0.0`. If two DB records have the same token overlap score, the first one encountered wins — deterministic but potentially wrong. The threshold `args.threshold=0.55` is reasonable for most cases.

- **[LOW]** `_COR_RE` at line 149 does not match CoR format `"B-13.02437"` because the pattern `[A-Z]{1,4}-\d{3,}` expects 3+ digits after the dash. `"13.02437"` contains a dot, so the digit group `\d{3,}` will only match `"02437"` (5 digits after the dot). The full CoR won't be matched. This could miss legitimate CoR numbers in that format.
  - **Fix:** Update the regex to `[A-Z]{1,4}-[\d.]{3,}` or use a more specific pattern for each known CoR format variant.

---

## fetch_annual_report.py

### Issues

- **[HIGH]** `_download_pdf()` at line 149 checks `if len(content) > 10_000`. A PDF with only a cover page or a corrupted PDF could pass this check (> 10KB) but yield empty or garbage text. The `_extract_text_from_pdf()` call returns empty string for such files — handled. However, there is no timeout on `r.iter_content(8192)` — a server that streams slowly can hang indefinitely. The `timeout=30` parameter to `session.get()` only sets the connection and read timeouts for initial response headers, not for streaming body.
  - **Fix:** Use `stream=True` with an explicit content-length check before downloading, or set a maximum download size (e.g., 50 MB) to avoid hanging on large PDFs.

- **[HIGH]** `_extract_aum()` at line 182 takes `max(values)` — the largest AUM match. For NBFCs, the MD&A section often mentions both "portfolio AUM" and "securitised AUM" or "off-book AUM," and the total could be a sum. Taking the max among multiple matches could return the segment AUM (e.g., gold loan AUM = ₹500 Cr) and miss the total AUM (₹2,000 Cr) if the total appears earlier in the document but with a smaller number in a different unit. The regex requires `crore/cr` suffix so unit confusion is bounded, but the "take max" heuristic is still imprecise.
  - **Advisory:** This is a known approximation. Add a comment explaining the heuristic and its limitations.

- **[MEDIUM]** `_find_annual_report_url()` at line 112 imports `requests` and `BeautifulSoup` inside the function body. These are already available at the module level (via the `session` parameter). The import inside the function works but is unconventional and will be re-executed on every call.
  - **Fix:** Move the imports to the top of the file.

- **[MEDIUM]** Policy upsert at line 416: `on_conflict='lender_id,loan_type'` — this is correct for annual report FPC rates (one rate range per loan type per lender). However, if the DB has an existing policy from a scraper run with `product_name='MSME Term Loan - Secured'` and this script upserts with `product_name=''` (not set), the conflict key `(lender_id, loan_type)` may not match because the policies table's actual unique constraint uses `(lender_id, product_name_normalized, loan_type)` per `run_policy_extraction.py`. The `on_conflict` here bypasses `product_name_normalized` and will duplicate policies.
  - **Fix:** Set `product_name` and `product_name_normalized` on FPC policy rows before upserting, or use the full 3-column conflict key.

- **[MEDIUM]** `_extract_fpc_rates()` at line 211 searches only the first 5000 characters after the "Fair Practice Code" header. Many annual reports have the FPC in appendices far into the document. The 5000-char window will miss rates that appear after boilerplate text within the FPC section.
  - **Fix:** Increase the window to 10,000–15,000 characters, or parse until the next major section header.

- **[LOW]** The `lender_update` dict at line 383 adds `'data_source': 'annual_report_pdf'` only when `changed = True`. But `changed` is only set to `True` when AUM, employee count, or branch count are extracted. If only FPC rates are extracted (no lender-level updates), `data_source` is not updated, which is correct behavior. However, the `data_source` update at the lender level overwrites whatever prior source was recorded (e.g., `'bse_financial_result'`), destroying provenance information.
  - **Fix:** Append to `data_source` rather than overwriting, or use a separate `financial_data_source` field.

---

## fetch_bse_financials.py

### Issues

- **[HIGH]** `_parse_crores()` at line 84 converts BSE values from Lakhs to Crores by dividing by 100. BSE's financial API actually reports values in **Lakhs** for most endpoints, but some consolidated balance sheet items are in **Rupees** (not Lakhs), and the `TotalAssets` fallback key in particular is often in Rupees. Dividing Rupees by 100 to get Crores produces a value 1,000× too small (e.g., SBI total assets ₹6,80,000 Cr would be returned as ₹6,800 Cr). This is a critical data quality bug that will silently store wrong AUM values in the DB.
  - **Fix:** Inspect the actual BSE API response units for each key. Use `NetAdvances` and `Advances` keys (which are reliably in Lakhs for NBFC financials) and explicitly exclude `TotalAssets` from the AUM extraction, or document the unit assumption per-key.

- **[HIGH]** `_extract_aum_from_result()` at line 175 falls back to `TotalAssets` when no `NetAdvances`/`Advances` key is found. For banks and NBFCs, Total Assets includes equity, fixed assets, and investments — it is NOT equivalent to AUM/Loan Book. Using Total Assets as AUM will overstate AUM by 20–40% for NBFCs and by a factor of 2–5× for banks. This corrupts the AUM-based tiering logic (`Micro/Small/Mid/Large`) and the lender ranking engine.
  - **Fix:** Remove `TotalAssets` from `aum_keys`. Return `None` when no net advances key is found rather than falling back to an incorrect proxy.

- **[MEDIUM]** `_search_scrip()` at line 102 uses the BSE search API which requires a `Referer: https://www.bseindia.com/` header. BSE has anti-scraping measures and may block requests that don't come from a real browser session. The `HEADERS` dict at line 72 includes `Referer` — this is necessary. However, no CSRF token or session cookie is included, meaning BSE may reject requests from this script if their anti-bot measures are active. The `session.get()` call will silently return a non-OK response; the code handles this with `if not r.ok: continue`, so the failure is safe but silent.
  - **Advisory:** BSE public API terms should be reviewed before use in production. Consider using SEBI/NSE data or a licensed data provider for financial data.

- **[MEDIUM]** Checkpoint at line 213 stores done IDs as a flat JSON array (not a dict with metadata). If the checkpoint file is corrupted (partial write during kill signal), `json.loads()` at line 216 will fail silently and return an empty set, causing all previously completed lenders to be re-processed.
  - **Fix:** Add a try/except with fallback to empty set (already done at line 216–218 — this is correct).

- **[LOW]** `CHECKPOINT_FILE.unlink(missing_ok=True)` at line 350 clears the checkpoint after a full successful run. However, `args.dry_run` check at line 331 is `if not args.dry_run and updates:` — correct, writes are skipped. But the checkpoint is still updated during dry-run (`done.add(str(lid))` and `_save_checkpoint(done)` at line 326–327 run regardless of dry-run mode). A dry-run will mark lenders as done without actually fetching their financials, so a subsequent real run won't process them.
  - **Fix:** Skip `done.add()` and `_save_checkpoint()` calls when `args.dry_run` is True.

---

## upload_lenders.py

### Issues

- **[HIGH]** `csv_to_rows()` at line 150 skips rows where `extraction_status` is not in `('success', 'partial', '')`. In `run_rbi_extraction.py`, `extraction_status` is set to `'merged'` for merger lineage records. These records are intentionally excluded from the product layer but contain valid provenance data. The upload script will silently skip them. If the intent is to upload them for audit trail purposes, the check is wrong; if the intent is to exclude them, a log message should be emitted.
  - **Fix:** Add `'merged'` to the allowed list if lineage records should be uploaded, OR add an explicit log: `logging.info(f"Skipping {name} (extraction_status={status})")`.

- **[MEDIUM]** `_coerce()` for `data_source` at line 128: `'gemini_rbi'` is mapped to `'gemini_only'` via `_ds_map`. However, `'gemini_rbi'` is a distinct source (Gemini enrichment on RBI input, not the same as Gemini-only NBFC enrichment). Merging them loses provenance information that the audit trail depends on.
  - **Fix:** Either add `'gemini_rbi': 'gemini_rbi'` to `_ds_map` (if the DB allows this value), or document why it's collapsed into `gemini_only`.

- **[MEDIUM]** `rewrite_csv_with_db_ids()` at line 345: if `id` is not in `fieldnames`, it is prepended. However, the function reads the existing CSV with `csv.DictReader`, which already has fieldnames from the header row. If the CSV was written by `run_nbfc_extraction.py` with `id` in position 0 and the rewrite adds a second `id`, the DictWriter will silently use the first `id` column in `POLICY_TABLE_FIELDS` and ignore the duplicate. This is safe but fragile.

- **[MEDIUM]** `validate_rows()` at line 229: `float(aum) < 0` will raise `TypeError` if `aum` is already `None` (which was just coerced at line 119). The `if aum is not None and float(aum) < 0:` check correctly handles None — this is correct.

- **[LOW]** `UPSERT_COLS` at line 54 includes `'last_scraped_at'` but `last_scraped_at` is set to `'NOW()'` literal string in `csv_to_rows()` at line 179. This sentinel is then handled specially in `upsert_lenders()` via the `template` string. The inconsistency between storing `'NOW()'` as a value and then interpreting it as SQL is fragile — a future maintainer might break the `template` logic without realizing the sentinel dependency.

---

## scheduler.py

### Issues

- **[HIGH]** `scheduler.py` reads `SUPABASE_URL` from `NEXT_PUBLIC_SUPABASE_URL` env var (line 62), not from `SUPABASE_URL`. The `.env` file defines both, but other scripts use `SUPABASE_URL`. The scheduler uses a different key name, meaning if `NEXT_PUBLIC_SUPABASE_URL` is not set in a production environment (e.g., a server-side cron context where Next.js env vars are not available), the scheduler will fail to connect with a misleading error.
  - **Fix:** Change line 62 to `SUPABASE_URL = os.getenv('SUPABASE_URL', os.getenv('NEXT_PUBLIC_SUPABASE_URL', '')).rstrip('/')` to accept both.

- **[HIGH]** `re_extract_lender()` at line 427 calls `_Guardrails()` (instantiates a new Guardrails object) on every single lender re-extraction. If Guardrails loads a rules file, database connection, or large configuration at init time, this creates significant overhead per lender and can cause resource exhaustion on large runs. The module-level `_get_shared_resources()` pattern is used for the scraper and rate limiter but not for Guardrails.
  - **Fix:** Add `_guardrails` to the shared globals and initialize it once in `_get_shared_resources()`.

- **[HIGH]** `_load_pipeline()` at line 309 imports from `run_nbfc_extraction`. As established above, `run_nbfc_extraction.py` calls `sys.exit(1)` at module level if its input CSV is missing. The `try/except Exception` block at line 327 will catch the `SystemExit` on Python ≤ 3.7, but on Python 3.8+, `SystemExit` derives from `BaseException`, not `Exception`. The `except Exception` block will NOT catch `SystemExit`, and the scheduler will terminate instead of gracefully falling back.
  - **Fix:** Catch `BaseException` in this specific import block, or — better — fix the root cause by moving `sys.exit()` calls in `run_nbfc_extraction.py` out of module scope.

- **[MEDIUM]** `LockFile.acquire()` at line 160 checks if the existing PID is alive using `os.kill(pid, 0)`. On Windows, `os.kill(pid, 0)` does not raise `ProcessLookupError` for non-existent PIDs — it either succeeds or raises `PermissionError`. The stale lock detection will not work correctly on Windows, meaning multiple scheduler instances can run simultaneously, causing race conditions on the DB.
  - **Fix:** Add a platform check: on Windows, use `psutil.pid_exists(pid)` or check if the lock file is older than the maximum expected run time.

- **[MEDIUM]** `SupabaseClient.update()` at line 238 uses `PATCH` with `?id=eq.{row_id}` on the URL. This is the correct PostgREST pattern. However, the `_retry()` method retries on `ConnectionError` and `Timeout` but NOT on `JSONDecodeError` (malformed response). If Supabase returns a non-JSON response (e.g., a WAF error page), `r.json()` in `_retry`'s inner function will raise `JSONDecodeError`, which is not caught and will propagate as an unhandled exception, crashing the scheduler loop without the retry logic engaging.
  - **Fix:** Catch `requests.exceptions.JSONDecodeError` (or `ValueError`) in `_retry()`, or in the `_do()` lambda.

- **[LOW]** `detect_changes()` at line 280 compares `operating_states` by normalizing JSON strings. If one lender's states are stored as a list `["Maharashtra", "Gujarat"]` and the re-extracted states are `["Gujarat", "Maharashtra"]` (different order), `sorted(str(x) for x in v)` will produce the same sorted list and detect no change — correct behavior. However, `_normalise()` on a non-list, non-JSON string (e.g., a raw comma-separated value) will return the original string, not a list, causing spurious change detection. Verify that `operating_states` is always stored as a JSON array string in the DB.

---

## mca21_enrich.py

### Issues

- **[HIGH]** `_CIN_RE` at line 89 uses the pattern `[A-Z]{3}` for the company class segment (e.g., `PLC`, `PTC`, `LLC`, `GOI`). But MCA CINs also use 2-character classes in some cases (e.g., older CINs). The sync_nbfc_csv.py uses `[A-Z]{2,3}` at line 101, which is more permissive. The mismatch means mca21_enrich.py will miss 2-character company class CINs that sync_nbfc_csv.py correctly captures.
  - **Fix:** Change `[A-Z]{3}` to `[A-Z]{2,3}` in `_CIN_RE`.

- **[HIGH]** Capital adequacy check at line 423: `if result['paid_up_capital_lakhs'] < 200:` — the RBI minimum paid-up capital for NBFCs is ₹200 Lakhs (₹2 Crores) per the NBFC (Non-Deposit Taking and Holding) Companies Prudential Norms. However, this threshold was revised for different NBFC tiers under Scale-Based Regulation (2022):
  - NBFC-Base Layer: ₹200 Lakhs minimum
  - NBFC-Middle Layer: ₹1,000 Lakhs (₹10 Crores)
  - NBFC-Upper Layer: ₹1,000 Lakhs minimum
  The code logs a warning at ₹200L for ALL NBFCs, including Middle/Upper layer NBFCs for which the threshold is ₹1,000L. A Middle Layer NBFC with ₹500L paid-up capital would not trigger a warning even though it's below the applicable RBI minimum.
  - **Fix:** Look up the lender's `regulatory_tier` and apply the appropriate threshold: `threshold = 1000 if lender.get('regulatory_tier') in ('ND-ML', 'ND-UL') else 200`.

- **[MEDIUM]** `_search_zaubacorp()` at line 157 takes the FIRST CIN found in the search results without validating that the company name on that result matches the queried name. For common words (e.g., searching "Capital Finance"), the first result could be an unrelated company with a similar name. The function lacks any name-similarity check before proceeding to the detail page.
  - **Fix:** Before returning `best_cin`, call `_name_similarity(company_name, result_name)` and reject matches below a threshold (e.g., 0.4).

- **[MEDIUM]** `update_lender()` at line 293 always sets `mca21_status = 'enriched'` and `mca21_enriched_at` regardless of whether any useful data was actually extracted. If `_search_zaubacorp()` returns a dict with only `cin` and all other fields as `None`, `update` contains only `{'cin': '...'}`, but `update_lender` adds `mca21_status='enriched'` and `mca21_enriched_at`. The lender is marked enriched even if no compliance-critical data (company_status, capital) was obtained.
  - **Fix:** Only set `mca21_status='enriched'` if at least `company_status` or `paid_up_capital_lakhs` was populated; otherwise use `mca21_status='partial'`.

- **[LOW]** `RATE_DELAY = 3.0` seconds between requests. At this rate, enriching 9,000 NBFCs from the RBI Excel would take `9000 × 3 × 2 (search + detail) = 54,000 seconds ≈ 15 hours`. The `--limit` flag mitigates this for partial runs. No exponential backoff is implemented for 429 responses from zaubacorp.
  - **Fix:** Add a backoff on HTTP 429 responses similar to the Gemini rate limiter pattern used in other scripts.

---

## reset_pilot_checkpoint.py

### Issues

- **[LOW]** `reset_pilot_checkpoint.py` does not include the MCA21 checkpoint (`.mca21_checkpoint.json`) or the policy enrichment checkpoint (`.enrich_policies_checkpoint.json`) in its `CHECKPOINTS` list. A full pilot reset that does not clear these checkpoints will leave stale enrichment state, meaning re-runs after a reset may skip already-enriched lenders.
  - **Fix:** Add both checkpoint files to the `CHECKPOINTS` list.

- **[LOW]** `path.unlink()` at line 39 is called without `missing_ok=True` (available in Python 3.8+). If the file is deleted between the `path.exists()` check and `path.unlink()` (TOCTOU race), this raises `FileNotFoundError`. Although unlikely in a CLI script, it is better practice to use `path.unlink(missing_ok=True)`.

---

*End of review — 14 scripts analyzed.*
