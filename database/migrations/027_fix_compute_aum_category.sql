-- migration 027: fix compute_aum_category() returning 'Unknown' for NULL aum
-- =============================================================================
-- Bug: compute_aum_category(NULL) returned 'Unknown', but the CHECK constraint
-- chk_lenders_aum_category only allows NULL / Micro / Small / Mid / Large.
-- This caused every INSERT with null aum_crores to fail at the trigger level.

CREATE OR REPLACE FUNCTION public.compute_aum_category(aum numeric)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
BEGIN
  IF aum IS NULL THEN
    RETURN NULL;
  ELSIF aum < 500 THEN
    RETURN 'Micro';
  ELSIF aum >= 500 AND aum < 5000 THEN
    RETURN 'Small';
  ELSIF aum >= 5000 AND aum < 50000 THEN
    RETURN 'Mid';
  ELSE
    RETURN 'Large';
  END IF;
END;
$function$;
