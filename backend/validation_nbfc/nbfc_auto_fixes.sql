-- NBFC AUTO-FIX SCRIPT (Confidence >= 90%)
-- ======================================================================
-- Generated: 2026-02-25 16:12:17
-- Current Year: 2026

-- HQ_NOT_IN_STATES: Ceejay Microfin Limited
UPDATE lenders SET operating_states = operating_states || ARRAY['Delhi'] WHERE id = 354;

-- HQ_NOT_IN_STATES: Mitrata Inclusive Financial Services Limited
UPDATE lenders SET operating_states = operating_states || ARRAY['Delhi'] WHERE id = 736;

-- HQ_NOT_IN_STATES: Valar Aditi Social Finance Private Ltd
UPDATE lenders SET operating_states = operating_states || ARRAY['Delhi'] WHERE id = 1151;


-- Total auto-fixable: 3
