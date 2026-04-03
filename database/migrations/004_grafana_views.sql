-- Migration 004: SQL views for Grafana dashboards
-- All views are wrapped in existence checks — safe on a fresh DB.
-- UP

DO $$
DECLARE
    has_lenders  BOOLEAN := EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'lenders');
    has_policies BOOLEAN := EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'policies');
    has_mr       BOOLEAN := EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'matching_requests');
BEGIN

    IF has_policies AND has_lenders THEN
        EXECUTE $v$
            CREATE OR REPLACE VIEW v_interest_by_loan_type AS
            SELECT
                p.loan_type,
                l.company_type,
                COUNT(*)                                        AS policy_count,
                ROUND(AVG(p.interest_rate_min)::numeric, 2)    AS avg_rate_min,
                ROUND(AVG(p.interest_rate_max)::numeric, 2)    AS avg_rate_max,
                ROUND(MIN(p.interest_rate_min)::numeric, 2)    AS best_rate,
                ROUND(MAX(p.interest_rate_max)::numeric, 2)    AS worst_rate
            FROM policies p
            JOIN lenders l ON l.id = p.lender_id
            WHERE p.interest_rate_min IS NOT NULL
            GROUP BY p.loan_type, l.company_type
            ORDER BY avg_rate_min
        $v$;

        EXECUTE $v$
            CREATE OR REPLACE VIEW v_best_by_credit_score AS
            SELECT
                CASE
                    WHEN p.credit_score_min <= 549 THEN '300-549 (Poor)'
                    WHEN p.credit_score_min <= 649 THEN '550-649 (Fair)'
                    WHEN p.credit_score_min <= 699 THEN '650-699 (Average)'
                    WHEN p.credit_score_min <= 749 THEN '700-749 (Good)'
                    WHEN p.credit_score_min <= 799 THEN '750-799 (Very Good)'
                    ELSE                                '800+ (Excellent)'
                END                                 AS credit_bracket,
                p.loan_type,
                l.company_name,
                p.interest_rate_min                 AS best_rate,
                p.loan_amount_max,
                p.credit_score_min
            FROM policies p
            JOIN lenders l ON l.id = p.lender_id
            WHERE p.interest_rate_min IS NOT NULL
            ORDER BY p.credit_score_min, p.interest_rate_min
        $v$;
    END IF;

    IF has_mr THEN
        EXECUTE $v$
            CREATE OR REPLACE VIEW v_match_volume_daily AS
            SELECT
                DATE_TRUNC('day', created_at)::DATE AS day,
                loan_type,
                COUNT(*)                            AS searches,
                ROUND(AVG(credit_score))            AS avg_credit_score,
                ROUND(AVG(loan_amount)::numeric, 1) AS avg_amount_lakhs
            FROM matching_requests
            GROUP BY 1, 2
            ORDER BY 1 DESC
        $v$;
    END IF;

    IF has_lenders THEN
        EXECUTE $v$
            CREATE OR REPLACE VIEW v_lender_state_coverage AS
            SELECT
                company_type,
                hq_state,
                COUNT(*)                                AS lender_count,
                ROUND(AVG(aum_crores)::numeric, 0)      AS avg_aum
            FROM lenders
            WHERE hq_state IS NOT NULL
            GROUP BY company_type, hq_state
            ORDER BY lender_count DESC
        $v$;
    END IF;

END $$;

-- Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (4, 'grafana_views', md5('004_grafana_views'))
ON CONFLICT (version) DO NOTHING;
