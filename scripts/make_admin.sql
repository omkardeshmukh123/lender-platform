-- Grant admin role to Supabase users by email.
-- Run in Supabase SQL editor: https://supabase.com/dashboard/project/<your-project>/sql

UPDATE auth.users
SET raw_app_meta_data = raw_app_meta_data || '{"role":"admin"}'::jsonb
WHERE email IN (
  'mitram360finance@gmail.com',
  'sriram.aravind9@gmail.com',
  'agnihotri.2209@gmail.com',
  'aravind@gmail.com'
);

-- Verify:
SELECT id, email, raw_app_meta_data->>'role' AS role
FROM auth.users
WHERE email IN (
  'mitram360finance@gmail.com',
  'sriram.aravind9@gmail.com',
  'agnihotri.2209@gmail.com',
  'aravind@gmail.com'
);
